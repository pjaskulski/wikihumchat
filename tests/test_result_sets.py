import json

import pytest

from result_sets import result_statistics, verify_result_set


def result(text, error=False):
    return {"isError": error, "content": [{"type": "text", "text": text}]}


def verify(listing, count, **changes):
    arguments = dict(list_call_id="listing", count_call_id="count", item_column="item", count_column="n")
    arguments.update(changes)
    return verify_result_set(arguments, {"listing": listing, "count": count})


def test_distinct_ids_not_number_of_rows_are_counted():
    receipt, ids = verify(result(';item;label\n0;Q1;"A;B"\n1;Q1;A\n2;Q2;B\n'),
                          result(';n\n0;2\n'))
    assert ids == {"Q1", "Q2"}
    summary = json.loads(receipt["content"][0]["text"])
    assert summary["unique_count"] == 2
    assert summary["row_count"] == 3
    assert not receipt["isError"]


@pytest.mark.parametrize("listing,count,changes", [
    (result(';item\n0;Q1\n'), result(';n\n0;63\n'), {}),
    (result(';item\n0;Q1\n', True), result(';n\n0;1\n'), {}),
    (result(';item\n0;Q1\n'), result(';n\n0;1\n'), {"list_call_id": "invented"}),
    (result(';item\n0;Q1\n'), result(';n\n0;1\n'), {"item_column": "wrong"}),
    (result(';item\n0;Q1\n'), result(';n\n0;1\n1;1\n'), {}),
    (result(';item\n0;Q1;unexpected\n'), result(';n\n0;1\n'), {}),
    (result(';item\n0;not-an-id\n'), result(';n\n0;1\n'), {}),
])
def test_incomplete_or_unusable_results_cannot_verify_list(listing, count, changes):
    receipt, ids = verify(listing, count, **changes)
    assert receipt["isError"]
    assert ids is None


def test_empty_list_can_match_zero_but_not_nonzero_count():
    assert verify(result(';item\n'), result(';n\n0;0\n'))[1] == set()
    assert verify(result(';item\n'), result(';n\n0;1\n'))[1] is None


def test_statistics_do_not_claim_completeness_or_count_duplicate_ids_twice():
    stats = result_statistics(result(';item;type\n0;Q1;Q5\n1;Q1;Q6\n'))
    assert stats["retrieved_rows"] == 2
    assert stats["unique_entity_counts"] == {"item": 1, "type": 2}
    assert stats["completeness"] == "not_determined_by_row_count"


def test_sparql_error_text_is_not_a_data_table():
    assert result_statistics(result('SPARQL query returned no data.', True)) is None
