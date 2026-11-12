"""Ręczny test dwóch tur API z rzeczywistym modelem i MCP, bez serwera HTTP."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from time import monotonic


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402
from config import Settings  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/chat.json")
    args = parser.parse_args()
    settings = Settings.from_env()
    app = create_app(settings)
    history = []
    report = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "turns": []}
    with app.test_client() as client:
        for question in [
            "Znajdź w WikiHum osoby o imieniu i nazwisku Jan Kostka. Podaj odnośniki do rekordów.",
            "Który z nich był opatem oliwskim? Podaj odnośnik do jego rekordu.",
        ]:
            started = monotonic()
            response = client.post(
                "/api/chat", base_url=settings.app_public_url or "http://localhost",
                json={"message": question, "history": history},
            )
            result = response.get_json()
            report["turns"].append({"status": response.status_code,
                                    "seconds": round(monotonic() - started, 3),
                                    "response": result})
            print(f"Tura {len(report['turns'])}: HTTP {response.status_code}")
            if response.status_code != 200:
                break
            history = result["history"]
    ok = len(report["turns"]) == 2 and all(
        turn["status"] == 200 and turn["response"].get("sources")
        for turn in report["turns"]
    )
    report["status"] = "ok" if ok else "failed"
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    rendered = rendered.replace(settings.model_api_key, "[REDACTED]")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered + "\n", encoding="utf-8")
    print(f"Raport: {args.output}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
