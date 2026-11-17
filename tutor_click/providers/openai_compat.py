"""Services that speak the OpenAI Chat Completions API: OpenAI itself, Gemini, and local models.

Checked against OpenAI's structured-outputs guide on 2026-11-17 (json_schema response format).
Not checked against a live service: no API key is set on this machine and no local model server
is installed. Gemini's compatible endpoint address is from memory; its documentation page would
not load. TUTOR_CLICK_GEMINI_URL overrides it.
"""
import base64
import json
import os
import urllib.error
import urllib.request

from .. import config
from .base import ProviderError

TIMEOUT = 120


class OpenAICompatible:
    name = ""
    key_env = ""  # environment variable holding the API key; "" means no key is needed
    model_env = ""
    url = ""
    model = ""

    def health(self) -> str | None:
        if not self.model:
            return f"no model is chosen (set {self.model_env})"
        if self.key_env and not os.environ.get(self.key_env):
            return f"no {self.key_env} is set"
        return None

    def abort(self) -> None:
        pass  # the HTTP call cannot be interrupted; its answer is dropped by the daemon

    def ask(self, system: str, schema: dict, jpeg: bytes, prompt: str) -> dict:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.standard_b64encode(jpeg).decode()}},
                ]},
            ],
            "response_format": {"type": "json_schema", "json_schema": {"name": "step", "strict": True, "schema": schema}},
        }
        headers = {"Content-Type": "application/json"}
        if self.key_env:
            headers["Authorization"] = f"Bearer {os.environ.get(self.key_env, '')}"
        req = urllib.request.Request(f"{self.url.rstrip('/')}/chat/completions", data=json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                data = json.load(resp)
        except urllib.error.HTTPError as e:
            detail = e.read(600).decode(errors="replace")
            code = {401: "needs_login", 403: "needs_login", 429: "rate_limited"}.get(e.code, "failed")
            raise ProviderError(f"{self.name} returned error {e.code}.", code) from RuntimeError(detail)
        except TimeoutError as e:
            raise ProviderError(f"{self.name} took too long to answer.", "timeout") from e
        except (urllib.error.URLError, OSError) as e:
            raise ProviderError(f"I can't reach {self.name}.", "offline") from e
        except json.JSONDecodeError as e:
            raise ProviderError(f"{self.name} did not answer in the expected form.", "invalid_answer") from e
        try:
            choice = data["choices"][0]
            message = choice["message"]
            if message.get("refusal"):
                raise ProviderError(f"{self.name} declined that request.", "declined")
            if choice.get("finish_reason") == "length":
                raise ProviderError("The answer was cut off.", "invalid_answer")
            return json.loads(message["content"])
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
            raise ProviderError(f"{self.name} did not answer in the expected form.", "invalid_answer") from e


class OpenAIAPI(OpenAICompatible):
    id = "openai"
    name = "the OpenAI API"
    menu = "OpenAI API · pay per use"
    billing = "api_key"
    paid_by = "your OpenAI API key, billed per use and separately from any ChatGPT plan"
    sends_to = "OpenAI"
    key_env = "OPENAI_API_KEY"
    model_env = "TUTOR_CLICK_OPENAI_MODEL"

    def __init__(self):
        self.url, self.model = config.OPENAI_URL, config.OPENAI_MODEL


class GeminiAPI(OpenAICompatible):
    id = "gemini"
    name = "Gemini"
    menu = "Gemini API · pay per use"
    billing = "api_key"
    paid_by = "your Gemini API key, billed by Google"
    sends_to = "Google"
    key_env = "GEMINI_API_KEY"
    model_env = "TUTOR_CLICK_GEMINI_MODEL"

    def __init__(self):
        self.url, self.model = config.GEMINI_URL, config.GEMINI_MODEL


class LocalModel(OpenAICompatible):
    id = "local"
    name = "the local model"
    menu = "Local model · on this computer"
    billing = "local"
    paid_by = "nobody, it runs on this computer"
    sends_to = "this computer only"
    model_env = "TUTOR_CLICK_LOCAL_MODEL"

    def __init__(self):
        self.url, self.model = config.LOCAL_URL, config.LOCAL_MODEL

    def health(self) -> str | None:
        problem = super().health()
        if problem:
            return problem
        try:
            urllib.request.urlopen(f"{self.url.rstrip('/')}/models", timeout=1).close()
        except OSError:
            return f"no model server is answering at {self.url}"
        return None
