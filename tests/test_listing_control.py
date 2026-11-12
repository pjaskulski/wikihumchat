import pytest

from listing_control import (
    TABLE_MARKER, insert_record_table, listing_problem, prepare_record_list, render_record_table,
)


def result(text, **extra):
    return {"content": [{"type": "text", "text": text}], **extra}


def selection():
    return {"Q1": {"Biadaczów"}, "Q2": {"Bębło"}}


def answer(rows):
    return "| Miejscowość | QID |\n|---|---|\n" + rows


def test_valid_listing_and_non_listing_notes_are_accepted():
    text = answer("| Biadaczów | Q1 |\n| Bębło | Q2 |") + "\n\nUwagi: Adelin nie należy do wykazu."
    assert listing_problem(text, selection()) is None
    assert listing_problem("Nie znaleziono danych.", None) is None
    assert listing_problem("Sam opis bez wykazu.", selection()) == "missing_table"


@pytest.mark.parametrize("rows,expected", [
    ("| Adelin | — |\n| Biadaczów | Q1 |\n| Bębło | Q2 |", "missing_id"),
    ("| Arkuszewo | Q3 |", "outside_selection"),
    ("| Augustów | Q1 |", "name_mismatch"),
    ("| Biadaczów | Q2 |", "name_mismatch"),
    ("| Biadaczów (Q2) | Q1 |", "name_mismatch"),
    ("| [Biadaczów](https://example.org/wiki/Item:Q2) | Q1 |", "name_mismatch"),
    ("| Biadaczów | Q1 Q2 |", "outside_selection"),
])
def test_ungrounded_rows_cannot_hide_among_valid_records(rows, expected):
    assert listing_problem(answer(rows), selection()) == expected


def test_linked_names_and_numbered_rows_are_supported():
    text = "| Nr | Osoba | Daty |\n|---|---|---|\n| 1 | [Jan](https://example.org/wiki/Item:Q1) | 1500–1560 |"
    assert listing_problem(text, {"Q1": {"Jan"}}) is None
    assert listing_problem(text, None) == "missing_selection"


def test_selection_can_combine_original_tables_and_normalize_entity_urls():
    sources = [dict(call_id=key, item_column="item", label_column="itemLabel") for key in ("a", "b")]
    receipt, records = prepare_record_list({"sources": sources}, {
        "a": result("item;itemLabel\nhttp://example.org/entity/Q1;Biadaczów\n"),
        "b": result("item;itemLabel\nQ2;Bębło\n"),
    })
    assert not receipt["isError"]
    assert records == selection()


@pytest.mark.parametrize("source", [
    result("item;itemLabel\nQ1;Biadaczów\n", isError=True),
    result("item;itemLabel\nQ1;\n"),
    result("item;itemLabel\nQ1;Q1\n"),
    result("item;wrong\nQ1;Biadaczów\n"),
])
def test_invalid_or_failed_source_does_not_prepare_a_selection(source):
    receipt, records = prepare_record_list({"sources": [dict(
        call_id="a", item_column="item", label_column="itemLabel",
    )]}, {"a": source})
    assert receipt["isError"] and records is None


def test_table_merges_types_by_qid_without_transferring_values_between_records():
    args = {"sources": [dict(call_id="a", item_column="item", label_column="itemLabel",
                            columns=[dict(column="typeLabel", heading="Typ")])],
            "name_heading": "Miejscowość"}
    data = {"a": result("item;itemLabel;typeLabel\nQ1;Biadaczów;wieś\nQ1;Biadaczów;zamek\n"
                        "Q1;Biadaczów;wieś\nQ2;Bębło;wieś\n")}
    receipt, records = prepare_record_list(args, data)
    assert not receipt["isError"] and len(records) == 2
    output = render_record_table(args, data, "https://example.org")
    assert output.count("| Biadaczów |") == 1
    assert "wieś; zamek" in output
    assert "| Bębło | [Q2](https://example.org/wiki/Item:Q2) | wieś |" in output
    assert "record_count" in receipt["content"][0]["text"]
    assert "table_ready" in receipt["content"][0]["text"]
    assert "Biadaczów" not in receipt["content"][0]["text"]  # Model nie przepisuje wierszy.


def test_multiple_sources_merge_by_id_and_heading_with_missing_values_preserved():
    args = {"sources": [
        dict(call_id="a", item_column="item", label_column="name",
             columns=[dict(column="born", heading="Urodzenie")]),
        dict(call_id="b", item_column="person", label_column="label",
             columns=[dict(column="death", heading="Śmierć")]),
    ]}
    data = {"a": result("item;name;born\nQ1;Jan;ok. 1500\nQ2;Piotr;\n"),
            "b": result("person;label;death\nQ1;Jan;1560\n")}
    output = render_record_table(args, data, "https://example.org")
    assert "| Jan | [Q1](https://example.org/wiki/Item:Q1) | ok. 1500 | 1560 |" in output
    assert "| Piotr | [Q2](https://example.org/wiki/Item:Q2) | — | — |" in output


def test_insertion_rejects_a_second_model_table_and_multiple_or_inline_markers():
    table = "| Nazwa | QID |\n|---|---|\n| Jan | Q1 |"
    assert insert_record_table("Wstęp.\n\n" + TABLE_MARKER + "\n\nUwagi.", table) == "Wstęp.\n\n" + table + "\n\nUwagi."
    assert insert_record_table("Uwagi.", table) == table + "\n\nUwagi."
    assert insert_record_table(table, table) is None
    assert insert_record_table("| Inne dane | Uwagi |\n|---|---|\n| Adelin | opis |", table) is None
    assert insert_record_table(TABLE_MARKER + "\n\n" + TABLE_MARKER, table) is None
    assert insert_record_table("Tabela: " + TABLE_MARKER, table) is None
    assert insert_record_table("```\n" + TABLE_MARKER + "\n```", table) is None


def test_two_different_fields_cannot_share_a_heading_in_one_source():
    args = {"sources": [dict(call_id="a", item_column="item", label_column="name", columns=[
        dict(column="born", heading="Daty"), dict(column="died", heading="Daty"),
    ])]}
    receipt, records = prepare_record_list(args, {"a": result("item;name;born;died\nQ1;Jan;1500;1560\n")})
    assert receipt["isError"] and records is None
