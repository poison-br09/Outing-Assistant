"""NVIDIA Nemotron client (OpenAI-compatible endpoint).

Bounded timeout + retries, strict Pydantic validation of every response, and
per-provider metrics. Callers use `try_llm`, which never raises: on failure it
returns `(None, LLMInfo(fallback_reason=...))` so the caller can run its
deterministic fallback and disclose the reduced AI functionality.
"""
import logging
import re
import threading
import time
from collections import defaultdict
from typing import Optional, TypeVar

from openai import APIError, APITimeoutError, AuthenticationError, OpenAI
from pydantic import BaseModel, ValidationError

from ..config import Settings, get_settings
from ..schemas.api import LLMInfo

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)
PROVIDER = "nvidia"

_metrics_lock = threading.Lock()
_metrics: dict[str, int] = defaultdict(int)


def get_metrics() -> dict[str, float]:
    with _metrics_lock:
        calls = _metrics["calls"]
        return {
            "provider": PROVIDER,
            "calls": calls,
            "successes": _metrics["successes"],
            "timeouts": _metrics["timeouts"],
            "api_errors": _metrics["api_errors"],
            "invalid_outputs": _metrics["invalid_outputs"],
            "avg_latency_ms": round(_metrics["latency_ms"] / calls, 1) if calls else 0.0,
        }


def _bump(key: str, n: int = 1) -> None:
    with _metrics_lock:
        _metrics[key] += n


class LLMUnavailable(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def extract_json(text: str) -> str:
    """Strip reasoning blocks / code fences and return the outermost JSON object."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in response")
    return text[start : end + 1]


class LLMClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client: Optional[OpenAI] = None

    @property
    def enabled(self) -> bool:
        return self.settings.llm_enabled

    def _chat(self, system: str, user: str) -> str:
        if self._client is None:
            self._client = OpenAI(
                api_key=self.settings.nvidia_api_key,
                base_url=self.settings.nvidia_base_url,
                timeout=self.settings.llm_request_timeout_seconds,
                max_retries=0,  # retries are ours, so they stay bounded and traced
            )
        log.info("LLM request model=%s system=%.1500s user=%.3000s", self.settings.nvidia_nemotron_model, system, user)
        resp = self._client.chat.completions.create(
            model=self.settings.nvidia_nemotron_model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.2,
        )
        out = resp.choices[0].message.content or ""
        log.info("LLM response usage=%s content=%.3000s", getattr(resp, "usage", None), out)
        return out

    def complete_json(self, system: str, user: str, schema: type[T]) -> tuple[T, LLMInfo]:
        if not self.enabled:
            raise LLMUnavailable("not_configured")
        reason = "unknown"
        attempts = self.settings.llm_max_retries + 1
        for attempt in range(1, attempts + 1):
            t0 = time.monotonic()
            _bump("calls")
            try:
                text = self._chat(system, user)
                data = schema.model_validate_json(extract_json(text))
            except APITimeoutError:
                reason = "timeout"
                _bump("timeouts")
            except AuthenticationError:
                _bump("api_errors")
                raise LLMUnavailable("auth_error")  # retrying cannot help
            except APIError:
                reason = "api_error"
                _bump("api_errors")
            except (ValidationError, ValueError):
                reason = "invalid_output"
                _bump("invalid_outputs")
            else:
                ms = int((time.monotonic() - t0) * 1000)
                _bump("successes")
                _bump("latency_ms", ms)
                return data, LLMInfo(
                    used=True, model=self.settings.nvidia_nemotron_model, attempts=attempt, latency_ms=ms
                )
            _bump("latency_ms", int((time.monotonic() - t0) * 1000))
            log.warning("LLM attempt %d/%d failed: %s (%dms)", attempt, attempts, reason,
                        (time.monotonic() - t0) * 1000)
        raise LLMUnavailable(reason)


_client: Optional[LLMClient] = None


def get_llm() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient(get_settings())
    return _client


def try_llm(system: str, user: str, schema: type[T]) -> tuple[Optional[T], LLMInfo]:
    try:
        return get_llm().complete_json(system, user, schema)
    except LLMUnavailable as e:
        return None, LLMInfo(used=False, fallback_reason=e.reason)
