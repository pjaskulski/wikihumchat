"""Kontrola liczbowa zestawień na podstawie rzeczywistych wyników MCP."""

import csv
import io
import json
import re
from types import SimpleNamespace


VERIFY_TOOL = SimpleNamespace(
    name="verify_result_set",
    description=(
        "Lokalna kontrola zestawienia. Podaj identyfikatory wcześniejszych udanych "
        "wywołań execute_sparql z pola call_id ich wyników: listy rekordów i COUNT(DISTINCT ...) dla tych samych "
        "warunków. Narzędzie porównuje liczbę unikalnych QID z licznikiem. "
        "Nie ocenia zgodności warunków z pytaniem. Po sukcesie umieść w odpowiedzi "
        "każdy identyfikator z listy ids. Nie podawaj identyfikatorów wymyślonych wywołań."
    ),
    inputSchema={
        "type": "object", "additionalProperties": False,
        "properties": {name: {"type": "string", "minLength": 1} for name in (
            "list_call_id", "count_call_id", "item_column", "count_column",
        )},
        "required": ["list_call_id", "count_call_id", "item_column", "count_column"],
    },
)


def table(result):
    """CSV średnikowy WikiHum; nie interpretuj komunikatu błędu jako tabeli."""
    if result.get("isError"):
        raise ValueError
    texts = [block["text"] for block in result.get("content", []) if block.get("type") == "text"]
    if len(texts) != 1:
        raise ValueError
    rows = list(csv.reader(io.StringIO(texts[0]), delimiter=";", strict=True))
    if not rows or len(set(rows[0])) != len(rows[0]):
        raise ValueError
    if any(len(row) != len(rows[0]) for row in rows[1:]):
        raise ValueError
    return rows[0], rows[1:]


def verify_result_set(arguments, results):
    try:
        listing = results[arguments["list_call_id"]]
        count = results[arguments["count_call_id"]]
        if arguments["list_call_id"] == arguments["count_call_id"]:
            raise ValueError
        headers, rows = table(listing)
        column = headers.index(arguments["item_column"])
        ids = set()
        for row in rows:
            match = re.fullmatch(r"(?:https?://[^\s]+/)?(Q[1-9][0-9]*)", row[column])
            if not match:
                raise ValueError
            ids.add(match[1])
        headers, counts = table(count)
        column = headers.index(arguments["count_column"])
        if len(counts) != 1 or not re.fullmatch(r"[0-9]+", counts[0][column]):
            raise ValueError
        expected = int(counts[0][column])
        receipt = {"matches_count": len(ids) == expected, "row_count": len(rows),
                   "unique_count": len(ids), "expected_count": expected, "ids": sorted(ids),
                   "scope": "Zgodność liczbowa; warunki obu kwerend muszą odpowiadać pytaniu."}
        return {"isError": not receipt["matches_count"], "content": [{
            "type": "text", "text": json.dumps(receipt, ensure_ascii=False),
        }]}, (ids if receipt["matches_count"] else None)
    except (KeyError, ValueError, TypeError, csv.Error):
        return {"isError": True, "content": [{"type": "text", "text": (
            "Nie można porównać zestawienia. Wskaż dwa różne, udane wywołania "
            "execute_sparql oraz istniejące kolumny: QID w liście i licznik w jednej "
            "komórce wyniku COUNT(DISTINCT ...)."
        )}]}, None


def result_statistics(result):
    """Liczby pobranych wierszy, bez deklaracji kompletności kwerendy."""
    try:
        headers, rows = table(result)
    except (ValueError, TypeError, KeyError, csv.Error):
        return None
    counts = {}
    for index, header in enumerate(headers):
        values = [re.fullmatch(r"(?:https?://[^\s]+/)?(Q[1-9][0-9]*)", row[index]) for row in rows]
        if values and all(values):
            counts[header] = len({value[1] for value in values})
    return {"retrieved_rows": len(rows), "unique_entity_counts": counts,
            "completeness": "not_determined_by_row_count"}
