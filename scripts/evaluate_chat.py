"""Jawna ewaluacja z usługami: zapisuje syntetyczne pytania i dowody MCP."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from time import monotonic

import httpx


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402
from chat_service import ChatService, ENTITY_ID, ITEM_ID, item_ids  # noqa: E402
from config import Settings  # noqa: E402
from mcp_client import connect_mcp  # noqa: E402
from model_client import ModelClient  # noqa: E402
from listing_control import plain_label, record_rows  # noqa: E402


TRACE = ContextVar("evaluation_trace", default=None)
MODEL_TRACE = ContextVar("evaluation_model_trace", default=None)


def prepared_table_in_messages(messages):
    results = []
    for message in messages:
        try:
            value = json.loads(message.get("content") or "null")
        except (ValueError, TypeError):
            continue
        if not isinstance(value, dict):
            continue
        if message["role"] == "tool":
            results.append(value)
        for item in value.get("tool_results", []):
            if item.get("tool") == "prepare_record_list":
                results.append(item.get("result", {}))
    for result in results:
        if result.get("isError"):
            continue
        for block in result.get("content", []):
            try:
                receipt = json.loads(block.get("text", ""))
            except (ValueError, TypeError):
                continue
            if isinstance(receipt, dict) and receipt.get("table_ready") is True:
                return True
    return False


class AuditedMCP:
    def __init__(self, client, trace):
        self.client = client
        self.trace = trace
        self.instructions = client.instructions

    async def list_tools(self):
        return await self.client.list_tools()

    async def call_tool(self, name, arguments):
        started = monotonic()
        entry = {"name": name, "arguments": arguments}
        self.trace.append(entry)
        try:
            result = await self.client.call_tool(name, arguments)
            entry["result"] = result
            return result
        except Exception as error:
            entry["error_code"] = getattr(error, "code", type(error).__name__)
            raise
        finally:
            entry["seconds"] = round(monotonic() - started, 3)


@asynccontextmanager
async def audited_mcp(settings):
    async with connect_mcp(settings) as client:
        yield AuditedMCP(client, TRACE.get())


def audited_model(settings):
    client = ModelClient(settings)
    trace = MODEL_TRACE.get()

    async def response_status(response):
        # Wyłącznie status i wybór narzędzi; bez nagłówka autoryzacji i treści błędu.
        payload = json.loads(response.request.content)
        local_ids = {
            call["id"] for message in payload["messages"]
            for call in message.get("tool_calls", [])
            if call.get("function", {}).get("name") == "verify_result_set"
        }
        trace.append({"http_status": response.status_code,
                      "tool_choice": payload.get("tool_choice"),
                      "prepared_table_ready": prepared_table_in_messages(payload["messages"]),
                      "local_verifications": [json.loads(message["content"])
                          for message in payload["messages"]
                          if message.get("tool_call_id") in local_ids]})

    client.http.event_hooks["response"].append(response_status)
    return client


def automatic_checks(case, row):
    response = row["response"] or {}
    good = [call for call in row["tools"] if "result" in call
            and not call["result"].get("isError") and call["name"] != "get_wikibase_info"]
    observed = item_ids([call["result"] for call in good])
    ids = {source["id"] for source in response.get("sources", [])}
    answer = response.get("answer", "")
    checks = {
        "http_200": row["http_status"] == 200,
        "fresh_data_query": bool(good),
        "sources_grounded_in_current_tools": ids <= observed,
        "expected_records": set(case.get("expected_ids", [])) <= ids,
        "expected_terms": all(term.casefold() in answer.casefold()
                              for term in case.get("expected_terms", [])),
        "enough_data_calls": len(good) >= case.get("min_data_calls", 1),
        "enough_sources": len(ids) >= case.get("min_sources", 0),
    }
    if case.get("min_listed_records"):
        listed = set()
        for name, target in record_rows(answer):
            identifiers = set(ITEM_ID.findall(target))
            if plain_label(name) and len(identifiers) == 1:
                listed.update(identifiers)
        for line in answer.splitlines():
            if not re.match(r"^\s*(?:\||[-+*]\s|\d+[.)]\s)", line):
                continue
            for label, identifier in re.findall(
                r"\[([^\]\n]+)\]\(https?://[^\s)]*/wiki/Item:(Q[1-9][0-9]*)\)", line,
            ):
                if not re.fullmatch(r"Q[1-9][0-9]*", label.strip()):
                    listed.add(identifier)
        checks["enough_named_records_in_listing"] = len(listed) >= case["min_listed_records"]
    if case.get("reference_file"):
        reference = json.loads((ROOT / "tests" / case["reference_file"]).read_text("utf-8"))
        expected = set(reference["expected_ids"])
        rows = record_rows(answer)
        row_ids = [set(ITEM_ID.findall(target)) for _, target in rows]
        actual = set().union(*row_ids) if row_ids else set()
        checks["exact_result_set"] = (actual == expected and len(rows) == len(expected)
                                      and all(len(ids) == 1 for ids in row_ids))
        if response.get("verified_result_ids") is not None:
            checks["verified_count_matches_reference"] = set(response["verified_result_ids"]) == expected
            checks["exact_result_set"] &= checks["verified_count_matches_reference"]
        checks["all_result_ids_in_answer"] = expected <= set(ITEM_ID.findall(answer))
        checks["no_unprepared_forced_summary"] = not any(
            request.get("tool_choice") is None and not request.get("prepared_table_ready")
            for request in row.get("model_requests", [])
        )
    if case.get("no_sources"):
        checks["no_invented_sources"] = not ids and not ITEM_ID.findall(answer)
    if case.get("search_queries"):
        queries = {call["arguments"].get("query", "").casefold()
                   for call in good if call["name"] == "search_items"}
        checks["separate_alias_searches"] = all(
            query.casefold() in queries for query in case["search_queries"]
        )
    # Kontrola odnośników do elementów WikiHum także w samym tekście.
    mentioned_links = set(re.findall(r"/wiki/Item:(Q[1-9][0-9]*)", answer))
    checks["answer_record_links_grounded"] = mentioned_links <= observed
    checks["answer_ids_grounded"] = set(ITEM_ID.findall(answer)) <= observed
    checks["linked_records_listed"] = mentioned_links <= ids
    confirmed = item_ids([case.get("question", "")] + [
        item["content"] for item in response.get("history", []) if item["role"] == "user"
    ], ENTITY_ID)
    sparql_ok = True
    for call in row["tools"]:
        if call["name"] == "execute_sparql":
            sparql_ok &= item_ids(call["arguments"].get("sparql", ""), ENTITY_ID) <= confirmed
        if "result" in call and not call["result"].get("isError"):
            confirmed.update(item_ids(call["result"], ENTITY_ID))
    checks["sparql_ids_discovered"] = sparql_ok
    return checks


def main():
    cases = json.loads((ROOT / "tests/evaluation_cases.json").read_text("utf-8"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=[case["id"] for case in cases], action="append")
    parser.add_argument("--parallel", action="store_true", help="Dwie dodatkowe równoczesne kwerendy")
    parser.add_argument("--output", type=Path, default=ROOT / "reports/evaluation.json")
    args = parser.parse_args()
    selected = set(args.case or [case["id"] for case in cases])
    if "follow_up" in selected:
        selected.add("ambiguous_person")
    cases = [case for case in cases if case["id"] in selected]
    settings = Settings.from_env()
    service = ChatService(settings, mcp_factory=audited_mcp, model_factory=audited_model)
    app = create_app(settings, chat_service=service)
    report = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "suite_version": 2,
              "model": settings.model_name, "cases": [], "links": [],
              "semantic_review": "required"}

    def save():
        text = json.dumps(report, ensure_ascii=False, indent=2)
        text = text.replace(settings.model_api_key, "[REDACTED]")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")

    def evaluate(case, number, history):
        row = {"id": case["id"], "question": case["question"],
               "history_sent": len(history), "tools": [], "model_requests": []}
        token = TRACE.set(row["tools"])
        model_token = MODEL_TRACE.set(row["model_requests"])
        started = monotonic()
        try:
            with app.test_client() as client:
                response = client.post(
                    "/api/chat", base_url=settings.app_public_url or "http://localhost",
                    environ_overrides={"REMOTE_ADDR": f"192.0.2.{number}"},
                    json={"message": case["question"], "history": history},
                )
            row["http_status"] = response.status_code
            row["response"] = response.get_json()
        finally:
            TRACE.reset(token)
            MODEL_TRACE.reset(model_token)
        row["seconds"] = round(monotonic() - started, 3)
        row["checks"] = automatic_checks(case, row)
        row["technical_status"] = "passed" if all(row["checks"].values()) else "failed"
        print(f"{row['id']}: HTTP {row['http_status']}, {row['seconds']} s, {row['technical_status']}", flush=True)
        return row

    completed = {}
    for number, case in enumerate(cases, 1):
        parent = completed.get(case.get("history_from"), {})
        history = (parent.get("response") or {}).get("history", [])
        row = evaluate(case, number, history)
        if case.get("history_from"):
            row["checks"]["has_follow_up_history"] = bool(history)
            if not history:
                row["technical_status"] = "failed"
        report["cases"].append(row)
        completed[case["id"]] = row
        save()
    if args.parallel:
        parallel = [
            {"id": "parallel_a", "question": "Sprawdź rekord Q158021. Kim jest ta osoba? Podaj odnośnik.",
             "expected_ids": ["Q158021"]},
            {"id": "parallel_b", "question": "Sprawdź rekord Q158343. Kim jest ta osoba? Podaj odnośnik.",
             "expected_ids": ["Q158343"]},
        ]
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(evaluate, case, index + 100, [])
                       for index, case in enumerate(parallel)]
            rows = [future.result() for future in futures]
        for row, other in zip(rows, reversed(parallel)):
            answer = (row.get("response") or {}).get("answer", "")
            isolated = all(item not in answer for item in other["expected_ids"])
            row["checks"]["parallel_target_isolated"] = isolated
            if not isolated:
                row["technical_status"] = "failed"
        report["cases"].extend(rows)
        save()
    urls = sorted({source["url"] for row in report["cases"]
                   for source in (row.get("response") or {}).get("sources", [])})
    with httpx.Client(timeout=20) as http:
        for url in urls[:50]:
            entry = {"url": url}
            try:
                assert url.startswith(settings.wikihum_base_url.rstrip("/") + "/wiki/Item:Q")
                response = http.get(url)
                entry.update(status=response.status_code, content_type=response.headers.get("content-type"))
                entry["ok"] = response.status_code == 200 and "text/html" in entry["content_type"]
            except Exception as error:
                entry.update(ok=False, error_type=type(error).__name__)
            report["links"].append(entry)
    passed = all(row["technical_status"] == "passed" for row in report["cases"])
    report["links_sampled"] = len(urls) > 50
    passed = passed and all(item["ok"] for item in report["links"])
    report["technical_status"] = "passed" if passed else "failed"
    save()
    print(f"Raport: {args.output}. Ocena merytoryczna wymaga przeglądu dowodów.", flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
