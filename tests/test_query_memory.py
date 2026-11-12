import json

from query_memory import QueryMemory, encoded_size
from result_sets import verify_result_set


def result(text, error=False):
    return {"isError": error, "content": [{"type": "text", "text": text}]}


def test_compaction_keeps_protocol_and_original_results_available():
    memory = QueryMemory()
    original = result("Q1: Źródło " + "dane " * 6000)
    memory.remember("call1", "search_items", {"query": "Kraków"}, original)
    messages = [{"role": "system", "content": "Instrukcja"}, {"role": "user", "content": "Pytanie"},
                {"role": "assistant", "content": None, "tool_calls": [{"id": "call1"}]},
                {"role": "tool", "tool_call_id": "call1", "content": json.dumps(original, ensure_ascii=False)}]
    before = json.dumps(messages)
    packed, count = memory.pack(messages, [], 5000)
    assert count == 1 and encoded_size(packed, []) <= 5000
    assert [m["role"] for m in packed] == [m["role"] for m in messages]
    assert packed[-1]["tool_call_id"] == "call1"
    assert json.loads(packed[-1]["content"])["preview_is_complete"] is False
    assert json.dumps(messages) == before
    fragments, offset = [], 0
    while True:
        part = json.loads(memory.read({"call_id": "call1", "offset": offset, "limit": 1000})["content"][0]["text"])
        fragments.append(part["result_json_fragment"])
        if part["next_offset"] is None:
            break
        offset = part["next_offset"]
    assert json.loads("".join(fragments)) == original


def test_cache_is_local_and_does_not_cache_errors():
    memory = QueryMemory()
    memory.remember("1", "search", {"x": 1, "y": 2}, result("OK"))
    assert memory.cached("search", {"y": 2, "x": 1}) is not None
    assert QueryMemory().cached("search", {"x": 1, "y": 2}) is None
    memory.remember("2", "error", {}, result("Błąd", True))
    assert memory.cached("error", {}) is None
    assert json.loads(memory.read({"call_id": "2"})["content"][0]["text"])["original_is_error"]
    assert memory.read({"call_id": "invented"})["isError"]


def test_compaction_prefers_listing_over_newer_discovery_results():
    memory = QueryMemory()
    listing = {**result(";item;name;description\n0;Q1;Jan;" + "opis " * 400
                       + "\n1;Q2;Piotr;" + "opis " * 400 + "\n"),
               "table_statistics": {"unique_entity_counts": {"item": 2}}}
    discovery = result("Wynik pomocniczy " * 2000)
    memory.remember("list", "execute_sparql", {}, listing)
    memory.remember("discovery", "search_properties", {}, discovery)
    messages = [
        {"role": "tool", "tool_call_id": key, "content": json.dumps(value)}
        for key, value in [("list", listing), ("discovery", discovery)]
    ]
    packed, changed = memory.pack(messages, [], 10000)
    assert changed == 1
    assert json.loads(packed[0]["content"]) == listing
    assert json.loads(packed[1]["content"])["archived"]
    assert encoded_size(packed, []) <= 10000


def test_count_verification_uses_full_table_after_compaction():
    memory = QueryMemory()
    listing = result(";item;description\n" + "\n".join(f"{i};Q{i};" + "opis " * 100 for i in range(1, 40)))
    count = result(";n\n0;39\n")
    memory.remember("list", "execute_sparql", {}, listing)
    memory.remember("count", "execute_sparql", {"count": True}, count)
    packed, changed = memory.pack([
        {"role": "tool", "tool_call_id": "list", "content": json.dumps(listing)},
        {"role": "tool", "tool_call_id": "count", "content": json.dumps(count)},
    ], [], 4000)
    assert changed == 1
    receipt, ids = verify_result_set(dict(list_call_id="list", count_call_id="count",
                                         item_column="item", count_column="n"), {
        key: record[2] for key, record in memory.results.items()
    })
    assert not receipt["isError"]
    assert len(ids) == 39
    assert json.loads(packed[0]["content"])["archived"]
