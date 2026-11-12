"""Publiczne opisy operacji, bez surowych zapytań ani rozumowania modelu."""

from contextlib import contextmanager
from dataclasses import dataclass
from time import monotonic
import csv
import re

from result_sets import table


TOOL_LABELS = {
    "prepare_record_list": "Przygotowywanie tabeli z pobranych rekordów",
    "read_tool_result": "Odczytywanie wcześniejszych wyników",
    "verify_result_set": "Sprawdzanie liczby i zestawu rekordów",
    "get_wikibase_info": "Sprawdzanie zakresu i struktury danych WikiHum",
    "search_items": "Wyszukiwanie rekordów w WikiHum",
    "search_properties": "Ustalanie sposobu zapisu informacji w bazie",
    "get_statements": "Pobieranie danych rekordu",
    "get_statement_values": "Sprawdzanie szczegółów i źródeł stwierdzenia",
    "get_property_hierarchy": "Sprawdzanie powiązań rekordów",
    "execute_sparql": "Wyszukiwanie danych spełniających warunki pytania",
}


def short_text(value):
    return " ".join(value.split())[:100] if isinstance(value, str) else ""


@dataclass
class Stage:
    state: str = "done"


class Progress:
    def __init__(self, callback=None):
        self.callback = callback
        self.sequence = 0
        self.entity_labels = {}
        self.analysis_message = "Ustalanie sposobu wyszukania danych do pytania"

    def remember_result(self, result):
        if result.get("isError"):
            return
        blocks = result.get("content")
        if not isinstance(blocks, list):
            return
        blocks = [block for block in blocks if isinstance(block, dict)
                  and isinstance(block.get("text"), str)]
        labels = {}
        for block in blocks:
            for line in block["text"].replace("\\n", "\n").splitlines():
                match = re.match(r"^([QP][1-9][0-9]*): (.+?)(?: — |$)", line)
                if match:
                    labels[match[1]] = short_text(match[2])
                match = re.match(r"^(.+?) \(([QP][1-9][0-9]*)\):", line)
                if match:
                    labels[match[2]] = short_text(match[1])
        try:
            headers, rows = table({**result, "content": blocks})
            for index, header in enumerate(headers):
                if header + "Label" not in headers:
                    continue
                label_index = headers.index(header + "Label")
                for row in rows:
                    match = re.fullmatch(r"(?:https?://[^\s]+/)?([QP][1-9][0-9]*)", row[index])
                    if match and row[label_index]:
                        labels[match[1]] = short_text(row[label_index])
        except (ValueError, TypeError, KeyError, csv.Error):
            pass
        for identifier, label in labels.items():
            if len(self.entity_labels) < 2000:
                self.entity_labels.setdefault(identifier, label)

    def tool_label(self, name, arguments):
        query = short_text(arguments.get("query"))
        identifier = short_text(arguments.get("entity_id") or arguments.get("property_id"))
        entity = self.entity_labels.get(identifier, short_text(identifier))
        if name == "search_items" and query:
            self.analysis_message = f"Analizowanie wyników wyszukiwania: {query}"
            return f"Wyszukiwanie: {query}"
        if name == "search_properties" and query:
            self.analysis_message = "Analizowanie sposobu zapisu informacji w bazie"
            return f"Ustalanie sposobu zapisu informacji: {query}"
        if name in {"get_statements", "get_statement_values", "get_property_hierarchy"} and entity:
            self.analysis_message = f"Analizowanie danych: {entity}"
            return f"Sprawdzanie danych: {entity}"
        self.analysis_message = "Analizowanie pobranych danych i opracowywanie odpowiedzi"
        if name == "execute_sparql" and re.search(r"\bCOUNT\s*\(", arguments.get("sparql", ""), re.I):
            return "Sprawdzanie liczby pasujących rekordów"
        return TOOL_LABELS.get(name, "Sprawdzanie danych w WikiHum")

    @contextmanager
    def step(self, message, *, tool=None, round_number=None):
        self.sequence += 1
        event = {"id": self.sequence, "message": message}
        if tool:
            event["tool"] = tool
        if round_number is not None:
            event["round"] = round_number
        started = monotonic()
        if self.callback:
            self.callback({**event, "state": "running"})
        stage = Stage()
        try:
            yield stage
        except BaseException:
            stage.state = "error"
            raise
        finally:
            if self.callback:
                self.callback({**event, "state": stage.state,
                               "seconds": round(monotonic() - started, 3)})
