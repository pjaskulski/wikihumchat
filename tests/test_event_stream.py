import asyncio
import json
import logging

import pytest

from app import create_app
from config import Settings
from errors import ChatError


HEADERS = {"Accept": "text/event-stream"}
RESULT = {"answer": "Gotowa odpowiedź", "sources": [], "warnings": [], "history": [
    {"role": "user", "content": "Pytanie"}, {"role": "assistant", "content": "Gotowa odpowiedź"},
]}


def events(body):
    result = []
    for frame in body.split("\n\n"):
        lines = frame.splitlines()
        kind = next((line[7:] for line in lines if line.startswith("event: ")), None)
        data = next((line[6:] for line in lines if line.startswith("data: ")), None)
        if kind and data:
            result.append((kind, json.loads(data)))
    return result


class Service:
    def __init__(self):
        self.calls = 0
        self.cancelled = False

    async def reply(self, payload, *, on_progress=None):
        self.calls += 1
        on_progress({"id": 1, "message": "Wyszukiwanie rekordów", "state": "running"})
        try:
            await asyncio.sleep(0)
            on_progress({"id": 1, "message": "Wyszukiwanie rekordów", "state": "done", "seconds": 0.01})
            return RESULT
        finally:
            self.cancelled = True


def test_progress_is_received_before_service_finishes_and_slot_remains_taken():
    class Waiting(Service):
        async def reply(self, payload, *, on_progress=None):
            self.calls += 1
            on_progress({"id": 1, "message": "Praca narzędzia", "state": "running"})
            try:
                await asyncio.sleep(60)
            finally:
                self.cancelled = True

    service = Waiting()
    app = create_app(Settings(model_api_key="secret", chat_max_concurrent_requests=1),
                     chat_service=service)
    with app.test_client() as client:
        response = client.post("/api/chat", json={"message": "Pytanie"}, headers=HEADERS,
                               buffered=False)
        iterator = iter(response.response)
        assert b": connected" in next(iterator)
        assert b"event: progress" in next(iterator)
        assert service.calls == 1
        assert not service.cancelled
        busy = client.post("/api/chat", json={"message": "Inne"}, headers=HEADERS)
        assert busy.status_code == 503
        assert busy.is_json
        response.close()
        assert service.cancelled
        app.extensions["chat_service"] = Service()
        recovered = client.post("/api/chat", json={"message": "Pytanie"}, headers=HEADERS)
        assert recovered.status_code == 200
        assert events(recovered.get_data(as_text=True))[-1] == ("result", RESULT)
        recovered.close()


def test_stream_has_ordered_progress_single_final_result_and_safe_headers(caplog):
    app = create_app(Settings(model_api_key="private-key"), chat_service=Service())
    with caplog.at_level(logging.INFO):
        with app.test_client() as client:
            response = client.post("/api/chat", json={"message": "private-question"}, headers=HEADERS)
            assert response.mimetype == "text/event-stream"
            assert response.headers["X-Accel-Buffering"] == "no"
            assert response.headers["Cache-Control"] == "no-store"
            assert "Content-Security-Policy" in response.headers
            records = events(response.get_data(as_text=True))
            response.close()
    assert [kind for kind, _ in records] == ["progress", "progress", "result"]
    assert records[-1][1] == RESULT
    assert "private" not in caplog.text
    assert "result_status=200 code=ok" in caplog.text
    assert caplog.text.count("chat_request") == 1


@pytest.mark.parametrize("failure,code,status", [
    (ChatError("mcp_timeout", "Przekroczono czas.", 504), "mcp_timeout", 504),
    (RuntimeError("private-question private-key"), "internal_error", 500),
])
def test_errors_after_headers_are_terminal_safe_events(failure, code, status, caplog):
    class Failed(Service):
        async def reply(self, payload, *, on_progress=None):
            on_progress({"id": 1, "message": "Rozpoczęto kwerendę", "state": "running"})
            raise failure

    app = create_app(Settings(model_api_key="private-key"), chat_service=Failed())
    with caplog.at_level(logging.INFO):
        with app.test_client() as client:
            response = client.post("/api/chat", json={"message": "private-question"}, headers=HEADERS)
            body = response.get_data(as_text=True)
            response.close()
    terminal = events(body)[-1]
    assert terminal[0] == "error"
    assert terminal[1]["error"]["code"] == code
    assert terminal[1]["status"] == status
    assert "private" not in body
    assert "private" not in caplog.text


def test_input_validation_and_rate_limit_happen_before_stream_headers():
    service = Service()
    app = create_app(Settings(model_api_key="secret", rate_limit_requests=1), chat_service=service)
    with app.test_client() as client:
        response = client.post("/api/chat", json={"message": ""}, headers=HEADERS)
        assert response.status_code == 400
        assert response.is_json
        limited = client.post("/api/chat", json={"message": "Pytanie"}, headers=HEADERS)
        assert limited.status_code == 429
        assert limited.is_json
        assert "Retry-After" in limited.headers
    assert service.calls == 0


def test_bounded_progress_buffer():
    from event_stream import ChatEventStream

    class Burst(Service):
        async def reply(self, payload, *, on_progress=None):
            for index in range(1000):
                on_progress({"id": index + 1, "message": "Etap", "state": "done"})
            await asyncio.sleep(0)
            return RESULT

    finished = []
    released = []
    stream = ChatEventStream(Burst(), {}, lambda: released.append(True),
                             lambda *args: finished.append(args), heartbeat_seconds=0.001)
    body = "".join(stream)
    stream.close()
    assert len([kind for kind, _ in events(body) if kind == "progress"]) == 64
    assert events(body)[-1] == ("result", RESULT)
    assert released == [True]
    assert finished == [("ok", 200)]


def test_heartbeat_can_be_received_while_service_is_waiting():
    from event_stream import ChatEventStream

    class Waiting(Service):
        async def reply(self, payload, *, on_progress=None):
            await asyncio.Event().wait()

    stream = ChatEventStream(Waiting(), {}, lambda: None, lambda *_: None,
                             heartbeat_seconds=0.001)
    iterator = iter(stream)
    assert next(iterator) == ": connected\n\n"
    assert next(iterator) == ": keep-alive\n\n"
    stream.close()


def test_close_before_iteration_releases_reservation_once():
    from event_stream import ChatEventStream

    released = []
    stream = ChatEventStream(Service(), {}, lambda: released.append(True), lambda *_: None)
    stream.close()
    stream.close()
    assert released == [True]
