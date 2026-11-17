"""CLI: `tutor-click` starts the app; every other command talks to the running app over a socket."""
import json
import socket
import sys

from . import config, platform

USAGE = """usage: tutor-click [command]
  (no command)      start the app, or bring its panel back if it is already running
  install           add Tutor Click to your app launcher and put `tutor-click` on PATH
  ask TEXT...       ask or tell the tutor something by text
  type              pop up a text prompt and ask
  ptt-down|ptt-up   push-to-talk: start / stop recording
  next              guide mode: "I did that, what's next?"  auto mode: "continue"
                    right after a start: carry on the lesson that was left unfinished
  explain           stop and explain the current step in more detail
  do                the tutor performs the current step for you
  mode guide|auto|toggle   who clicks: you (guide) or the tutor (auto)
  stop              stop speaking and acting, clear the overlay
  reset             close the lesson for good, start fresh
  status            mode, busy, which app the tutor may act in, which AI, model and effort, lesson and step
  backend [NAME]    switch the AI provider: claude-code, codex, local, api, openai, gemini. Without NAME, list them
  model [NAME]      which model that provider answers with. Without NAME, list the ones on offer
  effort [LEVEL]    how hard it thinks before answering (low is fastest). Without LEVEL, list the levels
  quit              close the app
  bench [N] [KINDS] measure input delivery on a probe window (takes over the mouse for a few minutes)
  doctor [--input] [--ask] [--no-sound]
                    check each thing the app needs, one at a time, and print a report to send back.
                    --input also tests the mouse and keyboard (takes them over for a few seconds),
                    --ask also sends one screenshot to the AI provider"""

def send(cmd: str, text: str = "") -> str:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(10)
        s.connect(str(config.SOCKET))
        s.sendall((json.dumps({"cmd": cmd, "text": text}) + "\n").encode())
        return s.makefile().readline().strip()


def run_app():
    try:
        send("show")  # already running: just bring the panel back
        return
    except (FileNotFoundError, ConnectionRefusedError):
        pass
    desk = _desk()
    desk.preflight(["app"])
    from .daemon import Daemon

    sys.exit(Daemon(desk).run())


def _desk():
    try:
        return platform.load()
    except platform.Unsupported as e:
        sys.exit(str(e))


def main():
    args = sys.argv[1:]
    if args and args[0] in ("-h", "--help", "help"):
        print(USAGE)
        return
    cmd, rest = (args[0], " ".join(args[1:])) if args else ("app", "")
    if cmd in ("app", "daemon"):
        run_app()
        return
    if cmd == "install":
        _desk().install()
        return
    if cmd == "doctor":
        desk = _desk()
        desk.preflight(["doctor", *args[1:]])
        from .doctor import run

        sys.exit(run(desk, args[1:]))
    if cmd == "bench":
        _desk().run_bench(args[1:])
        return
    if cmd == "type":
        rest = _desk().prompt()
        if not rest:
            return
        cmd = "ask"
    try:
        print(send(cmd, rest))
    except (FileNotFoundError, ConnectionRefusedError):
        sys.exit("Tutor Click is not running - start it with: tutor-click")


if __name__ == "__main__":
    main()
