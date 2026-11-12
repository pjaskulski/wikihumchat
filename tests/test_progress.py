from progress import Progress


def result(text, **extra):
    return {"content": [{"type": "text", "text": text}], **extra}


def test_progress_uses_search_terms_and_previously_observed_labels():
    progress = Progress()
    assert progress.tool_label("search_items", {"query": "Kraków"}) == "Wyszukiwanie: Kraków"
    assert progress.analysis_message == "Analizowanie wyników wyszukiwania: Kraków"
    progress.remember_result(result("Q46043: Kraków — miasto\\nQ1: Inny rekord — opis"))
    assert progress.tool_label("get_statements", {"entity_id": "Q46043"}) == "Sprawdzanie danych: Kraków"
    progress.remember_result(result("Jan Kowalski (Q2): description: malarz"))
    assert progress.tool_label("get_statements", {"entity_id": "Q2"}) == "Sprawdzanie danych: Jan Kowalski"
    assert Progress().tool_label("get_statements", {"entity_id": "Q2"}) == "Sprawdzanie danych: Q2"


def test_progress_reads_csv_labels_but_never_error_labels_or_full_queries():
    progress = Progress()
    progress.remember_result(result("item;itemLabel\nhttp://example.org/entity/Q3;Anna\n"))
    progress.remember_result(result("Q4: Niepotwierdzona osoba — opis", isError=True))
    assert progress.tool_label("get_statements", {"entity_id": "Q3"}) == "Sprawdzanie danych: Anna"
    assert progress.tool_label("get_statements", {"entity_id": "Q4"}) == "Sprawdzanie danych: Q4"
    query = 'SELECT (COUNT(?item) AS ?n) WHERE { ?item ?p "private value" }'
    assert progress.tool_label("execute_sparql", {"sparql": query}) == "Sprawdzanie liczby pasujących rekordów"
    assert "private" not in progress.analysis_message


def test_progress_ignores_unexpected_result_shapes_and_bounds_public_search_text():
    progress = Progress()
    for data in ({"content": None}, {"content": "text"}, {"content": [None, {}]},
                 result('a;b\n"unfinished')):
        progress.remember_result(data)
    label = progress.tool_label("search_items", {"query": "\n" + "Kraków " * 100})
    assert "\n" not in label
    assert len(label) <= len("Wyszukiwanie: ") + 100
