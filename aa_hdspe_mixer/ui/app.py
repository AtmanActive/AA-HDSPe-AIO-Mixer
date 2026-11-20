"""Application shell: QApplication, QML window, system tray, single instance, visible/hidden modes."""
from __future__ import annotations

import argparse
import os
import signal
import sys

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QGuiApplication, QIcon, QWindow
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QMessageBox, QSystemTrayIcon

from .. import APP_ID, APP_NAME, __version__
from ..core.app_settings import AppSettings, start_mode, zoom_factor
from ..core.backend import AlsaBackend, MockBackend
from ..core.card import find_card
from ..core.engine import Engine
from ..core.memory import read_json
from .bridge import Bridge
from .desktop_entry import ensure_desktop_entry
from . import window_geometry

HERE = os.path.dirname(os.path.abspath(__file__))
ICON = os.path.join(HERE, "..", "resources", "icon_fader_3d.svg")
INSTANCE_KEY = f"{APP_ID}-{os.getuid()}"


class Shell(QObject):
    """Window/tray/file-dialog actions exposed to QML as `shell`."""

    visibleModeChanged = Signal(bool)

    def __init__(self, app: QApplication, engine: Engine, bridge: Bridge):
        super().__init__()
        self.app, self.engine, self.bridge = app, engine, bridge
        self.window: QWindow | None = None
        self.tray: QSystemTrayIcon | None = None
        self._tray_hint_shown = False
        self._quitting = False
        self.geometry_file: str | None = None      # where the window geometry is remembered (None = don't)
        self.zoom = 1.0                            # this run's zoom factor (the geometry is saved with it)
        self.restart_requested = False             # main() re-executes the app after the event loop ends
        self.show_maximized = False                # next showWindow() maximises (it was maximised when hidden)
        self._was_exposed = False
        bridge.metersEnabledChanged.connect(self._update_mode)
        bridge.presetsChanged.connect(self._rebuild_tray_menu)
        bridge.restartRequested.connect(self.restart)

    # ------------------------------------------------------------------ window & mode
    def attach(self, window: QWindow):
        self.window = window
        window.visibilityChanged.connect(lambda *_: self._update_mode())
        window.windowStateChanged.connect(self._on_state)
        window.installEventFilter(self)

    def _on_state(self, state):
        if state & Qt.WindowState.WindowMinimized and self.tray is not None:
            QTimer.singleShot(0, self.hideToTray)

    def eventFilter(self, obj, ev):
        # Wayland: minimising is done by the compositor and the app isn't told; Qt only sees the window
        # stop being exposed (KWin suspends it) while it still counts as visible. Treat that as minimised:
        # hide to the tray, like a minimise on X11. Meters and polling stop while not exposed either way.
        if obj is self.window and ev.type() == QEvent.Type.Expose:
            if self.window.isExposed():
                self._was_exposed = True
            elif self._was_exposed and self.window.isVisible():
                QTimer.singleShot(150, self._check_suspended)
            QTimer.singleShot(0, self._update_mode)
        return False

    def _check_suspended(self):
        w = self.window
        if (w and w.isVisible() and not w.isExposed() and self.tray is not None
                and QGuiApplication.platformName().startswith("wayland")):
            self.hideToTray()

    def _update_mode(self):
        w = self.window
        shown = bool(w and w.isVisible() and w.visibility() not in (QWindow.Visibility.Hidden, QWindow.Visibility.Minimized)
                     and (w.isExposed() or not self._was_exposed))
        self.engine.set_meters_active(shown and self.bridge.metersEnabled)
        self.bridge.set_window_shown(shown)          # clock status polling only while visible
        if not self.engine.meters_active:
            self.bridge.clear_levels()
        self.visibleModeChanged.emit(shown)

    @Slot()
    def showWindow(self):
        if self.window:
            if self.show_maximized:                # set before the window is mapped, Wayland ignores it
                self.show_maximized = False
                self.window.showMaximized()
            else:
                self.window.show()
                self.window.setWindowState(self.window.windowState() & ~Qt.WindowState.WindowMinimized)
            self.window.raise_()
            self.window.requestActivate()

    def save_geometry(self):
        if self.geometry_file and self.window:
            window_geometry.save(self.window, self.geometry_file, self.zoom)

    @Slot()
    def hideToTray(self):
        if not self.window:
            return
        self.show_maximized = bool(self.window.windowStates() & Qt.WindowState.WindowMaximized)
        if self.tray is None or not QSystemTrayIcon.isSystemTrayAvailable():
            self.window.showMinimized()
            self.save_geometry()
            return
        self.window.hide()
        self._was_exposed = False
        self.save_geometry()                       # after hiding: records "in the tray" for the Last option
        if not self._tray_hint_shown:
            self._tray_hint_shown = True
            self.tray.showMessage(APP_NAME, "Still running in the system tray. Meters are paused while hidden.",
                                  QSystemTrayIcon.MessageIcon.Information, 3000)

    @Slot()
    def toggleWindow(self):
        if self.window and self.window.isVisible() and self.window.visibility() != QWindow.Visibility.Minimized:
            self.hideToTray()
        else:
            self.showWindow()

    @Slot()
    def quit(self):
        self._quitting = True
        self.app.quit()

    @Slot()
    def restart(self):
        """Quit normally (memory and window geometry saved, card closed), then start again: see main()."""
        self.restart_requested = True
        self.quit()

    # ------------------------------------------------------------------ tray
    def create_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(QIcon(ICON), self)
        self.tray.setToolTip(f"{APP_NAME}\n{self.engine.info.name}")
        self.tray.activated.connect(self._on_tray)
        self._menu = QMenu()
        self._rebuild_tray_menu()
        self.tray.setContextMenu(self._menu)
        self.tray.show()

    def _rebuild_tray_menu(self):
        # Right-click menu: Show, presets, meters, Exit. Every action is created here, owned by the menu:
        # QMenu.clear() deletes the actions it owns, so a kept action would be gone after a rebuild (which
        # used to leave the menu empty after the first preset change).
        if self.tray is None:
            return
        m = self._menu
        m.clear()
        m.addAction("Show", self.showWindow)
        m.addSeparator()
        sub = m.addMenu("Recall Preset")
        names = self.bridge.presetNames
        any_preset = False
        for i, n in enumerate(names):
            if n:
                any_preset = True
                sub.addAction(f"{i + 1}: {n}", lambda i=i: (self.showWindow(), self.bridge.requestRecall(i)))
        sub.setEnabled(any_preset)
        meters = m.addAction("Show Meters")
        meters.setCheckable(True)
        meters.setChecked(self.bridge.metersEnabled)
        meters.toggled.connect(lambda on: setattr(self.bridge, "metersEnabled", on))
        m.addSeparator()
        m.addAction("Exit", self.quit)

    def _on_tray(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.toggleWindow()

    # ------------------------------------------------------------------ files
    def _ask(self, save: bool, title: str, default_name: str, flt: str) -> str:
        start = os.path.join(os.path.expanduser("~"), default_name)
        if save:
            path, _ = QFileDialog.getSaveFileName(None, title, start, flt)
            if path and not os.path.splitext(path)[1]:
                path += ".json"
        else:
            path, _ = QFileDialog.getOpenFileName(None, title, os.path.dirname(start), flt)
        return path

    def _try(self, what, fn):
        try:
            return fn()
        except (OSError, ValueError) as e:
            self.bridge.notify.emit("error", f"{what} failed: {e}")
            return None

    @Slot(str)
    def savePresetFile(self, name):
        path = self._ask(True, "Save Preset to File", f"{name or 'preset'}.aamix-preset.json",
                         "Mixer presets (*.json)")
        if path and self._try("Saving preset", lambda: self.engine.save_preset_file(path, name or "Preset")) is None:
            self.bridge.notify.emit("info", f"Preset saved to {path}")

    @Slot()
    def loadPresetFile(self):
        path = self._ask(False, "Load Preset from File", "", "Mixer presets (*.json)")
        if path:
            preset = self._try("Loading preset", lambda: self.engine.read_preset_file(path))
            if preset is not None:
                self.bridge.offer_file_preset(preset)       # confirmation overlay picks what to restore

    @Slot()
    def exportMemory(self):
        """Everything: routing, card and clock settings, virtual devices, MIDI, OSC and keyboard shortcuts."""
        path = self._ask(True, "Export Memory", "aa-hdspe-aio-mixer-memory.json", "Mixer memory (*.json)")
        if path and self._try("Export", lambda: self.bridge.export_all(path)) is None:
            self.bridge.notify.emit("info", f"Memory exported to {path}")

    @Slot()
    def importMemory(self):
        """Reads the file, then the import dialog picks which of its parts replace the app's."""
        path = self._ask(False, "Import Memory", "", "Mixer memory (*.json)")
        if path:
            self._try("Import", lambda: self.bridge.offer_import(path, read_json(path)))


def _single_instance() -> QLocalServer | None:
    """Returns a server if we are the first instance; otherwise pokes the first one and returns None."""
    sock = QLocalSocket()
    sock.connectToServer(INSTANCE_KEY)
    if sock.waitForConnected(300):
        sock.write(b"show\n")
        sock.waitForBytesWritten(300)
        sock.disconnectFromServer()
        return None
    QLocalServer.removeServer(INSTANCE_KEY)
    srv = QLocalServer()
    srv.listen(INSTANCE_KEY)
    return srv


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="aa-hdspe-aio-mixer", description=APP_NAME)
    ap.add_argument("--card", type=int, help="ALSA card number (default: first AIO / AIO Pro found)")
    ap.add_argument("--mock", action="store_true", help="use a simulated card")
    ap.add_argument("--mock-model", default="AIO", choices=["AIO", "AIO Pro"])
    ap.add_argument("--mock-speed", type=int, default=0, choices=[0, 1, 2])
    ap.add_argument("--hidden", action="store_true", help="start hidden in the system tray")
    ap.add_argument("--show", action="store_true", help=argparse.SUPPRESS)   # restart: window shown, as it was
    ap.add_argument("--memory-file", help="use this memory file instead of the per-card default")
    ap.add_argument("--no-desktop-entry", action="store_true",
                    help="don't install/refresh the user-local .desktop file and icon")
    ap.add_argument("--screenshot", help="(testing) save a PNG of the window after --screenshot-delay ms and exit")
    ap.add_argument("--screenshot-delay", type=int, default=1500)
    ap.add_argument("--window-size", help="(testing) initial window size, WxH")
    ap.add_argument("--qml-call", action="append", default=[],
                    help="(testing) call a root QML function before the screenshot: NAME or NAME:ARG")
    args = ap.parse_args(argv)

    if not (args.no_desktop_entry or args.screenshot or args.mock):
        # Must exist before the window maps: KWin picks the Wayland window icon from <app_id>.desktop.
        ensure_desktop_entry(ICON)

    # Zoom (cog panel): Qt's scale factor enlarges or shrinks the whole app, fonts, controls and dialogs
    # alike. Qt reads it only at start, so a change takes effect at the next start (Restart Now).
    # A QT_SCALE_FACTOR set in the environment is kept and multiplied.
    app_settings = AppSettings()
    env_scale = os.environ.get("QT_SCALE_FACTOR")
    try:
        base_scale = float(env_scale) if env_scale else 1.0
    except ValueError:
        base_scale = 1.0
    zoom = zoom_factor(app_settings.zoom)
    if zoom != 1.0:
        os.environ["QT_SCALE_FACTOR"] = f"{base_scale * zoom:g}"

    app = QApplication(sys.argv[:1])
    # Fusion draws its controls from the window palette, which follows the app's Colour mode (Theme.qml).
    # Set after the QApplication exists: Plasma's platform theme otherwise picks its own org.kde.desktop
    # style at that point, which paints buttons and drop-downs in the desktop's colours whatever the app uses.
    QQuickStyle.setStyle("Fusion")
    app.setApplicationName(APP_ID)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setDesktopFileName(APP_ID)
    app.setWindowIcon(QIcon(ICON))
    app.setQuitOnLastWindowClosed(False)

    server = None
    if not args.screenshot:
        server = _single_instance()
        if server is None:
            print(f"{APP_NAME} is already running; asked it to show its window.")
            return 0

    if args.mock:
        backend = MockBackend(args.mock_model, args.mock_speed)
    else:
        info = find_card(args.card)
        if info is None:
            QMessageBox.critical(None, APP_NAME, "No RME HDSPe AIO or AIO Pro found.\n\n"
                                 "The card must be driven by snd-hdspm or snd-hdspe. "
                                 "Use --mock to try the app without the card.")
            return 1
        backend = AlsaBackend(info)

    engine = Engine(backend, memory_file=args.memory_file)
    bridge = Bridge(engine, app_settings)
    shell = Shell(app, engine, bridge)
    shell.zoom = zoom

    qml = QQmlApplicationEngine()
    qml.rootContext().setContextProperty("mixer", bridge)
    qml.rootContext().setContextProperty("shell", shell)
    qml.load(QUrl.fromLocalFile(os.path.join(HERE, "qml", "Main.qml")))
    if not qml.rootObjects():
        return 2
    window = qml.rootObjects()[0]
    if args.window_size:
        w, h = (int(x) for x in args.window_size.lower().split("x"))
        window.resize(w, h)
    # At a large zoom the window's default and minimum sizes (in zoomed pixels) could exceed the screen:
    # cap them to it. (A remembered size is fitted to the screen when it is restored, below.)
    if window.screen() is not None:
        avail = window.screen().availableGeometry()
        window.setMinimumWidth(min(window.minimumWidth(), avail.width()))
        window.setMinimumHeight(min(window.minimumHeight(), avail.height()))
        window.resize(min(window.width(), avail.width()), min(window.height(), avail.height()))
    shell.attach(window)
    engine.start()
    start = "normal"                                       # "tray" | "maximized" | "normal"
    if not args.screenshot and not args.window_size:      # remember size/position (not in test runs)
        shell.geometry_file = window_geometry.geometry_path()
        last = window_geometry.load(shell.geometry_file)
        was_maximized = window_geometry.restore(window, shell.geometry_file, zoom)
        start = start_mode(app_settings.start_window, last)       # the cog panel's "Start window"
        if args.show:                                              # restarted from the window: as it was
            start = "maximized" if was_maximized else "normal"
        # Started in the tray, the window later opens as it was left (maximised or not).
        shell.show_maximized = start == "maximized" or (start == "tray" and was_maximized)
    if args.hidden:
        start = "tray"

    if server is not None:
        def on_conn():
            c = server.nextPendingConnection()
            c.readyRead.connect(shell.showWindow)
        server.newConnection.connect(on_conn)

    if args.screenshot:
        for call in args.qml_call:                     # "name" or "name:arg"
            fn, _, arg = call.partition(":")
            QTimer.singleShot(max(0, args.screenshot_delay - 400),
                              lambda fn=fn, arg=arg: getattr(window, fn)(*([arg] if arg else [])))

        def snap():
            window.grabWindow().save(args.screenshot)
            app.quit()
        window.show()
        shell._update_mode()
        QTimer.singleShot(args.screenshot_delay, snap)
    else:
        shell.create_tray()
        if start == "tray":
            if shell.tray is not None:
                window.hide()                              # straight into the tray, window never shown
            else:
                window.showMinimized()                     # no tray on this desktop
        else:
            shell.showWindow()                             # maximised if shell.show_maximized
        shell._update_mode()

    app.aboutToQuit.connect(shell.save_geometry)
    app.aboutToQuit.connect(engine.shutdown)
    app.aboutToQuit.connect(bridge.midi.shutdown)
    app.aboutToQuit.connect(bridge.osc.shutdown)
    app.aboutToQuit.connect(bridge.keys.shutdown)           # the keys are free while the app isn't running
    # Quit cleanly (saving memory) on SIGTERM/SIGINT, e.g. at logout or Ctrl+C in a terminal.
    # The idle timer lets the Python interpreter run its signal handlers while Qt's loop is busy.
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: shell.quit())
    pulse = QTimer(interval=500)
    pulse.timeout.connect(lambda: None)
    pulse.start()
    rc = app.exec()
    if shell.restart_requested:
        _restart(shell, server, env_scale)
    return rc


def _restart(shell: Shell, server: QLocalServer | None, env_scale: str | None):
    """Replace this process with a fresh start of the app (same command line), window shown."""
    if shell.tray is not None:
        shell.tray.hide()
    if server is not None:
        server.close()                             # the new process becomes the single instance
    if env_scale is None:                          # the new start sets its own zoom
        os.environ.pop("QT_SCALE_FACTOR", None)
    else:
        os.environ["QT_SCALE_FACTOR"] = env_scale
    argv = [a for a in sys.orig_argv if a not in ("--hidden", "--show")] + ["--show"]
    sys.stdout.flush(); sys.stderr.flush()
    os.closerange(3, 65536)                        # nothing (card, sockets) leaks into the new process
    os.execv(sys.executable, argv)
