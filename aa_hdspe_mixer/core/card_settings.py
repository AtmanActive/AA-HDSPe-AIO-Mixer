"""Card-specific settings: hardware controls other than the mixer matrix, indexed per card model.

Every setting has a stable id (used in memory and preset files), the ALSA control that carries it under
each driver (names and ifaces differ between snd-hdspm and snd-hdspe), and the channels it belongs to,
so the UI can show it on that channel's pill. For an ENUM control the choices are read from the
control at runtime; the tuple here is the expected list, used by the simulated card and as
documentation. For an on/off (BOOLEAN) control, switch=True and the two choices are (off, on).

A linked setting (follows=<id>) has no control of its own: it shows another setting from a different
channel's side. Its second choice means "the followed setting is at follow_value"; its first choice means
"anything else". Choosing it changes the followed setting. Memory and presets only store real settings,
so linked ones come back with them.

Panel settings (panel=True) belong to no channel: they are shown in the card-settings side panel (the
hammer button) as a drop-down. Settings with in_presets=False are kept in memory (and written to the card
at start) but never stored in or recalled from presets: they describe the installation, not a mix.

Settings with the same `group` share one submenu on the pill, one section each (headed by `section`),
and one tag on the pill's edge (the sections' `short` names joined).

To support another card: add its settings to CARD_SETTINGS under its model name (CardInfo.model).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .alsa import IFACE_CARD, IFACE_MIXER
from .channels import IN, OUT


@dataclass(frozen=True)
class Control:
    iface: int
    name: str


@dataclass(frozen=True)
class EnumSetting:
    id: str                                  # stable key in memory/presets, e.g. "spdif_in"
    label: str                               # menu title, e.g. "S/PDIF Input"
    controls: tuple[Control, ...]            # candidates; the first one the card has is used
    channels: tuple[tuple[str, int], ...]    # (kind, hw) channels it belongs to (shown on their pill)
    choices: tuple[str, ...]                 # expected items (the real ones come from the control)
    default: str                             # value of the simulated card
    switch: bool = False                     # BOOLEAN control: choices = (off, on)
    group: str = ""                          # submenu title (default: label)
    section: str = ""                        # heading inside a shared submenu
    short: tuple[str, ...] = ()              # tag text per choice (default: the choice, upper case)
    hints: tuple[str, ...] = ()              # extra menu text per choice ("" = none)
    texts: tuple[str, ...] = ()              # menu text per choice, when it differs from the stored name
    follows: str = ""                        # linked setting: id of the real setting it shows
    follow_value: str = ""                   # the followed value that selects choices[1]
    aliases: tuple[tuple[str, str], ...] = ()  # (driver item name, name shown and stored)
    panel: bool = False                      # shown in the card-settings side panel, not on a pill
    in_presets: bool = True                  # stored in and recalled from presets
    invert: bool = False                     # switch whose control is "on" for choices[0]
    description: str = ""                    # one line shown under the panel drop-down
    # On a group's first setting: makes the group's edge tag from {setting id: value} (default: the
    # sections' short names joined).
    group_tag: Callable[[dict], str] | None = None

    @property
    def linked(self) -> bool:
        return bool(self.follows)

    @property
    def menu_title(self) -> str:
        return self.group or self.label

    def tag(self, value: str | None) -> str:
        if value in self.choices and self.short:
            return self.short[self.choices.index(value)]
        return (value or "?").upper()

    def choice_text(self, value: str) -> str:
        i = self.choices.index(value) if value in self.choices else -1
        text = self.texts[i] if i >= 0 and self.texts else value
        if i >= 0 and self.hints and self.hints[i]:
            return f"{text} ({self.hints[i]})"
        return text

    def belongs_to(self, kind: str, hws) -> bool:
        return any((kind, hw) in self.channels for hw in hws)


# Which connector feeds the S/PDIF input (AIO: optical, coaxial on the breakout cable, internal header).
# snd-hdspm: MIXER 'S/PDIF Input'; snd-hdspe: CARD 'S/PDIF In'. Both: Optical, Coaxial, Internal.
SPDIF_IN = EnumSetting(
    id="spdif_in", label="S/PDIF Input",
    controls=(Control(IFACE_MIXER, "S/PDIF Input"), Control(IFACE_CARD, "S/PDIF In")),
    channels=((IN, 10), (IN, 11)),
    choices=("Optical", "Coaxial", "Internal"), default="Coaxial",
    hints=("ADAT input off unless internal", "", ""))

# S/PDIF output, two on/off switches in both drivers (snd-hdspm: MIXER iface, snd-hdspe: CARD iface).
# Optical: the coaxial output always runs; "on" also COPIES S/PDIF to the optical port, which then no
# longer carries ADAT (AIO manual). Stored names stay "Coaxial" / "Optical" (memory and presets); the menu
# says what really happens, and the tag shows COAX or COAX+OPT.
SPDIF_OUT = EnumSetting(
    id="spdif_out", label="S/PDIF Output Connectors",
    controls=(Control(IFACE_MIXER, "S/PDIF Out Optical"), Control(IFACE_CARD, "S/PDIF Out Optical")),
    channels=((OUT, 10), (OUT, 11)),
    choices=("Coaxial", "Optical"), default="Coaxial", switch=True,
    group="S/PDIF Output", section="Connectors", short=("COAX", "COAX+OPT"),
    texts=("Coaxial only", "Coaxial + Optical"), hints=("", "ADAT output off"))
# Format: off = consumer (S/PDIF), on = professional (AES/EBU channel status). Tag: C or P.
SPDIF_FORMAT = EnumSetting(
    id="spdif_pro", label="S/PDIF Output Format",
    controls=(Control(IFACE_MIXER, "S/PDIF Out Professional"), Control(IFACE_CARD, "S/PDIF Out Professional")),
    channels=((OUT, 10), (OUT, 11)),
    choices=("Consumer", "Professional"), default="Consumer", switch=True,
    group="S/PDIF Output", section="Format", short=("C", "P"))

# The AIO has one optical port per direction, shared by ADAT and S/PDIF: when S/PDIF uses the optical
# port, ADAT is off in that direction. These linked settings show (and switch) it from the ADAT side.
ADAT_CHANNELS = tuple(range(12, 20))       # all ADAT slots; fewer exist at double/quad speed


def _adat_in_tag(v: dict) -> str:
    # [inferred] The internal header frees the optical connector, so ADAT is only off when it uses the
    # optical connector while S/PDIF has it.
    if v.get("adat_in_source") == "Internal":
        return "INTERNAL"
    return "OFF" if v.get("optical_in") == "S/PDIF" else "OPTICAL"


# ADAT input connector: optical, or the internal ADAT In header for an expansion board (AEB4-I, AEB8-I,
# TEB). snd-hdspm: BOOLEAN MIXER 'ADAT internal (AEB/TEB)'; snd-hdspe: BOOLEAN CARD 'ADAT Internal'.
ADAT_IN_SOURCE = EnumSetting(
    id="adat_in_source", label="ADAT Input Connector",
    controls=(Control(IFACE_MIXER, "ADAT internal (AEB/TEB)"), Control(IFACE_CARD, "ADAT Internal")),
    channels=tuple((IN, hw) for hw in ADAT_CHANNELS),
    choices=("Optical", "Internal"), default="Optical", switch=True,
    group="ADAT Input", section="Connector", hints=("", "AEB / TEB header"), group_tag=_adat_in_tag)
OPTICAL_IN = EnumSetting(
    id="optical_in", label="Optical Input", controls=(),
    channels=tuple((IN, hw) for hw in ADAT_CHANNELS),
    choices=("ADAT", "S/PDIF"), default="ADAT", short=("ADAT", "OFF"),
    hints=("", "ADAT input off unless internal"), follows="spdif_in", follow_value="Optical",
    group="ADAT Input", section="Optical Port")
OPTICAL_OUT = EnumSetting(
    id="optical_out", label="Optical Output", controls=(),
    channels=tuple((OUT, hw) for hw in ADAT_CHANNELS),
    choices=("ADAT", "S/PDIF"), default="ADAT", short=("ADAT", "OFF"),
    hints=("", "copy of the coaxial S/PDIF; ADAT output off"), follows="spdif_out", follow_value="Optical")

# Analog input reference level (hardware trim of analog in 1/2), one control for both channels.
# AIO: snd-hdspm MIXER 'Input Level' = Hi Gain, +4 dBu, -10 dBV; snd-hdspe CARD 'Input Level' = Lo Gain,
# +4 dBu, -10 dBV. Same register; RME calls the input setting Lo Gain, so snd-hdspm's name is aliased.
INPUT_LEVEL = EnumSetting(
    id="analog_in_level", label="Analog Input Level",
    controls=(Control(IFACE_MIXER, "Input Level"), Control(IFACE_CARD, "Input Level")),
    channels=((IN, 0), (IN, 1)),
    choices=("Lo Gain", "+4 dBu", "-10 dBV"), default="+4 dBu",
    short=("LO GAIN", "+4 dBu", "-10 dBV"), aliases=(("Hi Gain", "Lo Gain"),))
# AIO Pro (snd-hdspe only): the voltage that gives 0 dBFS.
INPUT_LEVEL_PRO = EnumSetting(
    id="analog_in_level", label="Analog Input Level",
    controls=(Control(IFACE_CARD, "Input Level"),),
    channels=((IN, 0), (IN, 1)),
    choices=("+4 dBu", "+13 dBu", "+19 dBu", "+24 dBu"), default="+4 dBu",
    short=("+4 dBu", "+13 dBu", "+19 dBu", "+24 dBu"))

# Analog output (out 1/2) and phones (out 7/8, hardware 6/7) reference levels. Called "Analog Output
# Level" in the app because "Output Level…" is the app's own per-output level (matrix scaling).
# AIO, both drivers: 'Output Level' / 'Phones Level' = Hi Gain, +4 dBu, -10 dBV (snd-hdspm: MIXER iface,
# snd-hdspe: CARD iface).
_AIO_LEVELS = dict(choices=("Hi Gain", "+4 dBu", "-10 dBV"), default="+4 dBu",
                   short=("HI GAIN", "+4 dBu", "-10 dBV"))
OUTPUT_LEVEL = EnumSetting(
    id="analog_out_level", label="Analog Output Level",
    controls=(Control(IFACE_MIXER, "Output Level"), Control(IFACE_CARD, "Output Level")),
    channels=((OUT, 0), (OUT, 1)), **_AIO_LEVELS)
PHONES_LEVEL = EnumSetting(
    id="phones_level", label="Phones Level",
    controls=(Control(IFACE_MIXER, "Phones Level"), Control(IFACE_CARD, "Phones Level")),
    channels=((OUT, 6), (OUT, 7)), **_AIO_LEVELS)
# AIO Pro (snd-hdspe only): output voltage at 0 dBFS, for the RCA or XLR connection; phones power.
OUTPUT_LEVEL_PRO = EnumSetting(
    id="analog_out_level", label="Analog Output Level",
    controls=(Control(IFACE_CARD, "Output Level"),),
    channels=((OUT, 0), (OUT, 1)),
    choices=("-2 dBu RCA", "+4 dBu RCA", "+13 dBu RCA", "+19 dBu RCA",
             "+4 dBu XLR", "+13 dBu XLR", "+19 dBu XLR", "+24 dBu XLR"), default="+4 dBu XLR",
    short=("-2 dBu RCA", "+4 dBu RCA", "+13 dBu RCA", "+19 dBu RCA",
           "+4 dBu XLR", "+13 dBu XLR", "+19 dBu XLR", "+24 dBu XLR"))
PHONES_LEVEL_PRO = EnumSetting(
    id="phones_level", label="Phones Level",
    controls=(Control(IFACE_CARD, "Phones Level"),),
    channels=((OUT, 6), (OUT, 7)),
    choices=("Lo Power", "Hi Power"), default="Lo Power", short=("LO POWER", "HI POWER"))

# Analog breakout cable type (AIO only; the AIO Pro's output levels name RCA/XLR themselves).
# Both drivers: BOOLEAN 'XLR Breakout Cable' (snd-hdspm MIXER, snd-hdspe CARD); off = RCA, on = XLR.
BREAKOUT_CABLE = EnumSetting(
    id="breakout_cable", label="Analog Breakout Cable",
    controls=(Control(IFACE_MIXER, "XLR Breakout Cable"), Control(IFACE_CARD, "XLR Breakout Cable")),
    channels=(), choices=("RCA", "XLR"), default="RCA", switch=True, panel=True, in_presets=False)

# TMS (Track Marker Support): pass the channel status and track marker data of the S/PDIF and AES inputs
# on to the computer. snd-hdspe only: BOOLEAN CARD 'Clear TMS', which is inverted (on = data cleared =
# TMS off). snd-hdspm has no control for it.
TMS = EnumSetting(
    id="tms", label="TMS (Track Marker Support)",
    controls=(Control(IFACE_CARD, "Clear TMS"),),
    channels=(), choices=("Off", "On"), default="Off", switch=True, invert=True,
    panel=True, in_presets=False,
    description="Passes the channel status and track marker data of the S/PDIF and AES inputs on to "
                "the computer.")

# Word clock output at single speed (44.1/48 kHz) even when the card runs at double or quad speed.
# snd-hdspm: BOOLEAN MIXER 'Single Speed WordClock Out'; snd-hdspe: BOOLEAN CARD 'Single Speed WordClk Out'.
SS_WORDCLOCK = EnumSetting(
    id="ss_wordclock_out", label="Single Speed Word Clock Out",
    controls=(Control(IFACE_MIXER, "Single Speed WordClock Out"),
              Control(IFACE_CARD, "Single Speed WordClk Out")),
    channels=(), choices=("Off", "On"), default="Off", switch=True, panel=True, in_presets=False,
    description="Should the word clock output stay at single speed (44.1/48 kHz) when the card runs at "
                "double (88.2/96 kHz) or quad (176.4/192 kHz) speed?")

# Per card model. The AIO Pro has the same S/PDIF controls under snd-hdspe.
SPDIF_AND_OPTICAL = (SPDIF_IN, SPDIF_OUT, SPDIF_FORMAT, ADAT_IN_SOURCE, OPTICAL_IN, OPTICAL_OUT)
CARD_SETTINGS: dict[str, tuple[EnumSetting, ...]] = {
    "AIO": (INPUT_LEVEL, OUTPUT_LEVEL, PHONES_LEVEL) + SPDIF_AND_OPTICAL + (BREAKOUT_CABLE, TMS, SS_WORDCLOCK),
    "AIO Pro": (INPUT_LEVEL_PRO, OUTPUT_LEVEL_PRO, PHONES_LEVEL_PRO) + SPDIF_AND_OPTICAL + (TMS, SS_WORDCLOCK),
}


def settings_for(model: str) -> tuple[EnumSetting, ...]:
    return CARD_SETTINGS.get(model, ())
