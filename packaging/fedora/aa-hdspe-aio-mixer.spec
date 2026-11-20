# Built by packaging/fedora/build.sh, which passes the version (from aa_hdspe_mixer/__init__.py) and the
# source tree: rpmbuild -bb --define "app_version X" --define "app_source DIR" this.spec
Name:           aa-hdspe-aio-mixer
Version:        %{app_version}
Release:        %{?pkg_release}%{!?pkg_release:1}%{?dist}
Summary:        Routing-focused hardware mixer for RME HDSPe AIO and AIO Pro audio cards
License:        MIT
URL:            https://github.com/AtmanActive/AA-HDSPe-AIO-Mixer
BuildArch:      noarch

Requires:       python3 >= 3.11
Requires:       python3-pyside6
Requires:       qt6-qtdeclarative
Requires:       qt6-qtsvg
Requires:       alsa-lib
# Keyboard shortcuts (KDE Plasma), virtual PipeWire devices, the test tone.
Recommends:     python3-gobject
Recommends:     pipewire-utils
Recommends:     alsa-utils

%description
Routes the card's inputs and playback channels to its outputs, with levels,
mutes, presets, meters, the card's and clock's settings, virtual PipeWire
devices, and remote control over MIDI, OSC and keyboard shortcuts.
Works with the in-kernel snd-hdspm driver and the out-of-tree snd-hdspe driver.

%install
%{app_source}/packaging/stage.sh %{buildroot}
# The license goes in /usr/share/licenses instead (the files list below).
rm %{buildroot}%{_docdir}/%{name}/LICENSE
cp %{app_source}/LICENSE .

%files
%license LICENSE
%{_docdir}/%{name}/
%{_bindir}/%{name}
%{_datadir}/%{name}/
%{_datadir}/applications/%{name}.desktop
%{_datadir}/icons/hicolor/scalable/apps/%{name}.svg
