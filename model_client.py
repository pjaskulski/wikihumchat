"""Niestrumieniowy klient instytutowego API zgodnego z Chat Completions."""

import asyncio
import json
from dataclasses import replace
from time import monotonic
from uuid import uuid4

import httpx

from errors import ChatError, upstream_error
from model_diagnostics import request_metadata, response_metadata, write_diagnostic
from scope_policy import SCOPE_CLASSIFIER, scope_messages


class ModelClient:
    def __init__(self, settings, *, transport=None):
        self.settings = settings
        self.diagnostic_id = uuid4().hex
        self.call_index = 0
        self.http = httpx.AsyncClient(
            timeout=settings.model_timeout_seconds, transport=transport,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.http.aclose()

    async def check_scope(self, question, history):
        settings = replace(self.settings, model_enable_thinking=False, model_reasoning_effort="",
                           model_temperature=0.0, model_max_output_tokens=256,
                           model_timeout_seconds=min(30.0, self.settings.model_timeout_seconds))
        message = await self.complete(scope_messages(question, history), [SCOPE_CLASSIFIER],
                                      require_tools=True, request_settings=settings, purpose="scope_check")
        try:
            calls = message.get("tool_calls") or []
            if len(calls) != 1 or calls[0]["function"]["name"] != "classify_wikihum_scope":
                raise ValueError
            raw = calls[0]["function"]["arguments"]
            decision = json.loads(raw) if isinstance(raw, str) else raw
            if (not isinstance(decision, dict) or set(decision) != {"in_scope"}
                    or type(decision["in_scope"]) is not bool):
                raise ValueError
            return decision["in_scope"]
        except (ValueError, KeyError, TypeError):
            raise ChatError("scope_invalid_response", "Nie udało się określić zakresu pytania. "
                            "Spróbuj sformułować je inaczej. Identyfikator diagnostyczny: "
                            + self.diagnostic_id + ".") from None

    async def complete(self, messages, tools, *, allow_tools=True, require_tools=False,
                       request_settings=None, purpose=None):
        settings = request_settings or self.settings
        self.call_index += 1
        event = {
            "diagnostic_id": self.diagnostic_id, "call_index": self.call_index,
            "request": request_metadata(messages, tools, settings, allow_tools, require_tools),
            "outcome": "cancelled",
        }
        if purpose:
            event["request"]["purpose"] = purpose
        started = monotonic()
        try:
            result = await self._complete(messages, tools, event, allow_tools=allow_tools,
                                          require_tools=require_tools, request_settings=settings)
            event["outcome"] = "ok"
            return result
        except ChatError as error:
            event["outcome"] = error.code
            raise ChatError(
                error.code, error.message + f" Identyfikator diagnostyczny: {self.diagnostic_id}.",
                error.status, retry_after=error.retry_after,
            ) from None
        finally:
            event["duration_ms"] = round((monotonic() - started) * 1000)
            write_diagnostic(event, self.settings.model_diagnostics_file)

    async def _complete(self, messages, tools, event, *, allow_tools, require_tools, request_settings=None):
        settings = request_settings or self.settings
        payload = {
            "model": settings.model_name,
            "messages": messages,
            "temperature": settings.model_temperature,
            "max_tokens": settings.model_max_output_tokens,
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": settings.model_enable_thinking},
        }
        if settings.model_enable_thinking and settings.model_reasoning_effort:
            payload["chat_template_kwargs"]["reasoning_effort"] = settings.model_reasoning_effort
        if allow_tools:
            payload["tools"] = tools
            payload["tool_choice"] = "required" if require_tools else "auto"
        # Końcowe podsumowanie jest zwykłym żądaniem tekstowym.
        try:
            async with asyncio.timeout(settings.model_timeout_seconds):
                async with self.http.stream(
                    "POST", settings.model_base_url.rstrip("/") + "/chat/completions",
                    headers={"Authorization": "Bearer " + settings.model_api_key},
                    json=payload,
                ) as response:
                    event["http_status"] = response.status_code
                    response.raise_for_status()
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        event["response_bytes"] = len(body)
                        if len(body) > settings.chat_max_context_chars * 4:
                            raise ChatError(
                                "model_response_too_large", "Odpowiedź modelu jest zbyt duża.",
                            )
            document = json.loads(body)
            event["response"] = response_metadata(document)
            choice = document["choices"][0]
            if choice.get("finish_reason") not in {"stop", "tool_calls"}:
                raise ChatError(
                    "model_incomplete",
                    "Model nie ukończył odpowiedzi. Spróbuj zadać węższe pytanie.",
                )
            raw = choice["message"]
            if raw.get("role") != "assistant":
                raise ValueError
            content = raw.get("content")
            calls = raw.get("tool_calls") or []
            if content is not None and not isinstance(content, str):
                raise ValueError
            if not isinstance(calls, list):
                raise ValueError
            if not calls and not (content or "").strip():
                raise ChatError(
                    "model_empty_response", "Serwer modelu zwrócił pustą odpowiedź. Spróbuj ponownie.",
                )
            # Nie przekazuj dodatkowych pól ani ukrytego rozumowania do API.
            result = {"role": "assistant", "content": content}
            if calls:
                result["tool_calls"] = calls
            return result
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            if status in {401, 403}:
                code = "model_auth_error"
                message = f"Błąd konfiguracji dostępu do modelu (HTTP {status})."
            elif 400 <= status < 500:
                code = "model_request_rejected"
                message = f"Serwer modelu odrzucił żądanie (HTTP {status})."
            else:
                code = "model_server_error"
                message = f"Serwer modelu zwrócił błąd (HTTP {status}). Spróbuj ponownie."
            raise ChatError(code, message) from None
        except (KeyError, IndexError, TypeError, ValueError):
            raise ChatError(
                "model_invalid_response", "Model zwrócił niepoprawną odpowiedź.",
            ) from None
        except Exception as error:
            raise upstream_error(error, "model") from None
