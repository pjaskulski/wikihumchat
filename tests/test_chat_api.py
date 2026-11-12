from unittest.mock import AsyncMock

import pytest

from app import create_app
from config import Settings
from errors import ChatError


def test_chat_endpoint_contract():
    service = AsyncMock()
    result = {"answer": "Odpowiedź", "sources": [], "history": [], "warnings": []}
    service.reply.return_value = result
    app = create_app(Settings(model_api_key="secret"), chat_service=service)
    with app.test_client() as client:
        response = client.post("/api/chat", json={"message": "Pytanie", "history": []})
    assert response.status_code == 200
    assert response.json == result
    assert response.headers["Cache-Control"] == "no-store"
    service.reply.assert_awaited_once_with({"message": "Pytanie", "history": []})


@pytest.mark.parametrize("body,content_type,status,code", [
    ("not json", "application/json", 400, "invalid_json"),
    ('{"message":"x"}', "text/plain", 400, "invalid_json"),
    ('{"message":"' + "x" * 100 + '"}', "application/json", 413, "request_too_large"),
])
def test_invalid_http_payload_never_calls_services(body, content_type, status, code):
    service = AsyncMock()
    app = create_app(Settings(model_api_key="secret", request_max_bytes=80), chat_service=service)
    with app.test_client() as client:
        response = client.post("/api/chat", data=body, content_type=content_type)
    assert response.status_code == status
    assert response.json["error"]["code"] == code
    service.reply.assert_not_awaited()


def test_upstream_failure_returns_safe_json():
    service = AsyncMock()
    service.reply.side_effect = ChatError("model_timeout", "Przekroczono czas.", 504)
    app = create_app(Settings(model_api_key="private-secret"), chat_service=service)
    with app.test_client() as client:
        response = client.post("/api/chat", json={"message": "Pytanie"})
    assert response.status_code == 504
    assert response.json == {"error": {"code": "model_timeout", "message": "Przekroczono czas."}}
    assert "private-secret" not in response.get_data(as_text=True)
