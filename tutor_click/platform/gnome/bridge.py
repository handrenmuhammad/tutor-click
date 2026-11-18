"""A private connection to the installed GNOME Shell helper. No Shell.Eval or unsafe mode."""
import atexit
import json
import queue
import threading

from gi.repository import Gio, GLib

UUID = "tutor-click@handren.dev"
BUS = "org.gnome.Shell"
PATH = "/dev/handren/TutorClick"
IFACE = "dev.handren.TutorClick"
SETUP = "GNOME helper is not active. Run tutor-click install, then sign out of GNOME and sign back in once."
_connection = None
_lock = threading.RLock()
_loop = None
_pending = None


def _commands(connection):
    global _loop, _pending
    context = GLib.MainContext.new()
    loop = _loop = GLib.MainLoop.new(context, False)
    ready = threading.Event()
    pending = _pending = queue.Queue()

    def dispatch():
        from ...__main__ import send
        while True:
            command = pending.get()
            if command is None:
                return
            cmd, _, rest = command.partition(" ")
            try:
                send("show" if cmd == "type" else cmd, rest)
            except (OSError, TimeoutError):
                pass
    threading.Thread(target=dispatch, daemon=True).start()

    def run():
        context.push_thread_default()
        def command(_connection, _sender, _path, _iface, _signal, args):
            pending.put(args.unpack()[0])
        connection.signal_subscribe(BUS, IFACE, "Command", PATH, None, Gio.DBusSignalFlags.NONE, command)
        ready.set()
        loop.run()
        context.pop_thread_default()
    threading.Thread(target=run, daemon=True).start()
    ready.wait(5)


def request(op, **args):
    global _connection
    with _lock:
        if _connection is None or _connection.is_closed():
            _connection = Gio.DBusConnection.new_for_address_sync(
                Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None),
                Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
            _commands(_connection)
        try:
            reply = _connection.call_sync(BUS, PATH, IFACE, "Call", GLib.Variant("(s)", (json.dumps(dict(op=op, **args)),)),
                                          GLib.VariantType.new("(s)"), Gio.DBusCallFlags.NONE, 15000, None)
        except GLib.Error as e:
            raise RuntimeError(SETUP) from e
        result = json.loads(reply.unpack()[0])
        if "error" in result:
            raise RuntimeError(result["error"])
        return result["result"]


def close():
    global _connection, _pending
    with _lock:
        if _connection is not None:
            _connection.close_sync(None)
            _connection = None
        if _loop is not None:
            _loop.quit()
        if _pending is not None:
            _pending.put(None)
            _pending = None


atexit.register(close)
