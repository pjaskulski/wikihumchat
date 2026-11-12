from pathlib import Path
import runpy

import pytest

from app import create_app
from config import Settings


PROXY_HEADERS = {
    "X-Forwarded-Prefix": "/wikihumchat",
    "X-Forwarded-Proto": "https",
}
BASE = "http://ai.ihpan.edu.pl"


class Service:
    async def reply(self, payload, *, on_progress=None):
        if on_progress:
            on_progress({"id": 1, "state": "done", "message": "Sprawdzono dane", "seconds": 0.1})
        return {"answer": "Odpowiedź", "history": [
            {"role": "user", "content": payload["message"]},
            {"role": "assistant", "content": "Odpowiedź"},
        ], "sources": [], "warnings": [], "duration_ms": 100}


@pytest.fixture
def proxy_client(monkeypatch):
    application = create_app(Settings(model_api_key="test", app_public_url="https://ai.ihpan.edu.pl",
                                      trusted_proxy_cidrs="127.0.0.1/32", rate_limit_requests=2),
                             chat_service=Service())
    monkeypatch.setattr("app.create_app", lambda: application)
    namespace = runpy.run_path(str(Path(__file__).resolve().parents[1] / "wsgi.py"))
    return namespace["app"].test_client()


def test_proxy_prefix_generates_assets_and_api_urls(proxy_client):
    page = proxy_client.get("/", base_url=BASE, headers=PROXY_HEADERS)
    assert page.status_code == 200
    text = page.get_data(as_text=True)
    for path in ("static/chat.js", "static/theme.js", "static/style.css", "static/favicon.ico",
                 "static/image/logo-wiki.png", "static/image/logo-ihpan.png"):
        assert f"/wikihumchat/{path}" in text
        # Nginx usuwa prefiks przed przekazaniem żądania do WSGI.
        assert proxy_client.get(f"/{path}", base_url=BASE, headers=PROXY_HEADERS).status_code == 200
    assert 'data-chat-url="/wikihumchat/api/chat"' in text
    assert 'data-export-url="/wikihumchat/api/export/pdf"' in text
    assert proxy_client.get("/healthz", base_url=BASE, headers=PROXY_HEADERS).json == {"status": "ok"}
    assert proxy_client.get("/wikihumchat/", base_url=BASE, headers=PROXY_HEADERS).status_code == 404


def test_proxy_chat_supports_json_sse_and_origin_validation(proxy_client):
    options = dict(base_url=BASE, json={"message": "Pytanie"})
    assert proxy_client.post("/api/chat", headers=PROXY_HEADERS, **options).json["answer"] == "Odpowiedź"
    streamed = proxy_client.post("/api/chat", headers={
        **PROXY_HEADERS, "Accept": "text/event-stream", "Origin": "https://ai.ihpan.edu.pl",
    }, buffered=True, **options)
    assert streamed.status_code == 200
    assert "event: progress" in streamed.text and "event: result" in streamed.text
    # Weryfikacja źródła odbywa się przed przyjęciem do limitów ruchu.
    denied = proxy_client.post("/api/chat", headers={**PROXY_HEADERS, "Origin": "https://other.example"}, **options)
    assert denied.status_code == 403


def test_proxy_export_returns_pdf_and_requires_correct_origin(proxy_client):
    options = dict(base_url=BASE, json={"messages": [
        {"role": "user", "content": "Pytanie"}, {"role": "assistant", "content": "Odpowiedź"},
    ]})
    result = proxy_client.post("/api/export/pdf", headers={
        **PROXY_HEADERS, "Origin": "https://ai.ihpan.edu.pl",
    }, **options)
    assert result.status_code == 200 and result.mimetype == "application/pdf"
    assert result.data.startswith(b"%PDF-")
    assert proxy_client.post("/api/export/pdf", headers={
        **PROXY_HEADERS, "Origin": "https://other.example",
    }, **options).status_code == 403


def test_ip_limits_use_original_proxy_peer_and_rightmost_forwarded_client(proxy_client):
    options = dict(base_url=BASE, json={"message": "Pytanie"},
                   environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    # Klient dopisał fałszywy adres z lewej; Nginx dopisał jego prawdziwy adres z prawej.
    first = {**PROXY_HEADERS, "X-Forwarded-For": "198.51.100.77, 203.0.113.1"}
    other = {**PROXY_HEADERS, "X-Forwarded-For": "203.0.113.2"}
    assert proxy_client.post("/api/chat", headers=first, **options).status_code == 200
    assert proxy_client.post("/api/chat", headers=first, **options).status_code == 200
    assert proxy_client.post("/api/chat", headers=first, **options).status_code == 429
    assert proxy_client.post("/api/chat", headers=other, **options).status_code == 200


def test_development_entry_does_not_trust_proxy_prefix():
    app = create_app(Settings(model_api_key="test"), chat_service=Service())
    response = app.test_client().get("/", headers=PROXY_HEADERS)
    assert 'data-chat-url="/api/chat"' in response.text
    assert "/wikihumchat/api/chat" not in response.text
