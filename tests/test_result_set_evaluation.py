import json

from scripts.evaluate_chat import ROOT, automatic_checks


def test_count_and_reference_links_do_not_replace_named_listing():
    row = {"http_status": 200, "tools": [], "response": {
        "answer": "Znaleziono 62 osoby.\n\n- [Q1](https://example.org/wiki/Item:Q1)",
        "sources": [],
    }}
    case = {"question": "Jakich znasz artystów?", "min_listed_records": 2}
    assert not automatic_checks(case, row)["enough_named_records_in_listing"]
    row["response"]["answer"] = (
        "| Artysta | Data |\n| --- | --- |\n"
        "| [Jan](https://example.org/wiki/Item:Q1) | XVI w. |\n"
        "| [Piotr](https://example.org/wiki/Item:Q2) | XVII w. |"
    )
    assert automatic_checks(case, row)["enough_named_records_in_listing"]
    row["response"]["answer"] = (
        "| Osoba | QID |\n|---|---|\n"
        "| Jan | [Q1](https://example.org/wiki/Item:Q1) |\n"
        "| Piotr | [Q2](https://example.org/wiki/Item:Q2) |"
    )
    assert automatic_checks(case, row)["enough_named_records_in_listing"]


def test_benchmark_rejects_same_count_with_wrong_identifier():
    expected = json.loads((ROOT / "tests/royal_places_reference.json").read_text())["expected_ids"]
    incorrect = [*expected[:-1], "Q999999999"]
    row = {"http_status": 200, "tools": [], "response": {
        "verified_result_ids": incorrect, "answer": "| Nazwa | QID |\n|---|---|\n"
        + "\n".join(f"| Nazwa | {identifier} |" for identifier in expected), "sources": [],
    }}
    case = {"question": "Pytanie", "reference_file": "royal_places_reference.json"}
    assert not automatic_checks(case, row)["exact_result_set"]
    row["response"]["verified_result_ids"] = expected
    assert automatic_checks(case, row)["exact_result_set"]
    row["response"]["answer"] = " ".join(expected[:-1])
    assert not automatic_checks(case, row)["all_result_ids_in_answer"]
    row["model_requests"] = [{"tool_choice": "auto"}, {"tool_choice": None}]
    assert not automatic_checks(case, row)["no_unprepared_forced_summary"]
    row["model_requests"][-1]["prepared_table_ready"] = True
    assert automatic_checks(case, row)["no_unprepared_forced_summary"]
