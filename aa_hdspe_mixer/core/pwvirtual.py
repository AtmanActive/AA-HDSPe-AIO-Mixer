"""Virtual PipeWire devices on chosen channels of the card: a stereo output (an Audio/Sink that desktop apps play
into, fed to two playback channels of the card) or a stereo input (an Audio/Source that apps record from, fed
from two capture channels). On the Pro Audio profile, which the AIO Pro needs, the card shows up as one
16/18-channel device that ordinary apps can't use; these give them plain stereo devices instead.

How one works (the same method as PipeWireController, written anew here):
- A small PipeWire process of its own (`pipewire -c <id>.conf`) loads module-loopback. The loopback's virtual
  side carries [FL FR]; its hardware side declares the card channels it uses (e.g. [AUX2 AUX3]) with
  stream.dont-remix, so it uses exactly those channels and never a downmix of all of them.
- WirePlumber doesn't route streams onto a Pro Audio node by itself, so the hardware side has
  node.autoconnect = false and is linked with pw-link, port by port, once both ends exist. A shell script
  next to the config does that when the process starts (waiting for the card's ports, as at boot), and the
  app puts back any links that went missing while it runs (the status check in VirtualDevices.status()).
- A systemd user unit per device runs the process. It's enabled with pipewire.service, so the device stays
  after the app quits, comes back at login, and restarts with PipeWire. Creating or removing one leaves the
  rest of the audio alone: nothing restarts PipeWire.

Files: <config>/aa-hdspe-aio-mixer/virtual/<id>.json (what the app knows about the device), <id>.conf and
<id>.link.sh (generated), and <config>/systemd/user/aa-hdspe-virtual-<id>.service.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import asdict, dataclass, field

from .memory import atomic_write_json, config_dir

OUTPUT, INPUT = "output", "input"
DIRECTIONS = (INPUT, OUTPUT)                # the panel's order: inputs first
VIRTUAL_POSITIONS = ("FL", "FR")
UNIT_PREFIX = "aa-hdspe-virtual-"
NODE_PREFIX = "aa-hdspe."
LINK_WAIT_S = 60                            # the link script waits this long for the card's ports


def _xdg_config() -> str:
    return os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")


@dataclass
class VirtualDevice:
    id: str
    name: str                               # what desktop apps show (node.description)
    direction: str                          # OUTPUT or INPUT
    target: str                             # node.name of the card's PipeWire device
    channels: list[str] = field(default_factory=list)   # its channels used for left and right, e.g. AUX2, AUX3

    @property
    def node_name(self) -> str:             # the device apps see
        return NODE_PREFIX + self.id

    @property
    def hw_node(self) -> str:               # the loopback side that connects to the card
        return self.node_name + ".hw"

    @property
    def unit(self) -> str:
        return f"{UNIT_PREFIX}{self.id}.service"

    def link_pairs(self) -> list[tuple[str, str]]:
        """(output port, input port) for each channel, as pw-link names them: node:port."""
        if self.direction == OUTPUT:
            return [(f"{self.hw_node}:output_{c}", f"{self.target}:playback_{c}") for c in self.channels]
        return [(f"{self.target}:capture_{c}", f"{self.hw_node}:input_{c}") for c in self.channels]

    def sort_key(self):
        return (DIRECTIONS.index(self.direction), self.name.casefold(), self.id)


@dataclass
class Target:
    """The card's PipeWire device for one direction: node name and channel names in order."""
    node: str
    positions: list[str]


@dataclass
class Snapshot:
    """What PipeWire has now (from one pw-dump)."""
    nodes: set[str] = field(default_factory=set)
    ports: set[str] = field(default_factory=set)                 # "node:port"
    links: set[tuple[str, str]] = field(default_factory=set)     # ("node:port" out, "node:port" in)
    targets: dict[str, Target] = field(default_factory=dict)     # direction -> the card's device


def _positions(props: dict) -> list[str]:
    pos = props.get("audio.position")
    if isinstance(pos, str):
        pos = [s.strip() for s in pos.strip("[] ").replace(",", " ").split() if s.strip()]
    return [str(p) for p in pos or []]


def _aux_sorted(chans: list[str]) -> list[str]:
    if chans and all(re.fullmatch(r"AUX\d+", c) for c in chans):
        return sorted(chans, key=lambda c: int(c[3:]))
    return chans


def parse_dump(objs: list, card: int) -> Snapshot:
    """Nodes, ports and links from pw-dump output, and the ALSA card's playback and capture devices (PCM
    device 0: on Pro Audio "…pro-output-0" / "…pro-input-0", on other profiles e.g. "…multichannel-output")."""
    snap = Snapshot()
    node_names: dict[int, str] = {}
    port_names: dict[int, str] = {}
    port_node: dict[int, int] = {}
    node_ports: dict[int, list[tuple[str, str]]] = {}      # node id -> [(direction, port name)]
    for o in objs:
        info = o.get("info") or {}
        p = info.get("props") or {}
        if o.get("type") == "PipeWire:Interface:Node" and p.get("node.name"):
            node_names[o["id"]] = p["node.name"]
        elif o.get("type") == "PipeWire:Interface:Port" and p.get("port.name") is not None:
            port_names[o["id"]] = p["port.name"]
            port_node[o["id"]] = int(p.get("node.id", -1))
            node_ports.setdefault(int(p.get("node.id", -1)), []).append((info.get("direction", ""), p["port.name"]))
    snap.nodes = set(node_names.values())
    for pid, name in port_names.items():
        if port_node[pid] in node_names:
            snap.ports.add(f"{node_names[port_node[pid]]}:{name}")
    for o in objs:
        if o.get("type") != "PipeWire:Interface:Link":
            continue
        info = o.get("info") or {}
        out_p, in_p = info.get("output-port-id"), info.get("input-port-id")
        if out_p in port_names and in_p in port_names and port_node[out_p] in node_names \
                and port_node[in_p] in node_names:
            snap.links.add((f"{node_names[port_node[out_p]]}:{port_names[out_p]}",
                            f"{node_names[port_node[in_p]]}:{port_names[in_p]}"))
    for o in objs:
        if o.get("type") != "PipeWire:Interface:Node":
            continue
        p = (o.get("info") or {}).get("props") or {}
        if str(p.get("api.alsa.pcm.card", p.get("alsa.card", ""))) != str(card):
            continue
        if str(p.get("api.alsa.pcm.device", p.get("alsa.device", "0"))) != "0":
            continue
        direction = {"Audio/Sink": OUTPUT, "Audio/Source": INPUT}.get(p.get("media.class"))
        if not direction or direction in snap.targets:
            continue
        pos = _positions(p)
        if not pos:                         # from the ports: playback_AUX0… / capture_AUX0…
            prefix = "playback_" if direction == OUTPUT else "capture_"
            pos = _aux_sorted([n[len(prefix):] for _d, n in node_ports.get(o["id"], []) if n.startswith(prefix)])
        if pos and p.get("node.name"):
            snap.targets[direction] = Target(p["node.name"], pos)
    return snap


# ---------------------------------------------------------------------------------------- generated files
def _spa(v) -> str:
    """A value in PipeWire's config syntax (SPA JSON); strings always quoted."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (list, tuple)):
        return "[ " + " ".join(_spa(x) for x in v) + " ]"
    if isinstance(v, dict):
        return "{ " + " ".join(f"{k} = {_spa(x)}" for k, x in v.items()) + " }"
    return json.dumps(str(v), ensure_ascii=False)


def conf_text(dev: VirtualDevice) -> str:
    virtual = {"node.name": dev.node_name, "node.description": dev.name,
               "media.class": "Audio/Sink" if dev.direction == OUTPUT else "Audio/Source",
               "audio.position": list(VIRTUAL_POSITIONS)}
    if dev.direction == INPUT:
        virtual["node.autoconnect"] = False     # a source: apps link to it; it links to nothing itself
    hw = {"node.name": dev.hw_node,
          "node.description": f"{dev.name} ({'to' if dev.direction == OUTPUT else 'from'} the card)",
          "audio.position": list(dev.channels),
          "stream.dont-remix": True,            # exactly these channels, not a mix of all of the card's
          "node.passive": True,
          "node.autoconnect": False}            # linked by the link script (Pro Audio isn't auto-routed)
    capture, playback = (virtual, hw) if dev.direction == OUTPUT else (hw, virtual)
    modules = [
        {"name": "libpipewire-module-rt", "args": {}, "flags": ["ifexists", "nofail"]},
        {"name": "libpipewire-module-protocol-native"},
        {"name": "libpipewire-module-client-node"},
        {"name": "libpipewire-module-adapter"},
        {"name": "libpipewire-module-loopback",
         "args": {"node.description": dev.name, "capture.props": capture, "playback.props": playback}},
    ]
    lines = [
        f"# {dev.name}: virtual {dev.direction} of AA-HDSPe-AIO-Mixer on {dev.target} {' '.join(dev.channels)}.",
        "# Generated by the app; remove it from the app's PipeWire panel rather than editing it.",
        "context.properties = { log.level = 2 }",
        "context.spa-libs = {",
        '    audio.convert.* = "audioconvert/libspa-audioconvert"',
        '    support.* = "support/libspa-support"',
        "}",
        "context.modules = [",
        *("    " + _spa(m) for m in modules),
        "]",
    ]
    return "\n".join(lines) + "\n"


def link_script(dev: VirtualDevice) -> str:
    pairs = dev.link_pairs()
    need = " ".join(shlex.quote(p) for pair in pairs for p in pair)
    links = "\n".join(f"        pw-link {shlex.quote(a)} {shlex.quote(b)} 2>/dev/null" for a, b in pairs)
    tries = LINK_WAIT_S * 4
    return f"""#!/bin/sh
# Links "{dev.name}" (AA-HDSPe-AIO-Mixer) to the card's channels once both ends exist: the card's ports
# may come later than this process, as at login. Generated by the app.
n=0
while [ $n -lt {tries} ]; do
    ports=$( (pw-link -o; pw-link -i) 2>/dev/null)
    ok=1
    for p in {need}; do
        printf '%s\\n' "$ports" | grep -qxF "$p" || {{ ok=0; break; }}
    done
    if [ $ok = 1 ]; then
{links}
        exit 0
    fi
    n=$((n + 1))
    sleep 0.25
done
exit 0
"""


def unit_text(dev: VirtualDevice, conf: str, script: str, pipewire: str) -> str:
    q = lambda s: '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"' if re.search(r'[\s"\\]', s) else s
    return f"""# Generated by AA-HDSPe-AIO-Mixer: virtual {dev.direction} "{dev.name}". Remove it from the app.
[Unit]
Description=AA-HDSPe-AIO-Mixer virtual {dev.direction}: {dev.name.replace('%', '%%')}
After=pipewire.service wireplumber.service
BindsTo=pipewire.service

[Service]
Type=simple
ExecStart={q(pipewire)} -c {q(conf)}
ExecStartPost=-/bin/sh {q(script)}
Restart=on-failure
RestartSec=2

[Install]
WantedBy=pipewire.service
"""


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")[:40].strip("-") or "device"


# ---------------------------------------------------------------------------------------- the manager
class VirtualDevices:
    """The app's virtual devices: create, list, remove, and their state in PipeWire. Storage, systemctl,
    pw-dump and pw-link go through small methods that the simulated card's version replaces."""

    def __init__(self, root: str | None = None, unit_dir: str | None = None):
        self.root = root or os.path.join(config_dir(), "virtual")
        self.unit_dir = unit_dir or os.path.join(_xdg_config(), "systemd", "user")

    # ---- storage
    def devices(self) -> list[VirtualDevice]:
        """All of them, inputs first, then outputs, each by name."""
        return sorted(self._load_all(), key=VirtualDevice.sort_key)

    def get(self, dev_id: str) -> VirtualDevice | None:
        return next((d for d in self._load_all() if d.id == dev_id), None)

    def _paths(self, dev: VirtualDevice) -> dict[str, str]:
        return {"meta": os.path.join(self.root, dev.id + ".json"),
                "conf": os.path.join(self.root, dev.id + ".conf"),
                "script": os.path.join(self.root, dev.id + ".link.sh"),
                "unit": os.path.join(self.unit_dir, dev.unit)}

    def _load_all(self) -> list[VirtualDevice]:
        out = []
        try:
            names = sorted(os.listdir(self.root))
        except OSError:
            return out
        for n in names:
            if not n.endswith(".json"):
                continue
            try:
                with open(os.path.join(self.root, n)) as f:
                    d = json.load(f)
                dev = VirtualDevice(id=str(d["id"]), name=str(d["name"]), direction=str(d["direction"]),
                                    target=str(d["target"]), channels=[str(c) for c in d["channels"]])
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if dev.direction in DIRECTIONS and len(dev.channels) == 2:
                out.append(dev)
        return out

    def _store(self, dev: VirtualDevice):
        p = self._paths(dev)
        os.makedirs(self.root, exist_ok=True)
        os.makedirs(self.unit_dir, exist_ok=True)
        for key, text in (("conf", conf_text(dev)), ("script", link_script(dev)),
                          ("unit", unit_text(dev, p["conf"], p["script"], shutil.which("pipewire") or "/usr/bin/pipewire"))):
            with open(p[key], "w") as f:
                f.write(text)
        atomic_write_json(p["meta"], asdict(dev))

    def _erase(self, dev: VirtualDevice):
        for path in self._paths(dev).values():
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    # ---- system
    def _run(self, *cmd: str, timeout: float = 15) -> tuple[int, str, str]:
        try:
            r = subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout)
            return r.returncode, r.stdout, r.stderr
        except (OSError, subprocess.SubprocessError) as e:
            return 1, "", str(e)

    def _systemctl(self, *args: str) -> tuple[int, str, str]:
        return self._run("systemctl", "--user", *args)

    def _dump(self) -> list:
        rc, out, _err = self._run("pw-dump", timeout=5)
        try:
            return json.loads(out) if rc == 0 and out else []
        except ValueError:
            return []

    def _link(self, out_port: str, in_port: str) -> bool:
        return self._run("pw-link", out_port, in_port, timeout=5)[0] == 0

    def available(self) -> str:
        """"" when PipeWire and systemd's user manager are there to run virtual devices, else why not."""
        for tool in ("pipewire", "pw-dump", "pw-link", "systemctl"):
            if not shutil.which(tool):
                return f"{tool} isn't installed."
        return ""

    # ---- create and remove
    def validate(self, name: str, direction: str, target: Target | None, channels: list[str]) -> str:
        """"" if a device can be made from this, else what's wrong with it."""
        name = name.strip()
        if direction not in DIRECTIONS:
            return f"Not a direction: {direction}."
        if not name:
            return "Give it a name."
        if any(d.direction == direction and d.name.casefold() == name.casefold() for d in self._load_all()):
            return f'There is already a virtual {direction} called "{name}".'
        if target is None:
            return f"The card has no PipeWire {direction} device (is it on the Pro Audio profile?)."
        if len(channels) != 2 or any(c not in target.positions for c in channels):
            return "Choose a channel for left and right."
        if channels[0] == channels[1]:
            return "Left and right need different channels."
        return ""

    def create(self, name: str, direction: str, target: Target | None, channels: list[str]) -> VirtualDevice:
        """Write its files. Raises ValueError with the reason when it can't be made. start() runs it."""
        err = self.validate(name, direction, target, channels)
        if err:
            raise ValueError(err)
        taken = {d.id for d in self._load_all()}
        base = ("out-" if direction == OUTPUT else "in-") + slug(name)
        dev_id, n = base, 2
        while dev_id in taken:
            dev_id, n = f"{base}-{n}", n + 1
        dev = VirtualDevice(dev_id, name.strip(), direction, target.node, list(channels))
        self._store(dev)
        return dev

    def start(self, dev: VirtualDevice) -> str:
        """Enable and start its unit (without waiting for it). "" or the error."""
        rc, _out, err = self._systemctl("daemon-reload")
        if rc == 0:
            rc, _out, err = self._systemctl("enable", "--now", "--no-block", dev.unit)
        return "" if rc == 0 else (err.strip() or f"systemctl couldn't start {dev.unit}")

    def remove(self, dev_id: str) -> str:
        """Stop and disable its unit and delete its files. "" or the error (the files go regardless)."""
        dev = self.get(dev_id)
        if dev is None:
            return ""
        rc, _out, err = self._systemctl("disable", "--now", dev.unit)
        self._erase(dev)
        self._systemctl("daemon-reload")
        return "" if rc == 0 else (err.strip() or f"systemctl couldn't stop {dev.unit}")

    # ---- state
    def snapshot(self, card: int) -> Snapshot:
        return parse_dump(self._dump(), card)

    def unit_states(self, devs: list[VirtualDevice]) -> dict[str, str]:
        if not devs:
            return {}
        _rc, out, _err = self._systemctl("is-active", *(d.unit for d in devs))
        states = out.split()
        return {d.id: (states[i] if i < len(states) else "unknown") for i, d in enumerate(devs)}

    def status(self, card: int, repair: bool = True) -> tuple[Snapshot, dict[str, tuple[str, str]]]:
        """(snapshot, {device id: (state, text)}), state "ok", "starting", "warning" or "error". With
        repair, puts back the links of a running device whose card ports are there but not linked."""
        devs = self._load_all()
        snap = self.snapshot(card)
        units = self.unit_states(devs)
        out = {}
        for d in devs:
            unit = units.get(d.id, "unknown")
            pairs = d.link_pairs()
            card_ports = {p for pair in pairs for p in pair if not p.startswith(d.hw_node + ":")}
            own_ports = {p for pair in pairs for p in pair if p.startswith(d.hw_node + ":")}
            if unit not in ("active", "activating", "reloading"):
                out[d.id] = ("error", f"Not running ({unit}).")
            elif d.target not in snap.nodes or not card_ports <= snap.ports:
                out[d.id] = ("warning", "The card's channels aren't in PipeWire now (is the card on the same "
                                        "profile as when this was made?).")
            elif not own_ports <= snap.ports:
                out[d.id] = ("starting", "Starting…")
            else:
                missing = [pr for pr in pairs if pr not in snap.links]
                if missing and repair:
                    missing = [pr for pr in missing if not self._link(*pr)]
                out[d.id] = ("error", "Couldn't link it to the card's channels.") if missing \
                    else ("ok", "Running.")
        return snap, out


class MockVirtualDevices(VirtualDevices):
    """For the simulated card: kept in memory, "runs" at once, and the card is a Pro Audio device with 16
    playback and 18 capture channels."""

    def __init__(self, playback: int = 16, capture: int = 18):
        super().__init__(root="/nonexistent", unit_dir="/nonexistent")
        self._devs: dict[str, VirtualDevice] = {}
        self.out_target = Target("alsa_output.mock.pro-output-0", [f"AUX{i}" for i in range(playback)])
        self.in_target = Target("alsa_input.mock.pro-input-0", [f"AUX{i}" for i in range(capture)])
        self.commands: list[tuple[str, ...]] = []

    def available(self):
        return ""

    def _load_all(self):
        return list(self._devs.values())

    def _store(self, dev):
        self._devs[dev.id] = dev

    def _erase(self, dev):
        self._devs.pop(dev.id, None)

    def _systemctl(self, *args):
        self.commands.append(("systemctl", *args))
        if args[0] == "is-active":
            return 0, "\n".join("active" for _ in args[1:]) + "\n", ""
        return 0, "", ""

    def _link(self, out_port, in_port):
        return True

    def snapshot(self, card):
        snap = Snapshot(targets={OUTPUT: self.out_target, INPUT: self.in_target})
        for t, prefix in ((self.out_target, "playback_"), (self.in_target, "capture_")):
            snap.nodes.add(t.node)
            snap.ports |= {f"{t.node}:{prefix}{c}" for c in t.positions}
        for d in self._devs.values():
            snap.nodes |= {d.node_name, d.hw_node}
            pairs = d.link_pairs()
            snap.ports |= {p for pair in pairs for p in pair}
            snap.links |= set(pairs)
        return snap
