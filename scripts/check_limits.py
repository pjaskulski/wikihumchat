"""Kontrola limitów przez lokalny HTTP z atrapą usług, bez Qwen i MCP."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from threading import Event, Thread

import httpx
from werkzeug.serving import make_server


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402
from config import Settings  # noqa: E402


class Service:
    def __init__(self):
        self.entered = Event()
        self.release = Event()
        self.calls = 0

    async def reply(self, payload):
        self.calls += 1
        if self.calls == 1:
            self.entered.set()
            if not self.release.wait(timeout=10):
                raise RuntimeError("Nie zwolniono żądania testowego")
        return {"answer": "Odpowiedź testowa", "history": [], "sources": [], "warnings": []}


def main():
    service = Service()
    app = create_app(Settings(
        model_api_key="diagnostic-only", rate_limit_requests=3,
        chat_max_concurrent_requests=1,
    ), chat_service=service)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    report = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "checks": []}

    def check(name, response, status, code=None):
        report["checks"].append({"name": name, "status": response.status_code,
                                 "retry_after": response.headers.get("Retry-After")})
        assert response.status_code == status, name
        if code:
            assert response.json()["error"]["code"] == code, name
        assert "Content-Security-Policy" in response.headers, name
        print(f"{name}: HTTP {response.status_code}")

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            first = executor.submit(httpx.post, base + "/api/chat",
                                    json={"message": "test"}, timeout=15)
            try:
                assert service.entered.wait(timeout=5), "Nie rozpoczęto pierwszego żądania"
                with httpx.Client(timeout=5) as client:
                    busy = client.post(base + "/api/chat", json={"message": "test"})
                    check("concurrency", busy, 503, "service_busy")
                    assert busy.headers["Retry-After"] == "1"
                    assert service.calls == 1
            finally:
                service.release.set()
            check("first_request", first.result(timeout=5), 200)
        with httpx.Client(timeout=5) as client:
            check("slot_released", client.post(base + "/api/chat", json={"message": "test"}), 200)
            limited = client.post(base + "/api/chat", json={"message": "test"})
            check("rate_limit", limited, 429, "rate_limit")
            assert int(limited.headers["Retry-After"]) > 0
            check("forged_ip", client.post(base + "/api/chat", json={"message": "test"},
                                           headers={"X-Forwarded-For": "192.0.2.99"}),
                  429, "rate_limit")
            check("foreign_origin", client.post(base + "/api/chat", json={"message": "test"},
                                                 headers={"Origin": "https://other.example"}),
                  403, "origin_not_allowed")
            check("health_unlimited", client.get(base + "/healthz"), 200)
        assert service.calls == 2
        report["service_calls"] = service.calls
        report["status"] = "ok"
    except Exception as error:
        report["status"] = "failed"
        report["error_type"] = type(error).__name__
    finally:
        service.release.set()
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
    output = ROOT / "reports/limits.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Raport: {output}")
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
