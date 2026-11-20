"""Virtual PipeWire devices (core/pwvirtual.py): parsing pw-dump, the generated files, and the manager with
systemctl, pw-dump and pw-link replaced (nothing here touches PipeWire or systemd)."""
import json
import os
import subprocess

import pytest

from aa_hdspe_mixer.core.pwvirtual import (INPUT, OUTPUT, MockVirtualDevices, Target, VirtualDevice,
                                           VirtualDevices, conf_text, link_script, parse_dump, unit_text)

OUT_NODE = "alsa_output.pci-0000_0a_00.0.pro-output-0"
IN_NODE = "alsa_input.pci-0000_0a_00.0.pro-input-0"


def node(i, name, cls, card=None, positions=None, device=0):
    p = {"node.name": name, "media.class": cls}
    if card is not None:
        p.update({"api.alsa.pcm.card": str(card), "api.alsa.pcm.device": str(device)})
    if positions is not None:
        p["audio.position"] = ",".join(positions)
    return {"id": i, "type": "PipeWire:Interface:Node", "info": {"props": p}}


def port(i, node_id, name, direction):
    return {"id": i, "type": "PipeWire:Interface:Port",
            "info": {"direction": direction, "props": {"port.name": name, "node.id": node_id}}}


def link(i, out_port, in_port):
    return {"id": i, "type": "PipeWire:Interface:Link", "info": {"output-port-id": out_port, "input-port-id": in_port}}


def dump():
    aux16 = [f"AUX{i}" for i in range(16)]
    return [
        node(10, OUT_NODE, "Audio/Sink", card=0, positions=aux16),
        node(11, IN_NODE, "Audio/Source", card=0),                     # positions from its ports
        node(12, "alsa_output.other", "Audio/Sink", card=1, positions=["FL", "FR"]),
        node(13, "alsa_output.hdmi", "Audio/Sink", card=0, positions=["FL", "FR"], device=3),
        port(20, 10, "playback_AUX2", "input"),
        port(21, 10, "playback_AUX3", "input"),
        *(port(30 + i, 11, f"capture_AUX{i}", "output") for i in (10, 2, 0, 1)),
        node(40, "aa-hdspe.out-x.hw", "Stream/Output/Audio"),
        port(41, 40, "output_AUX2", "output"),
        port(42, 40, "output_AUX3", "output"),
        link(50, 41, 20),
    ]


def test_parse_dump_finds_the_cards_devices_ports_and_links():
    snap = parse_dump(dump(), 0)
    assert snap.targets[OUTPUT] == Target(OUT_NODE, [f"AUX{i}" for i in range(16)])
    assert snap.targets[INPUT] == Target(IN_NODE, ["AUX0", "AUX1", "AUX2", "AUX10"])    # numeric order
    assert f"{OUT_NODE}:playback_AUX2" in snap.ports and "aa-hdspe.out-x.hw:output_AUX3" in snap.ports
    assert snap.links == {("aa-hdspe.out-x.hw:output_AUX2", f"{OUT_NODE}:playback_AUX2")}
    assert parse_dump(dump(), 1).targets == {OUTPUT: Target("alsa_output.other", ["FL", "FR"])}
    assert parse_dump(dump(), 5).targets == {}


def test_generated_files():
    out = VirtualDevice("out-mon", 'Mon "A"', OUTPUT, OUT_NODE, ["AUX2", "AUX3"])
    c = conf_text(out)
    assert 'node.name = "aa-hdspe.out-mon" node.description = "Mon \\"A\\"" media.class = "Audio/Sink"' in c
    assert 'audio.position = [ "AUX2" "AUX3" ] stream.dont-remix = true' in c
    assert "capture.props" in c and c.index('"Audio/Sink"') < c.index("playback.props")   # apps play into it
    assert out.link_pairs() == [("aa-hdspe.out-mon.hw:output_AUX2", f"{OUT_NODE}:playback_AUX2"),
                                ("aa-hdspe.out-mon.hw:output_AUX3", f"{OUT_NODE}:playback_AUX3")]
    inp = VirtualDevice("in-mic", "Mic", INPUT, IN_NODE, ["AUX0", "AUX1"])
    c = conf_text(inp)
    assert c.index("capture.props") < c.index("stream.dont-remix") < c.index('"Audio/Source"')
    assert inp.link_pairs()[0] == (f"{IN_NODE}:capture_AUX0", "aa-hdspe.in-mic.hw:input_AUX0")

    script = link_script(out)
    assert subprocess.run(["sh", "-n"], input=script, text=True).returncode == 0    # valid shell
    assert f"pw-link aa-hdspe.out-mon.hw:output_AUX2 {OUT_NODE}:playback_AUX2" in script
    unit = unit_text(out, "/home/a b/x.conf", "/home/a b/x.link.sh", "/usr/bin/pipewire")
    assert 'ExecStart=/usr/bin/pipewire -c "/home/a b/x.conf"' in unit
    assert 'ExecStartPost=-/bin/sh "/home/a b/x.link.sh"' in unit
    assert "BindsTo=pipewire.service" in unit and "WantedBy=pipewire.service" in unit


class Fake(VirtualDevices):
    """The real manager on temporary folders, with the system calls recorded and answered."""

    def __init__(self, tmp_path):
        super().__init__(root=str(tmp_path / "virtual"), unit_dir=str(tmp_path / "units"))
        self.calls, self.linked, self.unit_state = [], [], "active"
        self.objs = dump()

    def _systemctl(self, *args):
        self.calls.append(args)
        if args[0] == "is-active":
            return 3, "\n".join(self.unit_state for _ in args[1:]), ""
        return 0, "", ""

    def _dump(self):
        return self.objs

    def _link(self, a, b):
        self.linked.append((a, b))
        return True


def test_create_start_and_remove(tmp_path):
    m = Fake(tmp_path)
    snap = m.snapshot(0)
    dev = m.create("  Monitors ", OUTPUT, snap.targets[OUTPUT], ["AUX2", "AUX3"])
    assert (dev.id, dev.name, dev.target) == ("out-monitors", "Monitors", OUT_NODE)
    files = sorted(os.listdir(tmp_path / "virtual")) + os.listdir(tmp_path / "units")
    assert files == ["out-monitors.conf", "out-monitors.json", "out-monitors.link.sh",
                     "aa-hdspe-virtual-out-monitors.service"]
    assert json.load(open(tmp_path / "virtual" / "out-monitors.json"))["channels"] == ["AUX2", "AUX3"]
    assert m.start(dev) == ""
    assert m.calls == [("daemon-reload",), ("enable", "--now", "--no-block", dev.unit)]

    # same name: another output can't, an input can; ids stay unique
    with pytest.raises(ValueError, match="already"):
        m.create("monitors", OUTPUT, snap.targets[OUTPUT], ["AUX4", "AUX5"])
    m.create("Monitors", INPUT, snap.targets[INPUT], ["AUX0", "AUX1"])
    m.create("Monitors!", OUTPUT, snap.targets[OUTPUT], ["AUX4", "AUX5"])
    assert {d.id for d in m.devices()} == {"out-monitors", "in-monitors", "out-monitors-2"}

    m.calls.clear()
    assert m.remove("out-monitors") == ""
    assert m.calls == [("disable", "--now", dev.unit), ("daemon-reload",)]
    assert not any(n.startswith("out-monitors.") for n in os.listdir(tmp_path / "virtual"))
    assert not (tmp_path / "units" / dev.unit).exists()


def test_validation(tmp_path):
    m = Fake(tmp_path)
    t = m.snapshot(0).targets[OUTPUT]
    assert m.validate("", OUTPUT, t, ["AUX0", "AUX1"]) == "Give it a name."
    assert "no PipeWire output" in m.validate("A", OUTPUT, None, ["AUX0", "AUX1"])
    assert "different" in m.validate("A", OUTPUT, t, ["AUX0", "AUX0"])
    assert "Choose" in m.validate("A", OUTPUT, t, ["AUX0", "AUX99"])
    assert m.validate("A", OUTPUT, t, ["AUX1", "AUX0"]) == ""


def test_sorted_inputs_first_then_by_name(tmp_path):
    m = MockVirtualDevices()
    for name, d in (("b out", OUTPUT), ("Zed in", INPUT), ("a out", OUTPUT), ("alpha in", INPUT)):
        m.create(name, d, m.out_target if d == OUTPUT else m.in_target, ["AUX0", "AUX1"])
    assert [d.name for d in m.devices()] == ["alpha in", "Zed in", "a out", "b out"]


def test_status_and_link_repair(tmp_path):
    m = Fake(tmp_path)
    snap = m.snapshot(0)
    dev = m.create("x", OUTPUT, snap.targets[OUTPUT], ["AUX2", "AUX3"])     # id out-x: its node is in dump()
    _snap, st = m.status(0)
    assert st[dev.id] == ("ok", "Running.")
    assert m.linked == [("aa-hdspe.out-x.hw:output_AUX3", f"{OUT_NODE}:playback_AUX3")]   # only the missing one

    m.linked.clear()
    assert m.status(0, repair=False)[1][dev.id][0] == "error" and not m.linked

    m.unit_state = "failed"
    assert m.status(0)[1][dev.id] == ("error", "Not running (failed).")
    m.unit_state = "active"
    m.objs = [o for o in dump() if o["id"] not in (40, 41, 42, 50)]            # its own node not up yet
    assert m.status(0)[1][dev.id][0] == "starting"
    m.objs = [o for o in dump() if o["id"] not in (10, 20, 21)]                # the card's device gone
    assert m.status(0)[1][dev.id][0] == "warning"
