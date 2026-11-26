"""Execution trace: tool-level outcomes only (never hidden reasoning, never secrets)."""
import logging
import time
from contextlib import contextmanager
from typing import Any, Iterator

from ..schemas.api import TraceStep

log = logging.getLogger(__name__)


class StepHandle:
    def __init__(self, step: TraceStep):
        self._s = step

    def ok(self, summary: str, **detail: Any) -> None:
        self._set("success", summary, detail)

    def warn(self, summary: str, **detail: Any) -> None:
        self._set("warning", summary, detail)

    def fail(self, summary: str, **detail: Any) -> None:
        self._set("failed", summary, detail)

    def _set(self, status: str, summary: str, detail: dict) -> None:
        self._s.status, self._s.summary = status, summary  # type: ignore[assignment]
        self._s.detail.update(detail)


class Trace:
    def __init__(self) -> None:
        self.steps: list[TraceStep] = []

    @contextmanager
    def step(self, name: str) -> Iterator[StepHandle]:
        s = TraceStep(id=len(self.steps) + 1, name=name, detail={})
        self.steps.append(s)
        t0 = time.monotonic()
        try:
            yield StepHandle(s)
            if s.status == "running":
                s.status = "success"
        except Exception as e:
            s.status, s.summary = "failed", f"{type(e).__name__}: {e}"
            log.exception("trace step failed: %s", name)
            raise
        finally:
            s.duration_ms = int((time.monotonic() - t0) * 1000)
