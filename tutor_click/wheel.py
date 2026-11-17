"""Mouse-wheel scrolling through the wlr-virtual-pointer Wayland protocol.

Hyprland has no dispatcher for the wheel, so this speaks the Wayland wire format directly:
no extra library. The scroll lands on whatever is under the cursor, so move the cursor first.
"""
import os
import socket
import struct
import time

MANAGER = "zwlr_virtual_pointer_manager_v1"
NOTCH = 15.0  # axis units per wheel click, as libinput reports
MAX_NOTCHES = 10
# zwlr_virtual_pointer_v1 request opcodes
MOTION, AXIS_SOURCE, AXIS_DISCRETE, FRAME, DESTROY = 0, 5, 7, 4, 8
WHEEL, VERTICAL = 0, 0


class WheelError(Exception):
    pass


class _Wire:
    def __init__(self):
        runtime = os.environ.get("XDG_RUNTIME_DIR")
        if not runtime:
            raise WheelError("XDG_RUNTIME_DIR is not set")
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(2)
        self.sock.connect(os.path.join(runtime, os.environ.get("WAYLAND_DISPLAY", "wayland-0")))
        self.buf = b""
        self.next_id = 2  # 1 is wl_display

    def new_id(self) -> int:
        self.next_id += 1
        return self.next_id - 1

    def send(self, obj: int, opcode: int, payload: bytes = b""):
        self.sock.sendall(struct.pack("<II", obj, ((8 + len(payload)) << 16) | opcode) + payload)

    def events(self):
        """Yield (object id, opcode, body) for each event as it arrives."""
        while True:
            while len(self.buf) >= 8:
                obj, word = struct.unpack_from("<II", self.buf)
                size = word >> 16
                if len(self.buf) < size:
                    break
                body, self.buf = self.buf[8:size], self.buf[size:]
                yield obj, word & 0xFFFF, body
            chunk = self.sock.recv(65536)
            if not chunk:
                raise WheelError("the compositor closed the connection")
            self.buf += chunk

    def roundtrip(self, on_event=None):
        """wl_display.sync: returns once the compositor has processed everything sent so far."""
        callback = self.new_id()
        self.send(1, 0, struct.pack("<I", callback))
        for obj, opcode, body in self.events():
            if obj == 1 and opcode == 0:  # wl_display.error(object, code, message)
                length = struct.unpack_from("<I", body, 8)[0]
                raise WheelError(body[12 : 12 + length - 1].decode(errors="replace"))
            if obj == callback:
                return
            if on_event:
                on_event(obj, opcode, body)


def _string(text: str) -> bytes:
    raw = text.encode() + b"\0"
    return struct.pack("<I", len(raw)) + raw + b"\0" * (-len(raw) % 4)


def _fixed(value: float) -> int:
    return round(value * 256)


def scroll(notches: int):
    """Turn the wheel under the cursor. Positive scrolls down, negative up. Bounded."""
    if notches == 0:
        return
    notches = max(-MAX_NOTCHES, min(MAX_NOTCHES, notches))
    wire = _Wire()
    try:
        registry = wire.new_id()
        wire.send(1, 1, struct.pack("<I", registry))  # wl_display.get_registry
        found = {}

        def on_global(obj, opcode, body):
            if obj == registry and opcode == 0:  # wl_registry.global(name, interface, version)
                name, length = struct.unpack_from("<II", body)
                if body[8 : 8 + length - 1].decode() == MANAGER:
                    found["name"] = name

        wire.roundtrip(on_global)
        if "name" not in found:
            raise WheelError("this compositor has no virtual pointer support")

        manager, pointer = wire.new_id(), wire.new_id()
        # wl_registry.bind(name, interface, version, new id)
        wire.send(registry, 0, struct.pack("<I", found["name"]) + _string(MANAGER) + struct.pack("<II", 1, manager))
        wire.send(manager, 0, struct.pack("<II", 0, pointer))  # create_virtual_pointer(seat = default)
        # Hyprland drops one wheel click from a fresh virtual pointer unless it has sent
        # something first (measured: 3 sent, 2 arrive). A zero-distance move costs nothing.
        wire.send(pointer, MOTION, struct.pack("<Iii", 0, 0, 0))
        wire.send(pointer, FRAME)
        wire.roundtrip()
        step = 1 if notches > 0 else -1
        for _ in range(abs(notches)):
            ms = int(time.monotonic() * 1000) & 0xFFFFFFFF
            wire.send(pointer, AXIS_SOURCE, struct.pack("<I", WHEEL))
            wire.send(pointer, AXIS_DISCRETE, struct.pack("<IIii", ms, VERTICAL, _fixed(NOTCH * step), step))
            wire.send(pointer, FRAME)
            wire.roundtrip()
            time.sleep(0.03)
        wire.send(pointer, DESTROY)
        wire.roundtrip()
    except (OSError, struct.error) as e:
        raise WheelError(str(e)) from e
    finally:
        wire.sock.close()
