"""CLI: `tutor-click` starts the app; every other command talks to the running app over a socket."""
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

from . import config

USAGE = """usage: tutor-click [command]
  (no command)      start the app, or bring its panel back if it is already running
  install           add Tutor Click to your app launcher and put `tutor-click` on PATH
  ask TEXT...       ask or tell the tutor something by text
  type              pop up a text prompt (fuzzel) and ask
  ptt-down|ptt-up   push-to-talk: start / stop recording
  next              guide mode: "I did that, what's next?"  auto mode: "continue"
  explain           stop and explain the current step in more detail
  do                the tutor performs the current step for you
  mode guide|auto|toggle   who clicks: you (guide) or the tutor (auto)
  stop              stop speaking and acting, clear the overlay
  reset             forget the lesson, start fresh
  status            mode, busy, which app the tutor may act in, which AI is answering
  backend [NAME]    switch the AI provider: claude-code, codex, local, api, openai, gemini. Without NAME, list them
  quit              close the app
  bench [N] [KINDS] measure input delivery on a probe window (takes over the mouse for a few minutes)"""

DESKTOP = """[Desktop Entry]
Type=Application
Name=Tutor Click
GenericName=Screen Tutor
Comment=A tutor that watches your screen and teaches you any app
Exec={exe} app
Icon=tutor-click
Terminal=false
Categories=Education;
"""


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
    if not os.path.exists(config.LAYER_SHELL_LIB):
        sys.exit(f"{config.LAYER_SHELL_LIB} not found - install it: sudo pacman -S gtk4-layer-shell")
    # gtk4-layer-shell must be loaded before libwayland-client, so re-exec with it preloaded.
    if config.LAYER_SHELL_LIB not in os.environ.get("LD_PRELOAD", ""):
        env = dict(os.environ, LD_PRELOAD=config.LAYER_SHELL_LIB)
        os.execve(sys.executable, [sys.executable, "-m", "tutor_click", "app"], env)
    from .daemon import Daemon

    sys.exit(Daemon().run())


def install():
    """Launcher entry, icon, and a `tutor-click` command in ~/.local/bin. No root needed."""
    exe = Path(sys.executable).with_name("tutor-click")
    share = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    desktop = share / "applications/tutor-click.desktop"
    icon = share / "icons/hicolor/scalable/apps/tutor-click.svg"
    link = Path.home() / ".local/bin/tutor-click"
    for path in (desktop, icon, link):
        path.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text(DESKTOP.format(exe=exe))
    icon.write_bytes((Path(__file__).parent.parent / "assets/tutor-click.svg").read_bytes())
    if link.is_symlink() or not link.exists():
        link.unlink(missing_ok=True)
        link.symlink_to(exe)
    else:
        print(f"left {link} alone: it is not a symlink")
    print(f"installed:\n  {desktop}\n  {icon}\n  {link} -> {exe}")


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
        install()
        return
    if cmd == "bench":
        from .bench import main as bench

        bench(args[1:])
        return
    if cmd == "type":
        r = subprocess.run(
            ["fuzzel", "--dmenu", "--prompt", "Ask the tutor: ", "--lines", "0"],
            input="", capture_output=True, text=True, check=False,
        )
        rest = r.stdout.strip()
        if not rest:
            return
        cmd = "ask"
    try:
        print(send(cmd, rest))
    except (FileNotFoundError, ConnectionRefusedError):
        sys.exit("Tutor Click is not running - start it with: tutor-click")


if __name__ == "__main__":
    main()
