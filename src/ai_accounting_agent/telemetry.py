from __future__ import annotations

import dataclasses
import hashlib
import inspect
import json
import logging
import time
from collections.abc import Iterable, Mapping
from functools import wraps
from typing import Any, Callable, TypeVar, cast

try:
    import google.cloud.logging
    from google.cloud.logging.handlers import CloudLoggingHandler
except ModuleNotFoundError:  # pragma: no cover - optional local dependency
    google = None
    CloudLoggingHandler = None


LOG_NAME = "tripletex-agent"
MAX_TEXT_PREVIEW = 1000
MAX_LIST_ITEMS = 50
REDACTED = "[REDACTED]"
SENSITIVE_KEYS = {
    "authorization",
    "api_key",
    "x-api-key",
    "session_token",
    "password",
    "token",
    "access_token",
    "content_base64",
}

_logger: logging.Logger | None = None
_structured_logger = None

F = TypeVar("F", bound=Callable[..., Any])


def preview_text(text: str, limit: int = MAX_TEXT_PREVIEW) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit]}... (truncated, len={len(normalized)})"


def sha256_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in SENSITIVE_KEYS or lowered.endswith("_token") or lowered.endswith("_api_key")


def _redact_string(value: str, key: str | None = None) -> str:
    if key and _is_sensitive_key(key):
        return REDACTED
    lowered = value.lower()
    if lowered.startswith("basic ") or lowered.startswith("bearer "):
        return REDACTED
    return preview_text(value)


def serialize_for_logging(value: Any, *, key: str | None = None) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value

    if isinstance(value, str):
        return _redact_string(value, key=key)

    if isinstance(value, bytes):
        return {
            "type": "bytes",
            "size": len(value),
            "sha256": sha256_digest(value),
        }

    if dataclasses.is_dataclass(value):
        return serialize_for_logging(dataclasses.asdict(value), key=key)

    if hasattr(value, "model_dump"):
        try:
            return serialize_for_logging(value.model_dump(mode="python"), key=key)
        except Exception:
            return repr(value)

    if isinstance(value, Mapping):
        return {
            str(item_key): serialize_for_logging(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }

    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray)):
        items = list(value)[:MAX_LIST_ITEMS]
        return [serialize_for_logging(item) for item in items]

    return repr(value)


def build_attachment_log(filename: str, mime_type: str, data: bytes) -> dict[str, Any]:
    return {
        "filename": filename,
        "mime_type": mime_type,
        "size_bytes": len(data),
        "sha256": sha256_digest(data),
    }


def serialize_agent_messages(messages: Iterable[Any]) -> list[dict[str, Any]]:
    serialized_messages: list[dict[str, Any]] = []

    for message in messages:
        entry: dict[str, Any] = {
            "kind": getattr(message, "kind", type(message).__name__),
            "run_id": getattr(message, "run_id", None),
            "model_name": getattr(message, "model_name", None),
            "provider_name": getattr(message, "provider_name", None),
            "timestamp": str(getattr(message, "timestamp", "")) or None,
        }

        parts = []
        for part in getattr(message, "parts", []):
            part_entry = {
                "part_kind": getattr(part, "part_kind", type(part).__name__),
            }

            for attr_name in (
                "content",
                "tool_name",
                "tool_call_id",
                "outcome",
                "provider_name",
                "provider_details",
                "metadata",
            ):
                if hasattr(part, attr_name):
                    part_entry[attr_name] = serialize_for_logging(getattr(part, attr_name), key=attr_name)

            parts.append(part_entry)

        entry["parts"] = parts
        serialized_messages.append(entry)

    return serialized_messages


def get_logger() -> logging.Logger:
    global _logger, _structured_logger
    if _logger is not None:
        return _logger

    logger = logging.getLogger(LOG_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not logger.handlers:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] [%(name)s] %(message)s")
        )
        logger.addHandler(stream_handler)

    if CloudLoggingHandler is not None:
        try:
            client = google.cloud.logging.Client()
            handler = CloudLoggingHandler(client, name=LOG_NAME)
            logger.addHandler(handler)
            _structured_logger = client.logger(LOG_NAME)
        except Exception as exc:  # pragma: no cover - depends on runtime credentials
            logger.warning("Cloud Logging setup failed: %s", exc)

    _logger = logger
    return logger


def log_event(event: str, severity: str = "INFO", **payload: Any) -> dict[str, Any]:
    logger = get_logger()
    entry = {"event": event}
    entry.update({key: serialize_for_logging(value, key=key) for key, value in payload.items()})

    if _structured_logger is not None:
        try:
            _structured_logger.log_struct(entry, severity=severity)
            return entry
        except Exception as exc:  # pragma: no cover - depends on runtime transport
            logger.warning("Structured Cloud Logging failed: %s", exc)

    level = getattr(logging, severity.upper(), logging.INFO)
    logger.log(level, json.dumps(entry, ensure_ascii=True))
    return entry


def log_agent_messages(*, run_id: str, model: str, messages: Iterable[Any], usage: Any = None) -> list[dict[str, Any]]:
    serialized = serialize_agent_messages(messages)
    log_event(
        "agent_messages",
        run_id=run_id,
        model=model,
        message_count=len(serialized),
        usage=usage,
        messages=serialized,
    )
    return serialized


def _tool_args_payload(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    positional_args = args[1:] if args else ()
    return {
        "positional_args": serialize_for_logging(positional_args),
        "kwargs": serialize_for_logging(kwargs),
    }


def _extract_run_id(args: tuple[Any, ...]) -> str | None:
    if not args:
        return None
    candidate = args[0]
    deps = getattr(candidate, "deps", None)
    if deps is not None and getattr(deps, "run_id", None):
        return str(deps.run_id)
    if getattr(candidate, "run_id", None):
        return str(candidate.run_id)
    return None


def log_tool(fn: F) -> F:
    if inspect.iscoroutinefunction(fn):

        @wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter()
            run_id = _extract_run_id(args)
            log_event("tool_call", run_id=run_id, tool=fn.__name__, arguments=_tool_args_payload(args, kwargs))
            try:
                result = await fn(*args, **kwargs)
            except Exception as exc:
                log_event(
                    "tool_error",
                    severity="ERROR",
                    run_id=run_id,
                    tool=fn.__name__,
                    arguments=_tool_args_payload(args, kwargs),
                    duration_ms=round((time.perf_counter() - started) * 1000),
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
                raise

            log_event(
                "tool_result",
                run_id=run_id,
                tool=fn.__name__,
                duration_ms=round((time.perf_counter() - started) * 1000),
                result=result,
            )
            return result

        return cast(F, async_wrapper)

    @wraps(fn)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        run_id = _extract_run_id(args)
        log_event("tool_call", run_id=run_id, tool=fn.__name__, arguments=_tool_args_payload(args, kwargs))
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            log_event(
                "tool_error",
                severity="ERROR",
                run_id=run_id,
                tool=fn.__name__,
                arguments=_tool_args_payload(args, kwargs),
                duration_ms=round((time.perf_counter() - started) * 1000),
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise

        log_event(
            "tool_result",
            run_id=run_id,
            tool=fn.__name__,
            duration_ms=round((time.perf_counter() - started) * 1000),
            result=result,
        )
        return result

    return cast(F, sync_wrapper)
