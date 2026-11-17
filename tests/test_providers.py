"""Every provider, driven through a stand-in for its transport: a fake `claude` and `codex`
executable (real subprocesses), a local HTTP server for the OpenAI-style APIs, and a fake
Anthropic client. No real service is called.
"""
import gc
import json
import shutil
import stat
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from test_brain import answer

from tutor_click import config, providers
from tutor_click.brain import SCHEMA, SYSTEM, Tutor
from tutor_click.platform.base import Shot
from tutor_click.providers import ProviderError
from tutor_click.providers.base import classify
from tutor_click.providers.claude_code import ClaudeCode
from tutor_click.providers.openai_compat import LocalModel

SHOT = Shot(b"\xff\xd8 not really a jpeg", 1568, 656, 0.4558)

FAKE_CLI = f"""#!{sys.executable}
import json, os, sys, time
args, mode = sys.argv[1:], os.environ.get("FAKE_MODE", "ok")
name = os.path.basename(sys.argv[0])
if args[:2] == ["login", "status"]:
    sys.exit(1 if mode == "logged_out" else 0)
seen = {{"args": args, "stdin": sys.stdin.read(), "cwd": os.getcwd(), "pid": os.getpid()}}
if name == "codex":
    image, out = args[args.index("-i") + 1], args[args.index("-o") + 1]
    seen["image"] = open(image, "rb").read().decode("latin1")
    seen["image_path"] = image
    seen["schema"] = json.load(open(args[args.index("--output-schema") + 1]))
json.dump(seen, open(os.environ["FAKE_LOG"], "w"))
if mode == "hang":
    time.sleep(30)
if mode == "limit":
    if name == "codex":
        sys.stderr.write("ERROR: You've hit your usage limit. Try again later.\\n")
        sys.exit(1)
    print(json.dumps({{"type": "result", "is_error": True, "result": "Claude AI usage limit reached|1794950000"}}))
    sys.exit(1)
reply = os.environ["FAKE_ANSWER"]
if name == "codex":
    open(out, "w").write("not json" if mode == "garbage" else reply)
else:
    print(json.dumps({{"type": "system", "subtype": "init"}}))
    print(json.dumps({{"type": "result", "is_error": False, "structured_output": json.loads(reply)}}))
"""


@pytest.fixture
def cli(tmp_path, monkeypatch):
    """Fake `claude` and `codex` on disk. Returns a function that reads what the last call received."""
    for name in ("claude", "codex"):
        exe = tmp_path / name
        exe.write_text(FAKE_CLI)
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(shutil, "which", lambda name: str(tmp_path / name))
    monkeypatch.setattr(config, "RUNTIME", tmp_path / "run")
    (tmp_path / "run").mkdir()
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "seen.json"))
    monkeypatch.setenv("FAKE_ANSWER", json.dumps(answer()))
    yield lambda: json.loads((tmp_path / "seen.json").read_text())
    for obj in gc.get_objects():  # no process started ahead outlives its test
        if isinstance(obj, ClaudeCode):
            obj.close()


class FakeAPI(ThreadingHTTPServer):
    """A local HTTP server that answers like an OpenAI-style chat completions endpoint."""

    def __init__(self):
        super().__init__(("127.0.0.1", 0), self.Handler)
        self.status, self.body = 200, {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(answer())}}]}
        self.seen = None
        threading.Thread(target=self.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_address[1]}/v1"

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.server.seen = SimpleNamespace(path=self.path, auth=self.headers.get("Authorization"), body=body)
            self._reply()

        def do_GET(self):
            self._reply()

        def _reply(self):
            raw = self.server.body if isinstance(self.server.body, bytes) else json.dumps(self.server.body).encode()
            self.send_response(self.server.status)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass


@pytest.fixture
def api(monkeypatch):
    server = FakeAPI()
    for name in ("OPENAI_URL", "GEMINI_URL", "LOCAL_URL"):
        monkeypatch.setattr(config, name, server.url)
    monkeypatch.setattr(config, "GEMINI_MODEL", "gemini-test")
    monkeypatch.setattr(config, "LOCAL_MODEL", "llava-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")
    yield server
    server.shutdown()
    server.server_close()


class FakeAnthropic:
    """Stands in for anthropic.Anthropic(): remembers the request, returns a text block."""

    def __init__(self):
        self.request = None
        self.stop_reason = "end_turn"
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **request):
        self.request = request
        return SimpleNamespace(stop_reason=self.stop_reason, content=[SimpleNamespace(type="text", text=json.dumps(answer()))])


def make(provider_id):
    p = providers.create(provider_id)
    if provider_id == "api":
        p._client = FakeAnthropic()
    return p


# ---- one contract for all of them ---------------------------------------------
@pytest.mark.parametrize("provider_id", list(providers.PROVIDERS))
def test_every_provider_takes_the_same_request_and_yields_the_same_step(provider_id, cli, api):
    step = Tutor(make(provider_id)).step(SHOT, "pinta", "teach me the brush", "guide")
    assert (step.action, step.point, step.risk, step.repeat_safe, step.caption) == ("click", (100, 100), "read_only", True, "Pick the brush")


def test_every_provider_says_who_gets_the_screenshots_and_who_pays():
    for provider_id, cls in providers.PROVIDERS.items():
        assert cls.id == provider_id
        assert cls.billing in ("subscription", "api_key", "local")
        assert all(getattr(cls, field) for field in ("name", "menu", "paid_by", "sends_to"))
    assert [cls.billing for cls in providers.PROVIDERS.values()][:2] == ["subscription", "subscription"]  # subscriptions lead the menu


def test_unknown_provider_is_an_error_not_a_default():
    with pytest.raises(ProviderError) as e:
        providers.create("gpt")
    assert e.value.code == "unknown"


# ---- Codex --------------------------------------------------------------------
def test_codex_is_boxed_in_and_gets_instructions_schema_and_image(cli):
    providers.create("codex").ask(SYSTEM, SCHEMA, SHOT.jpeg, "Now: what do I click?")
    seen = cli()
    args = seen["args"]
    assert args[0] == "exec"
    for flag in ("--ephemeral", "--skip-git-repo-check", "--ignore-user-config"):
        assert flag in args
    assert args[args.index("-s") + 1] == "read-only"
    assert [args[i + 1] for i, a in enumerate(args) if a == "--disable"] == ["apps", "plugins", "browser_use", "computer_use"]
    assert 'model_reasoning_effort="low"' in args
    assert "-m" not in args  # no model set: Codex's own default
    assert seen["cwd"] == args[args.index("-C") + 1] and seen["cwd"].endswith("/cwd")
    assert seen["cwd"].startswith(str(config.RUNTIME / "codex"))
    assert seen["stdin"].startswith(SYSTEM) and seen["stdin"].endswith("Now: what do I click?")
    assert seen["schema"] == SCHEMA
    assert seen["image"].encode("latin1") == SHOT.jpeg


def test_codex_model_and_effort_come_from_the_settings(cli, monkeypatch):
    monkeypatch.setattr(config, "CODEX_MODEL", "gpt-test")
    monkeypatch.setattr(config, "CODEX_EFFORT", "medium")
    providers.create("codex").ask(SYSTEM, SCHEMA, SHOT.jpeg, "x")
    args = cli()["args"]
    assert args[args.index("-m") + 1] == "gpt-test"
    assert 'model_reasoning_effort="medium"' in args


def test_claude_code_is_asked_for_the_model_and_effort_in_use(cli):
    p = providers.create("claude-code")
    assert (p.model, p.effort) == (config.MODEL, config.EFFORT)
    p.model, p.effort = "claude-haiku-5-5", "low"
    p.ask(SYSTEM, SCHEMA, SHOT.jpeg, "x")
    args = cli()["args"]
    assert (args[args.index("--model") + 1], args[args.index("--effort") + 1]) == ("claude-haiku-5-5", "low")


def test_claude_code_starts_the_next_steps_process_ahead_and_uses_it(cli):
    p = providers.create("claude-code")
    assert p._ready is None  # nothing is started before the first question
    p.ask(SYSTEM, SCHEMA, SHOT.jpeg, "one")
    first = cli()["pid"]
    waiting = p._ready[1]
    assert waiting.poll() is None and waiting.pid != first  # started, waiting for its input
    assert p.ask(SYSTEM, SCHEMA, SHOT.jpeg, "two") == answer()
    seen = cli()
    assert seen["pid"] == waiting.pid  # the second question went to the one that was waiting
    assert seen["stdin"].count("two") == 1 and "one" not in seen["stdin"]  # and it knows nothing of the first
    assert p._ready[1].pid != waiting.pid and p._ready[1].poll() is None


def test_a_waiting_process_for_another_model_is_not_used(cli):
    p = providers.create("claude-code")
    p.ask(SYSTEM, SCHEMA, SHOT.jpeg, "one")
    stale = p._ready[1]
    p.model = "claude-opus-5-5"
    p.ask(SYSTEM, SCHEMA, SHOT.jpeg, "two")
    seen = cli()
    assert seen["pid"] != stale.pid and stale.poll() is not None  # stopped, never asked
    assert seen["args"][seen["args"].index("--model") + 1] == "claude-opus-5-5"


def test_a_waiting_process_that_died_is_replaced_without_the_user_noticing(cli):
    p = providers.create("claude-code")
    p.ask(SYSTEM, SCHEMA, SHOT.jpeg, "one")
    p._ready[1].kill()
    p._ready[1].wait()
    assert p.ask(SYSTEM, SCHEMA, SHOT.jpeg, "two") == answer()


def test_interrupting_or_closing_leaves_no_claude_process_behind(cli):
    p = providers.create("claude-code")
    p.ask(SYSTEM, SCHEMA, SHOT.jpeg, "one")
    waiting = p._ready[1]
    p.abort()
    assert waiting.poll() is not None and p._ready is None
    p.ask(SYSTEM, SCHEMA, SHOT.jpeg, "two")
    waiting = p._ready[1]
    p.close()
    assert waiting.poll() is not None and p._ready is None
    p.close()  # twice is fine


def test_the_claude_api_is_asked_for_the_model_and_effort_in_use_and_haiku_gets_no_fallback(api):
    p = make("api")
    p.model, p.effort = "claude-sonnet-5-5", "low"
    p.ask(SYSTEM, SCHEMA, SHOT.jpeg, "x")
    sent = p._client.request
    assert (sent["model"], sent["output_config"]["effort"], sent["fallbacks"]) == ("claude-sonnet-5-5", "low", "default")
    p.model = "claude-haiku-5-5"
    p.ask(SYSTEM, SCHEMA, SHOT.jpeg, "x")
    assert p._client.request["model"] == "claude-haiku-5-5"
    assert "fallbacks" not in p._client.request and "betas" not in p._client.request


def test_every_provider_says_what_can_be_chosen():
    for cls in providers.PROVIDERS.values():
        assert all(isinstance(m, tuple) and len(m) == 2 for m in cls.models)
        assert cls.model_env.startswith("TUTOR_CLICK_")
        p = cls()
        assert not cls.models or p.model in dict(cls.models)  # the default is one of the ones on offer
        assert (p.effort in cls.efforts) if cls.efforts else p.effort == ""
    assert [m for m, _ in providers.PROVIDERS["claude-code"].models] == ["claude-fable-5-2", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]


def test_the_screenshot_does_not_outlive_the_codex_call(cli, monkeypatch):
    providers.create("codex").ask(SYSTEM, SCHEMA, SHOT.jpeg, "x")
    assert list((config.RUNTIME / "codex").iterdir()) == []
    monkeypatch.setenv("FAKE_MODE", "limit")
    with pytest.raises(ProviderError):
        providers.create("codex").ask(SYSTEM, SCHEMA, SHOT.jpeg, "x")
    assert list((config.RUNTIME / "codex").iterdir()) == []


def test_codex_calls_that_overlap_do_not_share_files(cli):
    p = providers.create("codex")
    results = []
    threads = [threading.Thread(target=lambda: results.append(p.ask(SYSTEM, SCHEMA, SHOT.jpeg, "x"))) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert results == [answer()] * 6


@pytest.mark.parametrize(("provider_id", "mode", "code"), [
    ("codex", "limit", "rate_limited"), ("codex", "garbage", "invalid_answer"), ("claude-code", "limit", "rate_limited"),
])
def test_cli_failures_are_named(provider_id, mode, code, cli, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", mode)
    with pytest.raises(ProviderError) as e:
        providers.create(provider_id).ask(SYSTEM, SCHEMA, SHOT.jpeg, "x")
    assert e.value.code == code


@pytest.mark.parametrize("provider_id", ["codex", "claude-code"])
def test_abort_kills_a_call_in_flight(provider_id, cli, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "hang")
    p = providers.create(provider_id)
    errors = []
    t = threading.Thread(target=lambda: errors.append(pytest.raises(ProviderError, p.ask, SYSTEM, SCHEMA, SHOT.jpeg, "x")))
    t0 = time.monotonic()
    t.start()
    while p._proc is None and time.monotonic() - t0 < 5:
        time.sleep(0.01)
    p.abort()
    t.join(5)
    assert not t.is_alive() and errors
    assert time.monotonic() - t0 < 5  # not the 30 s the fake would have slept


def test_health_reports_a_missing_or_signed_out_client(cli, monkeypatch, tmp_path):
    assert providers.create("codex").health() is None
    monkeypatch.setenv("FAKE_MODE", "logged_out")
    assert "not signed in" in providers.create("codex").health()
    (tmp_path / "codex").unlink()
    (tmp_path / "claude").unlink()
    assert "not installed" in providers.create("codex").health()
    assert "not installed" in providers.create("claude-code").health()


# ---- Claude Code -----------------------------------------------------------------
def test_claude_code_runs_without_tools_or_user_settings(cli):
    providers.create("claude-code").ask(SYSTEM, SCHEMA, SHOT.jpeg, "Now: what do I click?")
    seen = cli()
    args = seen["args"]
    assert args[args.index("--tools") + 1] == ""
    assert args[args.index("--setting-sources") + 1] == ""
    assert "--strict-mcp-config" in args and "--no-session-persistence" in args
    assert args[args.index("--system-prompt") + 1] == SYSTEM
    assert json.loads(args[args.index("--json-schema") + 1]) == SCHEMA
    content = json.loads(seen["stdin"])["message"]["content"]
    assert [c["type"] for c in content] == ["image", "text"]
    assert content[1]["text"] == "Now: what do I click?"


# ---- OpenAI-style APIs -------------------------------------------------------------
def test_openai_request_carries_key_model_instructions_image_and_schema(api):
    providers.create("openai").ask(SYSTEM, SCHEMA, SHOT.jpeg, "Now: what do I click?")
    seen = api.seen
    assert seen.path == "/v1/chat/completions"
    assert seen.auth == "Bearer sk-test-openai"
    assert seen.body["model"] == "gpt-6.2-astra"
    system, user = seen.body["messages"]
    assert (system["role"], system["content"]) == ("system", SYSTEM)
    assert user["content"][0] == {"type": "text", "text": "Now: what do I click?"}
    assert user["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert seen.body["response_format"] == {"type": "json_schema", "json_schema": {"name": "step", "strict": True, "schema": SCHEMA}}


def test_gemini_uses_its_own_key_and_the_local_model_sends_none(api):
    providers.create("gemini").ask(SYSTEM, SCHEMA, SHOT.jpeg, "x")
    assert (api.seen.auth, api.seen.body["model"]) == ("Bearer test-gemini", "gemini-test")
    providers.create("local").ask(SYSTEM, SCHEMA, SHOT.jpeg, "x")
    assert (api.seen.auth, api.seen.body["model"]) == (None, "llava-test")


@pytest.mark.parametrize(("status", "body", "code"), [
    (429, {"error": {"message": "Rate limit reached"}}, "rate_limited"),
    (401, {"error": {"message": "Incorrect API key"}}, "needs_login"),
    (500, {"error": {"message": "boom"}}, "failed"),
    (200, {"choices": [{"finish_reason": "stop", "message": {"content": None, "refusal": "I can't help with that."}}]}, "declined"),
    (200, {"choices": [{"finish_reason": "length", "message": {"content": '{"say": "The'}}]}, "invalid_answer"),
    (200, {"choices": [{"finish_reason": "stop", "message": {"content": "Sure! Click the brush."}}]}, "invalid_answer"),
    (200, {"choices": []}, "invalid_answer"),
    (200, b"<html>gateway</html>", "invalid_answer"),
])
def test_api_failures_are_named(status, body, code, api):
    api.status, api.body = status, body
    with pytest.raises(ProviderError) as e:
        providers.create("openai").ask(SYSTEM, SCHEMA, SHOT.jpeg, "x")
    assert e.value.code == code


def test_an_unreachable_api_is_offline(api, monkeypatch):
    monkeypatch.setattr(config, "OPENAI_URL", "http://127.0.0.1:9/v1")  # nothing listens on the discard port
    with pytest.raises(ProviderError) as e:
        providers.create("openai").ask(SYSTEM, SCHEMA, SHOT.jpeg, "x")
    assert e.value.code == "offline"


def test_health_names_what_is_missing(api, monkeypatch):
    assert providers.create("openai").health() is None
    monkeypatch.delenv("OPENAI_API_KEY")
    assert providers.create("openai").health() == "no OPENAI_API_KEY is set"
    monkeypatch.setattr(config, "GEMINI_MODEL", "")
    assert providers.create("gemini").health() == "no model is chosen (set TUTOR_CLICK_GEMINI_MODEL)"
    assert providers.create("local").health() is None  # the fake server answers /models
    monkeypatch.setattr(config, "LOCAL_URL", "http://127.0.0.1:9/v1")
    assert "no model server is answering" in LocalModel().health()
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert providers.create("api").health() == "no ANTHROPIC_API_KEY is set"


# ---- Anthropic API ---------------------------------------------------------------
def test_anthropic_api_request_and_a_refusal():
    p = make("api")
    p.ask(SYSTEM, SCHEMA, SHOT.jpeg, "Now: what do I click?")
    request = p._client.request
    assert request["system"] == SYSTEM
    assert request["output_config"]["format"] == {"type": "json_schema", "schema": SCHEMA}
    assert [c["type"] for c in request["messages"][0]["content"]] == ["image", "text"]
    p._client.stop_reason = "refusal"
    with pytest.raises(ProviderError) as e:
        p.ask(SYSTEM, SCHEMA, SHOT.jpeg, "x")
    assert e.value.code == "declined"


def test_classify():
    assert classify("ERROR: You've hit your usage limit.") == "rate_limited"
    assert classify("Claude AI usage limit reached|1794950000") == "rate_limited"
    assert classify("Not logged in. Please run /login") == "needs_login"
    assert classify("error sending request: connection refused") == "offline"
    assert classify("exit 2") == "failed"
