"""The Anthropic API with the user's own key. Billed per use, separately from any subscription."""
import base64
import json
import os

import anthropic

from .. import config
from .base import ProviderError


class AnthropicAPI:
    id = "api"
    name = "the Claude API"
    menu = "Claude API · pay per use"
    billing = "api_key"
    paid_by = "your Anthropic API key, billed per use and separately from any subscription"
    sends_to = "Anthropic"

    def __init__(self):
        self.model = config.MODEL
        self._client = None

    def health(self) -> str | None:
        return None if os.environ.get("ANTHROPIC_API_KEY") else "no ANTHROPIC_API_KEY is set"

    def abort(self) -> None:
        pass  # the HTTP call cannot be interrupted; its answer is dropped by the daemon

    def ask(self, system: str, schema: dict, jpeg: bytes, prompt: str) -> dict:
        image = {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.standard_b64encode(jpeg).decode()}}
        try:
            if self._client is None:
                self._client = anthropic.Anthropic()
            resp = self._client.beta.messages.create(
                model=config.MODEL,
                max_tokens=8000,
                system=system,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                output_config={"effort": config.EFFORT, "format": {"type": "json_schema", "schema": schema}},
                messages=[{"role": "user", "content": [image, {"type": "text", "text": prompt}]}],
            )
        except TypeError as e:
            # The SDK raises a bare TypeError when no credential source resolves at all.
            if "authentication method" not in str(e):
                raise
            raise ProviderError("I have no Anthropic credentials. Set an API key or run ant auth login.", "needs_login") from e
        except anthropic.AuthenticationError as e:
            raise ProviderError("My Anthropic credentials were rejected.", "needs_login") from e
        except anthropic.RateLimitError as e:
            raise ProviderError("The Claude API is rate limiting me.", "rate_limited") from e
        except anthropic.APIConnectionError as e:
            raise ProviderError("I can't reach the Claude API.", "offline") from e
        except anthropic.APIStatusError as e:
            raise ProviderError(f"The Claude API returned error {e.status_code}.") from e

        if resp.stop_reason == "refusal":
            raise ProviderError("Claude declined that request.", "declined")
        if resp.stop_reason == "max_tokens":
            raise ProviderError("The answer was cut off.", "invalid_answer")
        text = next((b.text for b in resp.content if b.type == "text"), None)
        if text is None:
            raise ProviderError("Claude returned no answer.", "invalid_answer")
        return json.loads(text)
