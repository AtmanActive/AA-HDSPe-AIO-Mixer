# AA-HDSPe-AIO-Mixer
Linux desktop application for controlling hardware mixer functions of RME HDSPe AIO (Pro) audio cards

Routing-focused hardware-mixer control for **RME HDSPe AIO** and **AIO Pro** audio cards on Linux (target: Debian 13, KDE Plasma).
It works with the in-kernel **snd-hdspm** driver (AIO, and the AIO Pro since Linux 7.3; single speed) and the out-of-tree **snd-hdspe** driver (AIO and AIO Pro, all speeds).

![The main window in the light theme: sources on top, outputs below, a wire for each connection, and the Headphones output's panel on the right](screenshots/main-light.png)

![The same window in the dark theme](screenshots/main-dark.png)

## Install

Grab the latest installer from [Releases](https://github.com/AtmanActive/AA-HDSPe-AIO-Mixer/releases):

- Debian 13, Ubuntu 24.04 and later: `sudo apt install ./aa-hdspe-aio-mixer_<version>-1_all.deb`
- Fedora: `sudo dnf install ./aa-hdspe-aio-mixer-<version>-1.<fedora>.noarch.rpm`
- CachyOS, Arch Linux: `sudo pacman -U aa-hdspe-aio-mixer-<version>-1-any.pkg.tar.zst`
- Any other x86_64 distribution: the AppImage (`chmod +x`, then run it). It brings its own Python and Qt,
  and has everything except keyboard shortcuts.

## Features

- Routing view: card sources on top, outputs below, one wire per connection, with configurable level in dB and mute state.
- Mono or stereo channels, your own channel names, and peak meters on every channel.
- Per-connection and per-output levels, and mutes on every channel and connection.
- 64 named presets with selective recall; preset files; export everything to one file and import the parts you pick.
- Card settings: analog input and output levels, phones level, S/PDIF and ADAT inputs, TMS, and more.
- Clock: sample rate, clock source with each input's sync status, and sample-rate tuning.
- Test tones on any playback channel.
- Virtual PipeWire input and output devices on chosen channels of the card.
- Remote control by MIDI (with lights and motor-fader feedback), OSC (with feedback) and KDE Plasma global
  keyboard shortcuts.
- System tray, light and dark themes, and zoom from 25 % to 400 %.
- Works with the in-kernel snd-hdspm driver and the out-of-tree snd-hdspe driver; single, double and quad
  speed with snd-hdspe.

## Using it

**On start, the app writes its stored routing to the card.** The hardware mixer is write-only, and its
state after the driver loads differs by driver and distro, so the app's memory is the reference.
The first time it runs, the memory is empty and the card goes silent until you add connections. Don't run
hdspmixer at the same time: both would write the mixer.

On start, the app also installs (or refreshes) `~/.local/share/applications/aa-hdspe-aio-mixer.desktop` and its icon under `~/.local/share/icons/hicolor/`. KWin on Wayland takes the window icon from that file, not from the app, and it also adds a menu launcher. The entry names its icon by full path, so no icon-theme lookup is involved. Use `--no-desktop-entry` to skip this (`--mock` skips it too). If the launcher doesn't list the app after the first start, run `kbuildsycoca6`.

Memory is saved automatically to `~/.config/aa-hdspe-aio-mixer/memory-<model>_<serial>.json`.

The window's size, position and maximised state are remembered app-wide in `~/.config/aa-hdspe-aio-mixer/window.json`
(saved when the window is hidden to the tray and when the app quits). On Wayland the compositor decides where windows
go, so only the size and maximised state come back there; the position is restored on X11. On KDE Plasma (Wayland),
a KWin window rule can remember the position instead: System Settings > Window Management > Window Rules, a rule for
window class `aa-hdspe-aio-mixer` (exact match, normal windows) with Position set to "Remember".

## User manual

See the [User manual](USER_MANUAL.md).

## Layout of the code

- `aa_hdspe_mixer/core/`: no GUI imports.
  - `alsa.py`: ctypes libasound control API.
  - `meters.py`: hwdep peak/RMS ioctl.
  - `card.py`: detection and info.
  - `card_settings.py`: card-specific settings per model (controls, choices, which channel they belong to).
  - `clock.py`: sample rate, clock source, input clock status and tuning, for both drivers.
  - `app_settings.py`: the app's own, app-wide settings (start window, colour mode, zoom).
  - `pwvirtual.py`: virtual PipeWire devices on the card's channels.
  - `alsaseq.py`: ctypes libasound sequencer API (MIDI ports and messages).
  - `midi.py`: MIDI remote control: bindings, mapping, feedback.
  - `osc.py`: OSC remote control: the OSC format, UDP, bindings, mapping, feedback.
  - `shortcuts.py`: keyboard shortcuts: KDE Plasma's global shortcuts over D-Bus, bindings, level steps.
  - `remote.py`: what MIDI, OSC and keyboard shortcuts share: applying bindings to the mixer, feedback, the
    level curve.
  - `channels.py`: channel map per speed.
  - `routing.py`: matrix and the mono/stereo routing view.
  - `memory.py`: names, presets, files.
  - `engine.py`: timers, hardware writes with retry on EBUSY.
  - `backend.py`: real and simulated card.
- `aa_hdspe_mixer/ui/`: Qt application shell (tray, single instance, file dialogs), the QML bridge, and
  the QML views.
- `packaging/`: build scripts for the Debian, Fedora and Arch packages and the AppImage (see Releases).
- `.github/workflows/release.yml`: builds them all and makes a draft release, on demand.

## Development

### Requirements (Debian 13)

```
sudo apt install python3-pyside6.qtcore python3-pyside6.qtgui python3-pyside6.qtwidgets \
  python3-pyside6.qtqml python3-pyside6.qtquick python3-pyside6.qtquickcontrols2 python3-pyside6.qtsvg \
  qml6-module-qtquick qml6-module-qtquick-controls qml6-module-qtquick-layouts \
  qml6-module-qtquick-window qml6-module-qtquick-templates python3-gi
```
`python3-gi` is only for the keyboard shortcuts (KDE Plasma's global shortcuts); without it the rest works.
Tests also need `python3-pytest`, and the UI tests (`tests/test_ui.py`: the real window on a mock card,
driven with simulated clicks and keys, offscreen) need `python3-pyside6.qttest`; without it they are skipped.

### Running

```
python3 -m aa_hdspe_mixer              # first AIO / AIO Pro found
python3 -m aa_hdspe_mixer --hidden     # start in the system tray
python3 -m aa_hdspe_mixer --mock       # simulated card, no hardware needed
python3 -m pytest -q tests
```

### Releases

The version is set in one place, `aa_hdspe_mixer/__init__.py`. To release, raise it there, push, then run
the **Release** workflow by hand (GitHub: Actions → Release → Run workflow). It runs the tests, builds the four
packages and puts them in a draft release tagged `v<version>`; publish the draft on GitHub when it looks
right. Running it again for the same version replaces the draft; once that version is published, the
workflow stops until the version is raised.

The build scripts are in `packaging/` and run the same way on a computer of that distribution; the
packages land in `dist/`:

| Package | Script | Needs |
|---|---|---|
| Debian (`.deb`) | `packaging/debian/build.sh` | `dpkg-deb` |
| Fedora (`.rpm`) | `packaging/fedora/build.sh` | `rpm-build` |
| CachyOS / Arch (`.pkg.tar.zst`) | `packaging/arch/build.sh` | `base-devel` (as a normal user) |
| AppImage | `packaging/appimage/build.sh` | `curl`; downloads Python, PySide6 and appimagetool |

The packages install the app in `/usr/share/aa-hdspe-aio-mixer`, outside Python's site-packages, so they
don't depend on one Python version, with the launcher `/usr/bin/aa-hdspe-aio-mixer`.


## Acknowledgements
This app is based on the excellent open source work of:
- Winfried Ritsch
- Paul Davis
- Marcus Andersson
- Thomas Charbonnel
- Remy Bruno
- Florian Faber
- Adrian Knoth
- Philippe Bekaert
- Takashi Iwai
- Jaroslav Kysela
- Jasmin Fazlic
- Christian Heldt
- Michel Normand
- Jordi Mallach
- Tim Blechmann
- Georg Rudolph
- Diego Pettenò
- Clemens Ladisch
- wynnfeng
- Knight-hamza
- Schroedingers-Cat

## License

MIT License
