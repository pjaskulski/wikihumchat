from scripts.evaluate_chat import automatic_checks


def test_evaluator_reports_sparql_pid_used_without_discovery():
    case = {"question": "Sprawdź Q5"}
    row = {"http_status": 200, "response": {"answer": "Wynik", "sources": []}, "tools": [
        {"name": "execute_sparql", "arguments": {"sparql": "SELECT ?x WHERE {?x wdt:P1448 wd:Q5}"},
         "result": {"isError": False, "content": [{"text": "No results"}]}},
    ]}
    assert not automatic_checks(case, row)["sparql_ids_discovered"]
    case["question"] = "Sprawdź Q5 i P1448"
    assert automatic_checks(case, row)["sparql_ids_discovered"]
