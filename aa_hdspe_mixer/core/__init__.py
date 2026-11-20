"""UI-independent core: ALSA access, channel maps, mixer state, presets and the engine.

Nothing in here imports QtGui/QtQml/QtWidgets, so the engine keeps running (and can later be driven by
MIDI/OSC) while the window is hidden.
"""
