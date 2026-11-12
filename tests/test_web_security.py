from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import logging
from threading import Event
from unittest.mock import AsyncMock

import pytest

from app import create_app
from config import Settings
from errors import ChatError
from traffic_control import TrafficControl


RESULT = {"answer": "Odpowiedź", "history": [], "sources": [], "warnings": []}


def make_app(**overrides):
    service = AsyncMock()
    service.reply.return_value = RESULT
    settings = replace(Settings(model_api_key="private-api-key"), **overrides)
    return create_app(settings, chat_service=service), service


def test_rate_limit_returns_retry_after_and_never_calls_model():
    now = [0.0]
    service = AsyncMock()
    service.reply.return_value = RESULT
    settings = Settings(model_api_key="secret", rate_limit_requests=1)
    app = create_app(settings, chat_service=service,
                     traffic_control=TrafficControl(settings, clock=lambda: now[0]))
    with app.test_client() as client:
        assert client.post("/api/chat", json={"message": "x"}).status_code == 200
        response = client.post("/api/chat", json={"message": "x"})
        assert response.status_code == 429
        assert response.json["error"]["code"] == "rate_limit"
        assert response.headers["Retry-After"] == "60"
        assert service.reply.await_count == 1
        assert client.get("/healthz").status_code == 200
        assert client.get("/").status_code == 200
        now[0] = 60
        assert client.post("/api/chat", json={"message": "x"}).status_code == 200


def test_invalid_json_consumes_attempt_but_releases_slot():
    app, service = make_app(rate_limit_requests=1, chat_max_concurrent_requests=1)
    with app.test_client() as client:
        assert client.post("/api/chat", data="bad", content_type="application/json").status_code == 400
        assert client.post("/api/chat", json={"message": "x"}).status_code == 429
        assert client.post("/api/chat", json={"message": "x"},
                           environ_overrides={"REMOTE_ADDR": "192.0.2.2"}).status_code == 200
    service.reply.assert_awaited_once()


def test_forged_forwarded_header_does_not_reset_quota():
    app, service = make_app(rate_limit_requests=1)
    with app.test_client() as client:
        assert client.post("/api/chat", json={"message": "x"},
                           headers={"X-Forwarded-For": "192.0.2.1"}).status_code == 200
        assert client.post("/api/chat", json={"message": "x"},
                           headers={"X-Forwarded-For": "192.0.2.2"}).status_code == 429
    service.reply.assert_awaited_once()


def test_explicit_trusted_proxy_separates_clients():
    app, service = make_app(rate_limit_requests=1, trusted_proxy_cidrs="127.0.0.1/32")
    with app.test_client() as client:
        for address in ("192.0.2.1", "192.0.2.2"):
            assert client.post("/api/chat", json={"message": "x"},
                               headers={"X-Forwarded-For": address}).status_code == 200
    assert service.reply.await_count == 2


def test_http_concurrency_limit_and_recovery():
    entered = Event()
    release = Event()

    class Service:
        calls = 0

        async def reply(self, payload):
            self.calls += 1
            if self.calls == 1:
                entered.set()
                # Osobny wątek żądania pozwala zatrzymać atrapę bez sieci
                # i bez dodatkowego executora wewnątrz pętli asyncio.
                assert release.wait(5)
            return RESULT

    service = Service()
    app = create_app(Settings(model_api_key="secret", chat_max_concurrent_requests=1),
                     chat_service=service)

    def first():
        with app.test_client() as client:
            return client.post("/api/chat", json={"message": "x"}).status_code

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(first)
        try:
            assert entered.wait(timeout=5)
            with app.test_client() as client:
                response = client.post("/api/chat", json={"message": "y"})
                assert response.status_code == 503
                assert response.json["error"]["code"] == "service_busy"
                assert response.headers["Retry-After"] == "1"
                assert service.calls == 1
        finally:
            release.set()
        assert future.result(timeout=5) == 200
    with app.test_client() as client:
        assert client.post("/api/chat", json={"message": "z"}).status_code == 200


@pytest.mark.parametrize("headers", [
    {"Origin": "https://other.example"}, {"Origin": "null"},
    {"Origin": "http://localhost.evil.example"},
    {"Origin": "http://localhost:5001"},
    {"Origin": "http://user:password@localhost"},
    {"Referer": "https://other.example/path"},
    {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"},
    {"Origin": "http://localhost", "Sec-Fetch-Site": "cross-site"},
])
def test_untrusted_origin_cannot_invoke_services_or_consume_quota(headers):
    app, service = make_app(rate_limit_requests=1)
    with app.test_client() as client:
        assert client.post("/api/chat", json={"message": "x"}, headers=headers).status_code == 403
        assert client.post("/api/chat", json={"message": "x"}).status_code == 200
    service.reply.assert_awaited_once()


@pytest.mark.parametrize("headers", [
    {}, {"Origin": "http://localhost:80"}, {"Sec-Fetch-Site": "same-origin"},
    {"Referer": "http://localhost/path?query=private-reference"},
])
def test_same_origin_and_command_line_clients_are_accepted(headers):
    app, _ = make_app()
    with app.test_client() as client:
        assert client.post("/api/chat", json={"message": "x"}, headers=headers).status_code == 200


def test_public_https_origin_behind_proxy_ignores_forged_forwarded_host():
    app, service = make_app(app_public_url="https://chat.example.org")
    with app.test_client() as client:
        response = client.post("/api/chat", base_url="http://chat.example.org", json={"message": "x"},
                               headers={"Origin": "https://chat.example.org:443"})
        assert response.status_code == 200
        assert "Strict-Transport-Security" in response.headers
        response = client.post("/api/chat", base_url="http://chat.example.org", json={"message": "x"},
                               headers={"Origin": "https://evil.example", "X-Forwarded-Host": "evil.example"})
        assert response.status_code == 403
    service.reply.assert_awaited_once()


@pytest.mark.parametrize("base_url", ["http://evil.example", "http://localhost.evil.example"])
def test_unknown_host_is_rejected_before_model_access(base_url):
    app, service = make_app()
    with app.test_client() as client:
        response = client.post("/api/chat", base_url=base_url, json={"message": "x"})
    assert response.status_code == 400
    assert response.json["error"]["code"] == "untrusted_host"
    service.reply.assert_not_awaited()


def test_headers_protect_html_static_assets_and_error_responses():
    app, _ = make_app()
    with app.test_client() as client:
        responses = [client.get("/"), client.get("/static/chat.js"),
                     client.post("/api/chat", data="bad", content_type="application/json")]
    for response in responses:
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Referrer-Policy"] == "no-referrer"
        policy = response.headers["Content-Security-Policy"]
        assert "connect-src 'self'" in policy
        assert "frame-ancestors 'none'" in policy
        assert "unsafe-inline" not in policy
        assert "Access-Control-Allow-Origin" not in response.headers
        assert "Strict-Transport-Security" not in response.headers


def test_unexpected_error_is_safe_and_does_not_leak_into_logs(caplog):
    app, service = make_app(chat_max_concurrent_requests=1)
    service.reply.side_effect = RuntimeError("private-api-key private-question private-answer")
    with caplog.at_level(logging.INFO):
        with app.test_client() as client:
            response = client.post("/api/chat?private-query", json={"message": "private-question"},
                                   environ_overrides={"REMOTE_ADDR": "192.0.2.123"})
            assert response.status_code == 500
            assert response.json["error"]["code"] == "internal_error"
            service.reply.side_effect = None
            assert client.post("/api/chat", json={"message": "x"}).status_code == 200
    for secret in ("private-api-key", "private-question", "private-answer", "private-query", "192.0.2.123"):
        assert secret not in response.get_data(as_text=True)
        assert secret not in caplog.text
    assert "status=500 code=internal_error duration_ms=" in caplog.text
    assert "status=200 code=ok duration_ms=" in caplog.text


def test_known_timeout_releases_slot_and_is_recorded_as_code(caplog):
    app, service = make_app(chat_max_concurrent_requests=1)
    service.reply.side_effect = ChatError("mcp_timeout", "Przekroczono czas.", 504)
    with caplog.at_level(logging.INFO):
        with app.test_client() as client:
            assert client.post("/api/chat", json={"message": "private"}).status_code == 504
            service.reply.side_effect = None
            assert client.post("/api/chat", json={"message": "x"}).status_code == 200
    assert "code=mcp_timeout" in caplog.text
    assert "private" not in caplog.text
