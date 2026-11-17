# Tutor Click

A screen tutor for Hyprland. Tell it what you want to learn ("rig a hand in Blender", "make a
pivot table"), by voice or text. It looks at your screen, points at the exact control, shows a
caption and subtitle, and explains each step out loud. It works with any app, because it only
looks at pixels.

Two modes, switchable at any time:

- **I click** (guide): you do every step. Press **Next step** when done; it checks the screen
  before moving on. **Do it for me** performs just the current step.
- **Tutor clicks** (auto): the tutor performs the steps itself while explaining what it is doing
  and why, checking the screen after each one.

You can interrupt at any moment: talk, type, or press **Explain more** or **Stop**. Speech stops
within about 0.3 s and the tutor stops acting.

## Start

```bash
tutor-click            # or pick "Tutor Click" in your app launcher
```

A small panel appears in the bottom corner: text box, Talk button, mode switch, Next step,
Explain more, Do it for me, Stop, New lesson.

| Keys (registered while the app runs) | Action |
|---|---|
| hold SUPER+ALT+Space | talk (also interrupts) |
| SUPER+ALT+N | next step / continue |
| SUPER+ALT+E | stop and explain this step more |
| SUPER+ALT+M | switch who clicks |
| SUPER+ALT+T | type a question (fuzzel) |
| SUPER+ALT+Escape | stop talking and acting |

Every command also works from a terminal: `tutor-click ask "..."`, `next`, `explain`, `do`,
`mode guide|auto|toggle`, `backend`, `model`, `effort`, `stop`, `reset`, `status`, `quit`. Log: `~/.local/state/tutor-click/log`.

## How it works

```
panel / hotkeys / CLI ──unix socket──► app (one lesson job at a time; new input cancels it)
                                        ├─ pw-record → faster-whisper (local speech-to-text)
                                        ├─ grim screenshot (overlay and panel hidden first)
                                        ├─ one AI provider, your choice (vision → JSON: say, caption, x,y, action, risk)
                                        ├─ GTK4 layer-shell overlay (click-through) + control panel
                                        ├─ Kokoro neural voice, streamed per sentence (espeak-ng fallback)
                                        └─ hands: mouse/keyboard through Hyprland dispatchers (auto mode, "do")
```

Each step sends one screenshot, your last 5 requests, the last 8 exchanges as text, and the names
of up to 30 tools or ideas the lesson has already taught.

Everything that touches the desktop sits in `tutor_click/platform/`: screenshots, windows, input,
the overlay and panel, hotkeys, the microphone and speaker. The lesson loop, the checks, the
voice models and the providers only see the backend that `platform.load()` returns:
`platform/hyprland/`, or `platform/macos/` (see "macOS" below: written, not yet run on a Mac). On
any other desktop the app says so and stops. The input guards (one app per lesson, never a
terminal, focus and cursor checked before a press) are in `platform/hands.py` and are the same
code on every desktop.

## How it checks its own actions

Before any click, drag, scroll or hovered key goes out, the app checks that the window took focus
and that the cursor is where it was sent (within 2 px). If not, nothing is pressed.

It takes a screenshot at that moment and another after the action, and compares the app's window
in the two. The model is then told what was measured ("input sent, the window changed, 1.8% of
it" or "no visible change"), never that the step worked. A change is evidence that the app
reacted, not that the right thing happened; the model still judges that from the screenshot.

If nothing changed and the step is one that must not be done twice (drawing, typing, pressing OK),
the tutor stops and asks you instead of trying again.

Everything is recorded in `~/.local/state/tutor-click/trace.jsonl`: one line per look, per action and
per outcome. `tutor-click bench` measures whether input arrives at all, on a probe window.

## Lessons carry on after a restart

Every lesson is saved to `~/.local/share/tutor-click/lessons/<id>.json` after each step and each
outcome: what you asked for, each step (caption, what was said, the action, what it expected, its
risk, the idea it taught), and what became of it (`verified`, `uncertain`, `failed`, `held`, or
`reported` when you said you did it). The file is written in full and swapped into place, so a
crash leaves the last complete record.

When the app starts and the most recent lesson was left unfinished, the panel says so. **Next
step** takes it up: the tutor gets its memory back, looks at the screen as it is now, and gives
the step that is needed. Asking for anything else starts a new lesson and leaves the old one
behind. **New lesson** closes a lesson for good. Nothing is sent to the AI until you choose.

In "I click" mode, **Next step** tells the model what its last step expected and whether the
window it pointed at has changed since, so it can say "that has not happened yet" and give a hint
instead of moving on.

Kept: the newest 200 steps of a lesson (116 KiB at that size, about 2 ms to save) and the newest
50 lessons (5.6 MiB at most). Older ones are deleted.

## What the tutor's hands can and cannot do

- Can: click, double/right/middle click, drag, scroll the mouse wheel (up to 10 clicks per step),
  key combos and sequences (`g z 2 enter`), type ASCII text (US layout). No extra tools: clicks and
  keys use `hyprctl eval` (`cursor.move`, `send_key_state`, `send_shortcut`); the wheel uses the
  wlr-virtual-pointer Wayland protocol, spoken directly from `tutor_click/platform/hyprland/wheel.py`.
- Cannot: scroll while holding a modifier (Ctrl+wheel, Shift+wheel). It uses keyboard or menu
  alternatives, or asks you.
- It acts only inside one app per lesson: the first app it acts in. Anything outside is refused
  and left to you. **New lesson** clears that.
- It never clicks or types in terminals or the Claude app (`TUTOR_CLICK_NEVER_ACT`).
- At most 12 actions in a row, then it pauses (`TUTOR_CLICK_AUTO_MAX_STEPS`). The same action
  three times in a row also pauses it.
- Each step carries a risk label from the model, and the app enforces it:
  - *sensitive* (saving or overwriting a file, changing settings, sending): only after you press
    **Do it for me** on that step.
  - *irreversible* (deleting files, discarding work, paying, passwords): never. That is yours.
  The label comes from the model, so this is as good as the model's labelling. The limits that do
  not depend on the model are the one-app rule and `TUTOR_CLICK_NEVER_ACT`.

## Setup

```bash
sudo pacman -S gtk4-layer-shell
uv venv --system-site-packages --python /usr/bin/python3 .venv
uv pip install --python .venv/bin/python -e .
.venv/bin/tutor-click install     # launcher entry, icon, ~/.local/bin/tutor-click
```

## Which AI answers

Pick it in the panel ("Answers come from") or with `tutor-click backend NAME`. `tutor-click backend` alone
lists them with what each is missing.

| Name | What it is | Who pays | Screenshots go to |
|---|---|---|---|
| `claude-code` (default) | headless Claude Code, `claude -p`, on your sign-in | your Claude subscription | Anthropic |
| `codex` | headless Codex, `codex exec`, on your sign-in | your ChatGPT plan | OpenAI |
| `local` | a model server on this computer (Ollama, LM Studio) | nobody | this computer only |
| `api` | Anthropic API, `ANTHROPIC_API_KEY` | you, per use | Anthropic |
| `openai` | OpenAI API, `OPENAI_API_KEY` | you, per use, separate from a ChatGPT plan | OpenAI |
| `gemini` | Gemini API, `GEMINI_API_KEY` | you, per use | Google |

### Model and effort

The panel has a **Model** and an **Effort** menu for the provider in use; `tutor-click model NAME` and
`tutor-click effort LEVEL` do the same, and without a name they list what is on offer. A change
applies from the next step and is remembered per provider in
`~/.local/share/tutor-click/settings.json`. If `TUTOR_CLICK_MODEL` or `TUTOR_CLICK_EFFORT` is set
when the app starts, it wins over what was remembered.

- Claude (`claude-code`, `api`): Fable 5.2, Opus 5.5, Sonnet 5.5, Haiku 5.5; effort `low`,
  `medium`, `high`, `xhigh`, `max`. The default is Opus 5.5 at `medium`.
- Codex: effort only. Its model is Codex's own default, or `TUTOR_CLICK_CODEX_MODEL`.
- `openai`, `gemini`, `local`: the model comes from their environment variable; no effort setting.

Measured on 2026-11-17, pointing at 47 named Pinta controls (about 16 x 18 screenshot px each),
three questions at a time, on the Claude subscription:

| Model | Effort | Inside the control | Seconds per answer, median (p90) |
|---|---|---|---|
| Haiku 5.5 | low | 46 of 47 | 5.3 (6.7) |
| Haiku 5.5 | medium | 46 of 47 | 5.5 (6.1) |
| Opus 5.5 | low | 44 of 47 | 7.6 (11.2) |
| Opus 5.5 | medium | 45 of 47 | 7.0 (9.8) |
| Sonnet 5.5 | medium | 44 of 47 | 8.1 (10.0) |

One control more or less out of 47 is noise, so pointing accuracy is the same across these, and
`low` against `medium` shows no difference in either accuracy or speed. Haiku answers about two
seconds sooner. What this does not measure is the lesson itself: choosing the right next step,
noticing that a step did not work, labelling risk. Only Opus at `medium` has been through real
lessons. Fable 5.2 and the efforts above `medium` are not measured.

- The app never switches by itself. If a provider fails or runs out of quota it stops, says so,
  and names the others that are usable right now. A subscription is never swapped for a paid key.
- The first time you pick a provider that sends screenshots somewhere new, it tells you where and
  who pays, and you pick it again to confirm. That is remembered in
  `~/.local/share/tutor-click/providers.json`.
- Switching in the middle of a lesson keeps the lesson: the history and the app's own checks do
  not belong to any provider.
- Tested for real: `claude-code` and `codex`. The other four are tested against stand-ins only
  (no key is set and no local server is installed here). Gemini's endpoint address is unverified.
- Measured on 47 named Pinta controls: Claude Opus (medium) 45 hits, about 7 s per answer; Codex
  gpt-6.2-sol (low) 45 hits, about 10 s per answer.

Voice model (Kokoro int8, ~120 MB total) goes in `~/.local/share/tutor-click/kokoro/`:

```bash
mkdir -p ~/.local/share/tutor-click/kokoro && cd ~/.local/share/tutor-click/kokoro
curl -fLO https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.int8.onnx
curl -fLO https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin
```

## Development

```bash
uv pip install --python .venv/bin/python pytest
.venv/bin/python -m pytest -q            # 244 tests, no desktop needed
.venv/bin/python -m tutor_click doctor   # checks screen, overlay, sound and provider on this machine
.venv/bin/python -m tutor_click bench 100      # input delivery on a probe window, about 10 minutes
.venv/bin/python tools/pointing.py       # pointing accuracy against an app's accessibility tree
```

## macOS

**Not run on a Mac yet.** It was written on Linux. What is tested here: the Qt overlay and panel
(drawn off screen, and the real lesson loop run on them), the input guards, and the Mac key,
window and capture logic against a stand-in for the system. What nothing here could test is
`platform/macos/native.py`, the 120 lines that call macOS itself, and how macOS treats the windows
(staying on top, letting clicks through, coming to the front).

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh      # uv, if it is not installed
 git clone https://github.com/handrenmuhammad/tutor-click.git
cd tutor-click
uv venv --python 3.12 && uv pip install -e .
```

While the repository is private, the clone needs a GitHub account that has been invited to it and
is signed in on the Mac (GitHub's `gh` command does both). To get a later fix, run `git pull` in
the folder, then `uv pip install -e .` again if the fix adds dependencies.

Install Claude Code and sign in to it with your own account (`claude`, then `/login`).

The natural voice is not in the repository (about 120 MB). Without these two files the tutor
speaks with the Mac's own `say` voice:

```bash
mkdir -p ~/.local/share/tutor-click/kokoro && cd ~/.local/share/tutor-click/kokoro
curl -fLO https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.int8.onnx
curl -fLO https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin
```

Then, from the `tutor-click` folder:

```bash
.venv/bin/tutor-click doctor                  # says which permissions to switch on, and where
.venv/bin/tutor-click doctor --input --ask    # also tests mouse and keyboard, and asks the AI once
.venv/bin/tutor-click                         # the app
```

`doctor` checks one thing at a time and prints a report (also saved under
`~/.local/state/tutor-click/`). Send that report back whatever it says: it names the part that
failed. `--input` takes over the mouse and keyboard for a few seconds on a test window of its own.
`--ask` sends one screenshot to the AI. Without them nothing leaves the computer.

macOS asks for two permissions, both for the app Tutor Click is started from (Terminal, iTerm):
Screen Recording and Accessibility, under System Settings > Privacy & Security. The first
`doctor` run asks macOS to list the app there.

Known gaps on macOS: no global hotkeys (every command is on the panel), `tutor-click type`,
`install` and `bench` are not built, the overlay does not follow you into a full-screen app's own
Space, and one wheel click is assumed to be three lines. Apple Silicon is assumed; whether every
dependency installs on an Intel Mac is not known.

## Settings (env)

| Variable | Default | |
|---|---|---|
| `TUTOR_CLICK_BACKEND` | `claude-code` | the provider the app starts with (see "Which AI answers") |
| `TUTOR_CLICK_CODEX_MODEL` | Codex's default | |
| `TUTOR_CLICK_CODEX_EFFORT` | `low` | `low`, `medium`, `high`, `xhigh`, `max`; outranks the Effort menu's remembered choice |
| `TUTOR_CLICK_OPENAI_MODEL` | `gpt-6.2-astra` | |
| `TUTOR_CLICK_GEMINI_MODEL` | none | must be set to use `gemini` |
| `TUTOR_CLICK_LOCAL_MODEL` | none | a vision model you have pulled; must be set to use `local` |
| `TUTOR_CLICK_LOCAL_URL` | `http://localhost:11434/v1` | Ollama's default address |
| `TUTOR_CLICK_MODEL` | `claude-opus-5-5` | outranks the Model menu's remembered choice |
| `TUTOR_CLICK_EFFORT` | `medium` | `low`, `medium`, `high`, `xhigh`, `max`; outranks the Effort menu's remembered choice |
| `TUTOR_CLICK_MAX_EDGE` | `1568` | screenshot long edge sent to Claude |
| `TUTOR_CLICK_WHISPER` | `base.en` | faster-whisper model; downloads from Hugging Face on first use |
| `TUTOR_CLICK_VOICE` | `af_heart` | Kokoro voice: `am_michael`, `bf_emma`, `bm_george`, ... |
| `TUTOR_CLICK_VOICE_SPEED` | `1.05` | |
| `TUTOR_CLICK_AUTO_MAX_STEPS` | `12` | actions in a row before the tutor pauses |
| `TUTOR_CLICK_NEVER_ACT` | terminals, Claude | window classes the tutor never acts in |
| `TUTOR_CLICK_BINDS` | `1` | `0` = do not register hotkeys (use `hypr/binds.lua`) |

## Known limits

- The overlay and panel stay on the monitor that was focused when the app started.
- On a 3440×1440 screen the screenshot is scaled to 1567×656, so pointing at tiny buttons can be off.
- Tutor-clicks mode takes about 20 s per step (5 to 9 s thinking plus the spoken explanation).
- Talking is push-to-talk (hold the key or click Talk). There is no always-open microphone.
- The app is a launcher entry plus a `tutor-click` command that run from this folder's `.venv`.
  It is not a single self-contained binary.
- A resumed lesson knows what was said, not what the screen looked like: the tutor looks again and
  decides. Only the most recent lesson is ever offered.
- Lesson records hold your requests and the tutor's words in plain text, readable by your user.
- Per-app bridges (Blender bpy, Unity MCP) are later work.
