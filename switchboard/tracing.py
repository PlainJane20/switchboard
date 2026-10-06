"""Optional OpenTelemetry spans for Switchboard.

Off by default and zero behaviour change: ``opentelemetry-api`` is imported
lazily, and if it is missing (or no SDK TracerProvider is configured, in which
case the API hands back non-recording spans) every ``span()`` is a no-op.

Attribute policy: identifiers, counts, enums, numbers and durations only.
Never ticket titles/bodies, prompts, command lines or secrets. Exception
messages are not recorded either, only the exception type name.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Optional

_provider: Any = None
_MAX_STR = 120


class _NoopSpan:
    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def is_recording(self) -> bool:
        return False


_NOOP = _NoopSpan()


def configure(provider: Any) -> None:
    """Use `provider` for this package's spans (None restores the global default)."""
    global _provider
    _provider = provider


def _api():
    """Return the opentelemetry.trace module, or None if the API is absent."""
    try:
        from opentelemetry import trace
    except Exception:  # ImportError, or a broken install: tracing just stays off
        return None
    return trace


def _clean(v: Any) -> Any:
    if isinstance(v, bool) or isinstance(v, (int, float)):
        return v
    if isinstance(v, (list, tuple)):
        return [str(x)[:_MAX_STR] for x in v]
    return str(v)[:_MAX_STR]


def set_attrs(s: Any, **attrs: Any) -> None:
    for k, v in attrs.items():
        if v is not None:
            s.set_attribute(k, _clean(v))


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Any]:
    """Open a span named ``switchboard.<name>``; yields an object with set_attribute()."""
    trace = _api()
    if trace is None:
        yield _NOOP
        return
    tracer = (
        trace.get_tracer("switchboard", tracer_provider=_provider)
        if _provider is not None
        else trace.get_tracer("switchboard")
    )
    with tracer.start_as_current_span(
        f"switchboard.{name}", record_exception=False, set_status_on_exception=False
    ) as s:
        set_attrs(s, **attributes)
        try:
            yield s
        except BaseException as e:
            # Type name only: messages can echo user content.
            set_attrs(s, **{"error.type": type(e).__name__})
            try:
                from opentelemetry.trace import Status, StatusCode

                s.set_status(Status(StatusCode.ERROR, type(e).__name__))
            except Exception:
                pass
            raise
