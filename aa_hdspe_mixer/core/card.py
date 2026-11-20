"""Find the AIO / AIO Pro, tell which driver drives it, and gather the info-bar facts."""
from __future__ import annotations

import os
import platform
import re
from dataclasses import dataclass, field

from .alsa import IFACE_CARD, IFACE_MIXER, AlsaError, Ctl, ElemId

SUPPORTED_DRIVERS = ("snd_hdspm", "snd_hdspe")


@dataclass
class CardInfo:
    index: int
    driver: str                 # "snd_hdspm" | "snd_hdspe" | "mock"
    driver_version: str
    model: str                  # "AIO" | "AIO Pro"
    name: str                   # e.g. "RME AIO Pro"
    serial: str
    pci_revision: int | None
    firmware_build: int | None
    elements: set = field(default_factory=set)   # {(iface, name)} present on this card

    @property
    def driver_label(self) -> str:
        return self.driver.replace("_", "-")

    @property
    def card_version(self) -> str:
        parts = []
        if self.pci_revision is not None:
            parts.append(f"rev {self.pci_revision}")
        if self.firmware_build is not None:
            parts.append(f"firmware build {self.firmware_build}")
        return ", ".join(parts) or "unknown"

    @property
    def key(self) -> str:
        """Stable identity used to keep per-card memory apart."""
        return f"{self.model}:{self.serial}"


def _read(path: str) -> str | None:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def driver_of(card: int) -> str | None:
    link = f"/sys/class/sound/card{card}/device/driver/module"
    try:
        return os.path.basename(os.readlink(link))
    except OSError:
        return None


def driver_version(driver: str) -> str:
    v = _read(f"/sys/module/{driver}/version")
    if v:
        return v
    # 'O' in taint = out-of-tree module (snd-hdspe develop has no MODULE_VERSION; it logs it to dmesg only).
    if "O" in (_read(f"/sys/module/{driver}/taint") or ""):
        return "out-of-tree, version unknown"
    return f"in-kernel, Linux {platform.release()}"


# PCI vendor of the card's FPGA: the AIO Pro is RME's own ID, the AIO (any firmware) Xilinx's. Both have
# device 3fc6 and revision 0xd4, and the firmware build doesn't tell them apart (seen on both cards).
PCI_VENDOR_RME = 0x1d18          # AIO Pro
PCI_VENDOR_XILINX = 0x10ee       # AIO


def model_of(name: str, pci_vendor: int | None) -> str:
    """'AIO Pro' or 'AIO'. snd-hdspm names an AIO Pro plain "RME AIO" (Linux 7.3+), so the PCI vendor
    decides when it is known; the driver's card name is the fallback."""
    if pci_vendor == PCI_VENDOR_RME:
        return "AIO Pro"
    if pci_vendor == PCI_VENDOR_XILINX:
        return "AIO"
    return "AIO Pro" if "AIO Pro" in name else "AIO"


def candidate_cards() -> list[int]:
    out = []
    for name in sorted(os.listdir("/sys/class/sound")) if os.path.isdir("/sys/class/sound") else []:
        m = re.fullmatch(r"card(\d+)", name)
        if m and driver_of(int(m.group(1))) in SUPPORTED_DRIVERS:
            out.append(int(m.group(1)))
    return out


def probe(card: int) -> CardInfo:
    drv = driver_of(card) or "unknown"
    with Ctl(card) as ctl:
        ci = ctl.card_info()
        elements = {(e.iface, e.name) for e in ctl.list_elements()}
        fw = None
        if (IFACE_CARD, "Firmware Build") in elements:
            fw = ctl.read(ElemId(IFACE_CARD, "Firmware Build"))[0]
        serial = None
        if (IFACE_CARD, "Serial") in elements:
            serial = str(ctl.read(ElemId(IFACE_CARD, "Serial"))[0])
    longname = ci["longname"]
    # snd-hdspe: "RME AIO Pro S/N 24011701 ..."; snd-hdspm: "RME AIO S/N 0x8d9191 ..."
    m = re.match(r"(RME .+?) S/N (\S+)", longname)
    name = m.group(1) if m else ci["name"]
    if serial is None:
        serial = m.group(2).rstrip(",") if m else ci["id"]
    vendor = _read(f"/sys/class/sound/card{card}/device/vendor")
    model = model_of(name, int(vendor, 16) if vendor else None)
    if model == "AIO Pro" and "AIO Pro" not in name:
        name = name.replace("AIO", "AIO Pro", 1)        # snd-hdspm calls it "RME AIO"
    rev = _read(f"/sys/class/sound/card{card}/device/revision")
    return CardInfo(index=card, driver=drv, driver_version=driver_version(drv), model=model, name=name,
                    serial=serial, pci_revision=int(rev, 16) if rev else None, firmware_build=fw,
                    elements=elements)


def find_card(preferred: int | None = None) -> CardInfo | None:
    cards = candidate_cards()
    if preferred is not None:
        cards = [c for c in cards if c == preferred]
    for c in cards:
        try:
            info = probe(c)
        except AlsaError:
            continue
        if "AIO" in info.name:
            return info
    return None


def read_sample_rate(info: CardInfo, ctl: Ctl) -> int | None:
    """System sample rate in Hz, from whatever the driver offers."""
    if (IFACE_MIXER, "System Sample Rate") in info.elements:          # snd-hdspm
        return ctl.read(ElemId(IFACE_MIXER, "System Sample Rate"))[0]
    txt = _read(f"/proc/asound/card{info.index}/hdspe")                 # snd-hdspe
    if txt:
        m = re.search(r"System sample rate\s*:\s*(\d+)", txt)
        if m:
            return int(m.group(1))
    return None
