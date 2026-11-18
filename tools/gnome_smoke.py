"""Exercise the GNOME helper in an isolated headless Shell, without touching the user's session.

Run with the project's Python: python tools/gnome_smoke.py. Logs stay in /tmp/tutor-click-gnome-smoke/.
Requires GNOME Shell 51, D-Bus, dconf, and the normal Tutor Click dependencies.
"""
import os
from pathlib import Path
import shutil
import re
import queue
import threading
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = Path("/tmp/tutor-click-gnome-smoke")


def diagnostic():
    import faulthandler
    faulthandler.dump_traceback_later(20, repeat=True)
    from tutor_click import doctor
    from tutor_click.__main__ import main as cli_main
    original = doctor._overlay
    def overlay(desk, seen):
        result = original(desk, seen)
        time.sleep(0.2)
        (OUT / "panel.jpg").write_bytes(desk.capture(seen["mon"], 1280).jpeg)
        return result
    doctor._overlay = overlay
    sys.argv = ["tutor-click", "doctor", "--input", "--no-sound"]
    cli_main()


def native_scroll():
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gio, Gtk
    from tutor_click.platform import gnome
    from tutor_click.platform.gnome import bridge, screen
    events, errors = [], []
    def activate(app):
        window = Gtk.ApplicationWindow(application=app, title="Tutor Click scroll probe", default_width=520, default_height=360)
        controller = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.BOTH_AXES)
        controller.connect("scroll", lambda _c, dx, dy: events.append((dx, dy)) or True)
        window.add_controller(controller)
        window.set_child(Gtk.Label(label="Isolated native Wayland scroll test"))
        window.present()
        def check():
            try:
                time.sleep(0.8)
                w = next(screen.window(w) for w in screen.state()["windows"] if w["title"] == "Tutor Click scroll probe")
                mon = gnome.focused_monitor()
                gnome.Hands(mon).scroll(w.at[0] + w.size[0] / 2 - mon.x, w.at[1] + w.size[1] / 2 - mon.y, 2)
                time.sleep(0.4)
                print("native Wayland scroll", events, flush=True)
                assert events and any(dy > 0 for _, dy in events)
            except Exception as e:
                errors.append(e)
            finally:
                from gi.repository import GLib
                GLib.idle_add(app.quit)
        threading.Thread(target=check, daemon=True).start()
    bridge.request("connect")
    app = Gtk.Application(application_id="dev.handren.TutorClickScrollProbe", flags=Gio.ApplicationFlags.NON_UNIQUE)
    app.connect("activate", activate)
    app.run([])
    bridge.close()
    if errors:
        raise errors[0]


def hotkeys():
    from tutor_click import __main__ as cli
    from tutor_click.daemon import BINDS
    from tutor_click.platform import gnome
    from tutor_click.platform.gnome import bridge, hands, screen
    from tutor_click.platform.qt.probe import Probe
    from tutor_click.platform.qt.app import _screen
    received = queue.Queue()
    cli.send = lambda cmd, text="": received.put((cmd, text))
    failures = []

    def start(app):
        mon = gnome.focused_monitor()
        probe = Probe(gnome._screen_at(mon, _screen))
        app.probe = probe
        def check():
            try:
                time.sleep(0.8)
                win = next(screen.window(w) for w in screen.state()["windows"] if w["title"] == "Tutor Click doctor")
                h = hands.Hands(mon)
                h._focus(win)
                gnome.binds(True, BINDS, "")
                for key, expected in (("n", [("next", "")]), ("space", [("ptt-down", ""), ("ptt-up", "")]),
                                      ("Escape", [("stop", "")])):
                    keyval = ord(key) if len(key) == 1 else hands.keysym(key)
                    bridge.request("key", id=win.address, key=keyval, mods=hands.parse_mods("super+alt"))
                    actual = [received.get(timeout=3) for _ in expected]
                    assert actual == expected, (key, actual)
                    print(f"hotkey {key}: {actual}", flush=True)
                gnome.binds(False, BINDS, "")
                print("hotkeys registered, delivered and removed", flush=True)
            except Exception as e:
                failures.append(e)
            finally:
                app.quit()
        threading.Thread(target=check, daemon=True).start()
    gnome.preflight([])
    gnome.App(start).run()
    if failures:
        raise failures[0]


def session():
    from gi.repository import Gio, GLib
    from tutor_click.platform.gnome import bridge

    subprocess.run(["gsettings", "set", "org.gnome.shell", "enabled-extensions", f"['{bridge.UUID}']"], check=True)
    subprocess.run(["gsettings", "set", "org.gnome.shell", "disable-user-extensions", "false"], check=True)
    subprocess.run(["gsettings", "set", "org.gnome.shell", "welcome-dialog-last-shown-version", "'51.1'"], check=True)
    subprocess.run(["gsettings", "set", "org.gnome.desktop.session", "idle-delay", "0"], check=True)
    subprocess.run(["gsettings", "set", "org.gnome.desktop.interface", "enable-hot-corners", "false"], check=True)
    with (OUT / "shell.log").open("w") as log:
        shell = subprocess.Popen(["gnome-shell", "--headless", "--wayland", "--virtual-monitor", "1280x800",
                                  "--wayland-display", "tutor-click-test"], stdout=log, stderr=log)
        try:
            for _ in range(100):
                try:
                    bridge.request("connect")
                    break
                except (RuntimeError, GLib.Error):
                    if shell.poll() is not None:
                        raise RuntimeError(f"GNOME Shell exited: see {OUT / 'shell.log'}")
                    time.sleep(0.1)
            else:
                errors = subprocess.run(["gnome-extensions", "info", bridge.UUID], capture_output=True, text=True)
                raise RuntimeError(f"Helper did not load: {errors.stdout} {errors.stderr}; see {OUT / 'shell.log'}")
            for _ in range(60):
                if "GNOME Shell started at" in (OUT / "shell.log").read_text():
                    break
                time.sleep(0.1)
            from tutor_click.platform import gnome
            monitor = gnome.focused_monitor()
            print("monitor", monitor, flush=True)
            shot = gnome.capture(monitor, 1280)
            (OUT / "desktop.jpg").write_bytes(shot.jpeg)
            overlay = gnome.Overlay(monitor)
            overlay.show_step((640, 400), "GNOME test", "Testing Tutor Click in an isolated GNOME session.")
            time.sleep(0.8)
            shot = gnome.capture(monitor, 1280)
            (OUT / "overlay.jpg").write_bytes(shot.jpeg)
            from tutor_click.doctor import ring_points
            from tutor_click.verify import pixels
            shown = ring_points(pixels(shot.jpeg), 640, 400, shot.factor)
            overlay.set_hidden(True)
            hidden = ring_points(pixels(gnome.capture(monitor, 1280).jpeg), 640, 400, 1)
            assert shown >= 8 and hidden <= 4, (shown, hidden)
            print(f"overlay: {shown}/16 ring points visible, {hidden}/16 hidden", flush=True)
            overlay.clear()
            # Use this isolated compositor's Xwayland, never the real desktop's display.
            display = re.search(r"public X11 display (:\d+)", (OUT / "shell.log").read_text()).group(1)
            auth = next(Path(os.environ["XDG_RUNTIME_DIR"]).glob(".mutter-Xwaylandauth.*"))
            os.environ["DISPLAY"] = display
            os.environ["XAUTHORITY"] = str(auth)
            os.environ["QT_QPA_PLATFORM"] = "xcb"
            os.environ["QT_NO_GLIB"] = "1"
            os.environ["WAYLAND_DISPLAY"] = "tutor-click-test"
            # Dismiss the startup overview through GNOME's supported property.
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            bus.call_sync("org.gnome.Shell", "/org/gnome/Shell", "org.freedesktop.DBus.Properties", "Set",
                          GLib.Variant("(ssv)", ("org.gnome.Shell", "OverviewActive", GLib.Variant("b", False))),
                          None, Gio.DBusCallFlags.NONE, 5000, None)
            time.sleep(0.8)  # allow GNOME's hide animation and modal grab to finish
            print("desktop state", {k: v for k, v in bridge.request("state").items() if k in ("overview", "modal")}, flush=True)
            (OUT / "ready.jpg").write_bytes(gnome.capture(monitor, 1280).jpeg)
            bridge.request("move", x=640, y=400)
            time.sleep(0.2)
            print("pointer after move", bridge.request("state")["cursor"], flush=True)
            bridge.close()  # the doctor is the sole owner while it runs
            with (OUT / "doctor.log").open("w") as doctor_log:
                code = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--diagnostic"],
                                      stdout=doctor_log, stderr=doctor_log, timeout=55)
            print((OUT / "doctor.log").read_text(), flush=True)
            doctor_ok = code.returncode == 0
            with (OUT / "hotkeys.log").open("w") as hotkey_log:
                code = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--hotkeys"],
                                      stdout=hotkey_log, stderr=hotkey_log, timeout=20)
            print((OUT / "hotkeys.log").read_text(), flush=True)
            if code.returncode:
                raise RuntimeError(f"hotkeys exited {code.returncode}: see {OUT / 'hotkeys.log'}")
            with (OUT / "scroll.log").open("w") as scroll_log:
                code = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--scroll"],
                                      stdout=scroll_log, stderr=scroll_log, timeout=15)
            print((OUT / "scroll.log").read_text(), flush=True)
            if code.returncode:
                raise RuntimeError(f"native scroll failed: see {OUT / 'scroll.log'}")
            if not doctor_ok:
                raise RuntimeError(f"doctor checks failed: see {OUT / 'doctor.log'}")
        finally:
            bridge.close()
            shell.terminate()
            try:
                shell.wait(timeout=8)
            except subprocess.TimeoutExpired:
                shell.kill()
                shell.wait()


def main():
    OUT.mkdir(exist_ok=True)
    if "--scroll" in sys.argv:
        native_scroll()
        return
    if "--diagnostic" in sys.argv:
        diagnostic()
        return
    if "--hotkeys" in sys.argv:
        hotkeys()
        return
    if "--session" in sys.argv:
        session()
        return
    with tempfile.TemporaryDirectory(prefix="tutor-click-gnome-", ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        data = root / "data"
        extension = data / "gnome-shell/extensions/tutor-click@handren.dev"
        shutil.copytree(ROOT / "tutor_click/platform/gnome/extension", extension)
        runtime = root / "runtime"
        runtime.mkdir(mode=0o700)
        env = dict(os.environ, XDG_DATA_HOME=str(data), XDG_CONFIG_HOME=str(root / "config"),
                   XDG_CACHE_HOME=str(root / "cache"), XDG_STATE_HOME=str(root / "state"),
                   XDG_RUNTIME_DIR=str(runtime), XDG_CURRENT_DESKTOP="GNOME", DESKTOP_SESSION="gnome",
                   LIBGL_ALWAYS_SOFTWARE="1", MUTTER_DEBUG_DUMMY_MODE_SPECS="1280x800", GIO_USE_VFS="local", NO_AT_BRIDGE="1")
        for key in ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "HYPRLAND_INSTANCE_SIGNATURE"):
            env.pop(key, None)
        code = subprocess.run(["dbus-run-session", "--", sys.executable, str(Path(__file__).resolve()), "--session"], env=env)
        sys.exit(code.returncode)


if __name__ == "__main__":
    main()
