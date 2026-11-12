import json
from pathlib import Path

from scripts.evaluate_chat import automatic_checks, prepared_table_in_messages


def sample_row(answer="Rekord Q123", sources=None):
    return {"http_status": 200, "response": {
        "answer": answer, "sources": sources if sources is not None else [{"id": "Q123"}],
    }, "tools": [{"name": "get_statements", "arguments": {"entity_id": "Q123"},
                  "result": {"isError": False, "content": [{"text": "Rekord Q123"}]}}]}


def test_evaluation_requires_fresh_evidence_and_expected_records():
    case = {"expected_ids": ["Q123"]}
    row = sample_row()
    assert all(automatic_checks(case, row).values())
    row["tools"].clear()
    checks = automatic_checks(case, row)
    assert not checks["fresh_data_query"]
    assert not checks["sources_grounded_in_current_tools"]


def test_evaluation_does_not_accept_invented_ids_in_plain_text():
    checks = automatic_checks({}, sample_row("Rekord Q123 oraz Q999"))
    assert not checks["answer_ids_grounded"]


def test_evaluation_empty_search_can_pass_without_invented_sources():
    row = sample_row("Nie znaleziono pasującego rekordu.", sources=[])
    row["tools"] = [{"name": "search_items", "arguments": {"query": "fictional"},
                     "result": {"isError": False, "content": [{"text": "No matching items"}]}}]
    assert all(automatic_checks({"no_sources": True}, row).values())


def test_evaluation_does_not_count_metadata_or_error_as_data_query():
    row = sample_row()
    row["tools"][0]["name"] = "get_wikibase_info"
    assert not automatic_checks({}, row)["fresh_data_query"]
    row["tools"][0]["name"] = "search_items"
    row["tools"][0]["result"]["isError"] = True
    assert not automatic_checks({}, row)["fresh_data_query"]


def test_evaluation_observes_multiline_ids_in_raw_tool_data():
    row = sample_row("Rekordy Q123 i Q456", sources=[{"id": "Q123"}, {"id": "Q456"}])
    row["tools"][0]["result"]["content"][0]["text"] = "Q123: first\nQ456: second"
    assert all(automatic_checks({"expected_ids": ["Q123", "Q456"]}, row).values())


def test_reference_check_rejects_extra_rows_without_qid_even_when_all_valid_ids_are_present():
    ids = json.loads((Path(__file__).parent / "royal_places_reference.json").read_text())["expected_ids"]
    text = "| Miejscowość | QID |\n|---|---|\n" + "\n".join(f"| Nazwa | {identifier} |" for identifier in ids)
    row = sample_row(text)
    row["tools"][0]["result"]["content"][0]["text"] = " ".join(ids)
    row["response"]["sources"] = [{"id": identifier} for identifier in ids]
    case = {"reference_file": "royal_places_reference.json"}
    assert automatic_checks(case, row)["exact_result_set"]
    row["response"]["answer"] += "\n| Adelin | — |"
    assert not automatic_checks(case, row)["exact_result_set"]


def test_evaluation_recognizes_ready_table_in_text_only_summary():
    result = {"content": [{"text": json.dumps({"table_ready": True})}]}
    messages = [{"role": "user", "content": json.dumps({"tool_results": [
        {"tool": "prepare_record_list", "result": result},
    ]})}]
    assert prepared_table_in_messages(messages)
    result["isError"] = True
    assert not prepared_table_in_messages([{"role": "tool", "content": json.dumps(result)}])
