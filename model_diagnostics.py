"""Metadane wywołań modelu; bez treści żądań i odpowiedzi."""

from collections import Counter
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Lock


LOGGER = logging.getLogger("wikihum.model")
LOGGER.setLevel(logging.INFO)
FILE_LOCK = Lock()
ROOT = Path(__file__).resolve().parent


class DiagnosticFileHandler(RotatingFileHandler):
    def handleError(self, record):
        # Domyślny handler wypisuje traceback; tutaj wystarcza stały komunikat.
        LOGGER.warning("model_diagnostics_write_failed")


def request_metadata(messages, tools, settings, allow_tools, require_tools):
    roles = Counter(item.get("role") for item in messages)
    archived_count = 0
    for item in messages:
        if item.get("role") != "tool":
            continue
        try:
            result = json.loads(item.get("content") or "null")
            archived_count += isinstance(result, dict) and result.get("archived") is True
        except (ValueError, TypeError, RecursionError):
            pass
    return {
        "message_count": len(messages),
        "archived_result_count": archived_count,
        "role_counts": {role: roles[role] for role in ("system", "user", "assistant", "tool")},
        "messages_chars": len(json.dumps(messages, ensure_ascii=False)),
        "tool_definition_count": len(tools) if allow_tools else 0,
        "tools_allowed": allow_tools,
        "tools_required": allow_tools and require_tools,
        "max_output_tokens": settings.model_max_output_tokens,
        "model": settings.model_name,
        "thinking_enabled": settings.model_enable_thinking,
        "reasoning_effort": (settings.model_reasoning_effort or None) if settings.model_enable_thinking else None,
        "temperature": settings.model_temperature,
    }


def response_metadata(document):
    result = {"json_type": type(document).__name__}
    if not isinstance(document, dict):
        return result
    choices = document.get("choices")
    result["choices_count"] = len(choices) if isinstance(choices, list) else None
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        choice = choices[0]
        reason = choice.get("finish_reason")
        result["finish_reason"] = reason if reason in (
            "stop", "tool_calls", "length", "content_filter", "function_call", None,
        ) else "other"
        message = choice.get("message")
        result["message_type"] = type(message).__name__
        if isinstance(message, dict):
            for field in ("content", "reasoning_content", "reasoning", "refusal", "tool_calls"):
                value = message.get(field)
                result[field + "_type"] = type(value).__name__
                result[field + "_length"] = len(value) if isinstance(value, (str, list, dict)) else None
    usage = document.get("usage")
    if isinstance(usage, dict):
        result["usage"] = {}
        for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = usage.get(field)
            if type(value) is int and value >= 0:
                result["usage"][field] = value
        for section, field in (("completion_tokens_details", "reasoning_tokens"),
                               ("prompt_tokens_details", "cached_tokens")):
            details = usage.get(section)
            value = details.get(field) if isinstance(details, dict) else None
            if type(value) is int and value >= 0:
                result["usage"][section] = {field: value}
    return result


def write_diagnostic(event, filename):
    event = {"format_version": 1, "timestamp": datetime.now(timezone.utc).isoformat(), **event}
    line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    level = logging.INFO if event["outcome"] == "ok" else logging.WARNING
    LOGGER.log(level, "%s", line)
    if not filename:
        return
    try:
        # Wspólny zamek obejmuje rotację i zapis równoległych żądań procesu.
        with FILE_LOCK:
            path = Path(filename)
            if not path.is_absolute():
                path = ROOT / path
            path.parent.mkdir(parents=True, exist_ok=True)
            handler = DiagnosticFileHandler(path, maxBytes=2_000_000, backupCount=3,
                                            encoding="utf-8", delay=True)
            try:
                handler.handle(logging.LogRecord(LOGGER.name, level, "", 0, line, (), None))
            finally:
                handler.close()
    except (OSError, ValueError):
        # Błąd zapisu nie zmienia wyniku kwerendy; nie ujawniaj ścieżek/wyjątków.
        LOGGER.warning("model_diagnostics_write_failed")
