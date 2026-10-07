"""
LLM Client — a small provider abstraction with structured JSON output and a
disk cache.

``AnthropicClient`` calls Claude through the official SDK using structured
outputs (``output_config.format`` with a JSON schema), so every response is
guaranteed to be valid JSON for the agent that asked.  Responses are cached on
disk keyed by (model, effort, system, prompt, schema): re-running a phase is
free and reproducible.

``MockClient`` returns deterministic responses for tests and dry runs.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import tempfile
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

# USD per million tokens (input, output) — keep in sync with Anthropic pricing.
_PRICING: dict[str, tuple[float, float]] = {}  # Supply verified prices before estimating USD.


class LLMError(RuntimeError):
    """Raised when the model refuses, truncates, or returns unusable output."""


class LLMUnavailableError(LLMError):
    """Authentication or exhausted quota: stop further requests in this run."""


@dataclass
class Usage:
    calls: int = 0
    cached_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, input_tokens: int, output_tokens: int) -> None:
        with self._lock:
            self.calls += 1
            self.input_tokens += input_tokens
            self.output_tokens += output_tokens

    def add_cached(self) -> None:
        with self._lock:
            self.cached_calls += 1

    def cost_usd(self, model: str) -> float:
        price_in, price_out = _PRICING.get(model, (0.0, 0.0))
        return (self.input_tokens * price_in + self.output_tokens * price_out) / 1_000_000

    def summary(self, model: str) -> str:
        cost = f"~ ${self.cost_usd(model):.2f}" if model in _PRICING else "cost unavailable; see provider billing"
        return (f"{self.calls} API call(s), {self.cached_calls} cache hit(s), "
                f"{self.input_tokens:,} in / {self.output_tokens:,} out tokens, "
                f"{cost}")


class LLMClient(Protocol):
    model: str
    usage: Usage

    def complete_json(self, system: str, prompt: str, schema: dict,
                      max_tokens: int = 16000) -> dict[str, Any]:
        ...


class _DiskCache:
    _io_lock = threading.Lock()
    def __init__(self, directory: str | None) -> None:
        self.directory = directory
        if directory:
            os.makedirs(directory, exist_ok=True)

    @staticmethod
    def key(*parts: Any) -> str:
        blob = json.dumps(parts, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def get(self, key: str) -> dict | None:
        if not self.directory:
            return None
        path = os.path.join(self.directory, f"{key}.json")
        if not os.path.isfile(path):
            return None
        try:
            with self._io_lock:
                with open(path, encoding="utf-8") as fh:
                    return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None

    def put(self, key: str, value: dict) -> None:
        if not self.directory:
            return
        path = os.path.join(self.directory, f"{key}.json")
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.directory,
                                         suffix=".tmp", delete=False) as fh:
            tmp = fh.name
            json.dump(value, fh, indent=1, ensure_ascii=False)
        try:
            with self._io_lock:
                os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)


def _validate(data: Any, schema: dict) -> dict:
    from jsonschema import ValidationError, validate

    try:
        validate(data, schema)
    except ValidationError as exc:
        raise LLMError(f"Response does not match the requested schema: {exc.message}") from exc
    return data


class OpenAIClient:
    """OpenAI structured output, bounded retries, schema validation and disk caching."""

    def __init__(self, model: str, effort: str = "medium", cache_dir: str | None = None,
                 base_url: str | None = None, api_key: str | None = None,
                 provider: str = "openai"):
        from openai import OpenAI

        kwargs: dict[str, Any] = {"timeout": 120, "max_retries": 2}
        if base_url:
            kwargs["base_url"] = base_url
        if api_key:
            kwargs["api_key"] = api_key
        self._client = OpenAI(**kwargs)
        self.provider = provider
        self.model, self.effort = model, effort
        self.usage = Usage()
        self._cache = _DiskCache(cache_dir)

    def complete_json(self, system: str, prompt: str, schema: dict,
                      max_tokens: int = 16000) -> dict[str, Any]:
        from openai import OpenAIError

        key = self._cache.key("openai", str(self._client.base_url), self.model,
                              self.effort, system, prompt, schema, max_tokens)
        cached = self._cache.get(key)
        if cached is not None:
            self.usage.add_cached()
            return _validate(cached, schema)
        kwargs = dict(model=self.model, max_completion_tokens=max_tokens,
                      messages=[{"role": "system", "content": system},
                                {"role": "user", "content": prompt}],
                      response_format={"type": "json_schema", "json_schema": {
                          "name": "technical_debt_result", "strict": True, "schema": schema}})
        if self.provider == "openai":
            kwargs["store"] = False
        if self.model.startswith(("gpt-5", "o3", "o4")):
            kwargs["reasoning_effort"] = self.effort
        else:
            kwargs["temperature"] = 0
        try:
            response = self._client.chat.completions.create(**kwargs)
        except OpenAIError as exc:
            body = getattr(exc, "body", {}) or {}
            if isinstance(body, dict) and (body.get("code") in ("credit_balance_exhausted", "insufficient_quota")
                                          or body.get("type") == "insufficient_quota"):
                raise LLMUnavailableError("OpenAI API credits are exhausted. Add credits or configure a funded key in .env.") from exc
            if getattr(exc, "status_code", None) in (401, 403):
                raise LLMUnavailableError("OpenAI authentication/access failed. Check the configured API key and model access.") from exc
            raise LLMError(f"{self.provider.capitalize()} request failed ({type(exc).__name__}); check credentials, model and quota.") from exc
        if response.usage:
            self.usage.add(response.usage.prompt_tokens, response.usage.completion_tokens)
        choice = response.choices[0]
        if choice.finish_reason != "stop" or choice.message.refusal:
            raise LLMError(f"No complete usable response (finish_reason={choice.finish_reason}).")
        try:
            data = _validate(json.loads(choice.message.content or ""), schema)
        except json.JSONDecodeError as exc:
            raise LLMError("The model returned invalid JSON.") from exc
        self._cache.put(key, data)
        return data


class AnthropicClient:
    """Claude via the official ``anthropic`` SDK, with structured JSON output."""

    def __init__(self, model: str, effort: str = "medium", cache_dir: str | None = None,
                 use_fallbacks: bool = False) -> None:
        import anthropic

        self._anthropic = anthropic
        # Credentials resolve from ANTHROPIC_API_KEY (loaded from .env by config).
        self._client = anthropic.Anthropic(max_retries=2, timeout=120)
        self.model = model
        self.effort = effort
        self.use_fallbacks = use_fallbacks
        self.usage = Usage()
        self._cache = _DiskCache(cache_dir)

    def complete_json(self, system: str, prompt: str, schema: dict,
                      max_tokens: int = 16000) -> dict[str, Any]:
        key = self._cache.key(self.model, self.effort, system, prompt, schema)
        cached = self._cache.get(key)
        if cached is not None:
            self.usage.add_cached()
            return cached

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
            "output_config": {
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": schema},
            },
        }
        try:
            response = self._client.messages.create(**kwargs)
        except self._anthropic.APIError as exc:
            raise LLMError(f"Anthropic request failed ({type(exc).__name__}); check credentials, model and quota.") from exc

        self.usage.add(response.usage.input_tokens, response.usage.output_tokens)
        if response.stop_reason == "refusal":
            raise LLMError(f"Model refused the request (request id {response._request_id}).")
        if response.stop_reason == "max_tokens":
            raise LLMError("Response truncated at max_tokens.")
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError(f"Invalid JSON from model: {text[:200]}") from exc
        _validate(data, schema)
        self._cache.put(key, data)
        return data


class MockClient:
    """Deterministic stand-in used by tests and ``--dry-run``."""

    def __init__(self, responder: Callable[[str, str, dict], dict], model: str = "mock") -> None:
        self.responder = responder
        self.model = model
        self.usage = Usage()
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, system: str, prompt: str, schema: dict,
                      max_tokens: int = 16000) -> dict[str, Any]:
        self.calls.append((system, prompt))
        self.usage.add(len(system + prompt) // 4, 200)
        return self.responder(system, prompt, schema)


GROQ_BASE_URL = "https://api.groq.com/openai/v1"


def make_client(model: str, effort: str, cache_dir: str | None) -> LLMClient:
    """Build the real client, failing early with a clear message if no key is set.

    Model prefixes: ``claude-*`` → Anthropic, ``groq/<model>`` → Groq's
    OpenAI-compatible endpoint (GROQ_API_KEY), anything else → OpenAI.
    """
    if model.startswith("groq/"):
        if not os.environ.get("GROQ_API_KEY"):
            raise SystemExit("[error] GROQ_API_KEY is required for groq/* models. Set it in .env.")
        return OpenAIClient(model=model[len("groq/"):], effort=effort, cache_dir=cache_dir,
                            base_url=GROQ_BASE_URL, api_key=os.environ["GROQ_API_KEY"],
                            provider="groq")
    if not model.startswith("claude-"):
        if not os.environ.get("OPENAI_API_KEY"):
            raise SystemExit("[error] OPENAI_API_KEY is required for the selected model. Set it in .env.")
        return OpenAIClient(model=model, effort=effort, cache_dir=cache_dir)
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise SystemExit(
            "[error] No Anthropic credentials found.\n"
            "        Create a .env file in the project root containing:\n"
            "            ANTHROPIC_API_KEY=<your key>\n"
            "        (it is git-ignored), or run with --dry-run to preview prompts and cost.")
    return AnthropicClient(model=model, effort=effort, cache_dir=cache_dir)
