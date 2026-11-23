"""The user-local desktop entry: from the source, beside a package's entry, and from an AppImage."""
import os

import pytest

from aa_hdspe_mixer import APP_ID
from aa_hdspe_mixer.ui import desktop_entry


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    home, system = tmp_path / "home", tmp_path / "system"
    monkeypatch.setenv("XDG_DATA_HOME", str(home))
    monkeypatch.setenv("XDG_DATA_DIRS", str(system))
    monkeypatch.delenv("APPIMAGE", raising=False)
    refreshed = []
    monkeypatch.setattr(desktop_entry, "_refresh_menu", lambda: refreshed.append(1))   # not the real menu
    icon = tmp_path / "icon.svg"
    icon.write_text("<svg/>")
    entry = home / "applications" / f"{APP_ID}.desktop"
    return {"home": home, "system": system, "icon": str(icon), "entry": entry, "refreshed": refreshed}


def _install_package_entry(system):
    os.makedirs(system / "applications")
    (system / "applications" / f"{APP_ID}.desktop").write_text("[Desktop Entry]\nExec=aa-hdspe-aio-mixer\n")


def test_from_the_source_writes_the_entry_once(dirs):
    assert desktop_entry.ensure_desktop_entry(dirs["icon"])
    text = dirs["entry"].read_text()
    assert "Exec=env PYTHONPATH=" in text and "-m aa_hdspe_mixer" in text
    assert not desktop_entry.ensure_desktop_entry(dirs["icon"])          # unchanged: nothing written
    assert dirs["refreshed"] == [1]


def test_a_package_entry_replaces_the_one_written_from_the_source(dirs):
    desktop_entry.ensure_desktop_entry(dirs["icon"])
    _install_package_entry(dirs["system"])
    assert desktop_entry.ensure_desktop_entry(dirs["icon"])              # ours removed
    assert not dirs["entry"].exists()
    assert not os.path.exists(desktop_entry._icon_path())                # its icon too
    assert not desktop_entry.ensure_desktop_entry(dirs["icon"])          # and not written again


def test_a_leftover_icon_goes_when_the_package_is_installed(dirs):
    # Seen on CachyOS: the entry from the source was gone, its old icon stayed and hid the package's icon.
    _install_package_entry(dirs["system"])
    icon = desktop_entry._icon_path()
    os.makedirs(os.path.dirname(icon))
    with open(icon, "w") as f:
        f.write("<svg>old</svg>")
    assert desktop_entry.ensure_desktop_entry(dirs["icon"])
    assert not os.path.exists(icon)


def test_an_appimage_entry_keeps_its_icon_beside_a_package(dirs, monkeypatch):
    monkeypatch.setenv("APPIMAGE", "/home/me/Mixer.AppImage")
    desktop_entry.ensure_desktop_entry(dirs["icon"])
    monkeypatch.delenv("APPIMAGE")
    _install_package_entry(dirs["system"])
    assert not desktop_entry.ensure_desktop_entry(dirs["icon"])          # the package app runs: nothing removed
    assert dirs["entry"].exists() and os.path.exists(desktop_entry._icon_path())


def test_a_users_own_entry_is_left_alone(dirs):
    _install_package_entry(dirs["system"])
    os.makedirs(dirs["entry"].parent)
    dirs["entry"].write_text("[Desktop Entry]\nExec=aa-hdspe-aio-mixer --hidden\n")
    assert not desktop_entry.ensure_desktop_entry(dirs["icon"])
    assert "--hidden" in dirs["entry"].read_text()


def test_from_an_appimage_the_entry_starts_the_appimage(dirs, monkeypatch):
    _install_package_entry(dirs["system"])                               # even beside a package
    monkeypatch.setenv("APPIMAGE", "/home/me/Apps/My Mixer.AppImage")
    assert desktop_entry.ensure_desktop_entry(dirs["icon"])
    assert 'Exec="/home/me/Apps/My Mixer.AppImage"' in dirs["entry"].read_text().splitlines()
