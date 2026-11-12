"""Wiązanie wierszy odpowiedzi z jawnie wybranymi wynikami SPARQL."""

import csv
import json
import re
from types import SimpleNamespace
import unicodedata

from result_sets import table


ID = re.compile(r"\bQ[1-9][0-9]*\b")
ENTITY = re.compile(r"(?:https?://[^\s]+/)?(Q[1-9][0-9]*)")
TABLE_MARKER = "[[WIKIHUM_RECORD_LIST]]"
PREPARE_TOOL = SimpleNamespace(
    name="prepare_record_list",
    description=(
        "Przed końcowym wykazem wskaż wyniki execute_sparql zawierające jego rekordy. "
        "Podaj sources: call_id z wyniku, item_column z QID i label_column z nazwą. "
        "Możesz połączyć kilka tabel odpowiadających warunkom pytania. Opcjonalne "
        "columns w każdym źródle wskazuje dodatkowe kolumny i ich nagłówki "
        "(column, heading), np. typLabel → Typ. Kod łączy wiersze po QID i scala "
        "różne wartości tej samej kolumny bez powtórzeń. Po zakończeniu sprawdzeń "
        "wywołaj to narzędzie; aplikacja utworzy tabelę. W odpowiedzi wpisz tylko "
        "krótkie wprowadzenie/uwagi oraz [[WIKIHUM_RECORD_LIST]] w miejscu tabeli. "
        "Nie przepisuj nazw ani wierszy. Narzędzie lokalne, bez COUNT ani nowej kwerendy. "
        "Nie wybieraj szerokiej próbki kandydatów zamiast końcowego wyniku."
    ),
    inputSchema={"type": "object", "additionalProperties": False, "properties": {
        "sources": {"type": "array", "minItems": 1, "maxItems": 10, "items": {
            "type": "object", "additionalProperties": False,
            "properties": {
                **{key: {"type": "string", "minLength": 1} for key in (
                    "call_id", "item_column", "label_column",
                )},
                "columns": {"type": "array", "maxItems": 4, "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {key: {"type": "string", "minLength": 1} for key in ("column", "heading")},
                    "required": ["column", "heading"],
                }},
            }, "required": ["call_id", "item_column", "label_column"],
        }},
        "name_heading": {"type": "string", "enum": ["Nazwa", "Miejscowość", "Osoba"]},
        "render_table": {"type": "boolean", "description": "Domyślnie true: tabelę tworzy aplikacja. false zachowuje starszy tryb kontroli tabeli modelu."},
    }, "required": ["sources"]},
)


def collect_records(arguments, results):
    records, headings = {}, []
    for source in arguments["sources"]:
        headers, rows = table(results[source["call_id"]])
        item_index = headers.index(source["item_column"])
        label_index = headers.index(source["label_column"])
        if item_index == label_index:
            raise ValueError
        columns = []
        source_headings = set()
        for field in source.get("columns", []):
            heading = " ".join(field["heading"].split())
            if (not heading or heading.casefold() in source_headings
                    or heading.casefold() in {"nazwa", "miejscowość", "osoba", "qid", "id", "identyfikator"}):
                raise ValueError
            source_headings.add(heading.casefold())
            columns.append((heading, headers.index(field["column"])))
            if heading not in headings:
                headings.append(heading)
        if len(headings) > 4:
            raise ValueError
        for row in rows:
            match = ENTITY.fullmatch(row[item_index])
            label = row[label_index].strip()
            if not match or not label or ID.fullmatch(label):
                raise ValueError
            entry = records.setdefault(match[1], {"labels": [], "values": {}})
            if label not in entry["labels"]:
                entry["labels"].append(label)
            for heading, column in columns:
                value = " ".join(row[column].split())
                values = entry["values"].setdefault(heading, [])
                if value and value not in values:
                    values.append(value)
    return records, headings


def prepare_record_list(arguments, results):
    try:
        entries, headings = collect_records(arguments, results)
    except (ValueError, KeyError, TypeError, csv.Error):
        return {"isError": True, "content": [{"type": "text", "text": (
            "Nie przygotowano wykazu. Wskaż udane wyniki execute_sparql i istniejące "
            "kolumny z QID oraz niepustymi nazwami rekordów. Dodatkowe columns muszą "
            "istnieć w tabelach; łącznie wybierz do czterech różnych nagłówków danych."
        )}]}, None
    records = {key: set(entry["labels"]) for key, entry in entries.items()}
    receipt = {"record_count": len(records), "columns": headings,
               "scope": "Zgodność z wybranymi tabelami; nie ocena historyczna zapytania."}
    if arguments.get("render_table", True):
        receipt.update(table_ready=True, table_marker=TABLE_MARKER,
                       instruction="Tabela jest gotowa. Nie przepisuj jej; użyj table_marker i krótkich uwag.")
    else:
        receipt["records"] = [{"id": key, "labels": sorted(labels)} for key, labels in records.items()]
    return {"isError": False, "content": [{"type": "text", "text": json.dumps(
        receipt, ensure_ascii=False,
    )}]}, records


def markdown_cell(value):
    value = " ".join(value.split())
    return re.sub(r"([\\|`*_\[\]])", r"\\\1", value)


def render_record_table(arguments, results, base_url):
    entries, headings = collect_records(arguments, results)
    if not entries:
        return "Nie znaleziono rekordów spełniających warunki wybranej kwerendy."
    headers = [arguments.get("name_heading", "Nazwa"), "QID", *headings]
    lines = ["| " + " | ".join(markdown_cell(value) for value in headers) + " |",
             "| " + " | ".join("---" for _ in headers) + " |"]
    for identifier, entry in entries.items():
        values = [markdown_cell(entry["labels"][0]),
                  f"[{identifier}]({base_url.rstrip('/')}/wiki/Item:{identifier})"]
        values.extend(markdown_cell("; ".join(entry["values"].get(heading, []))) or "—"
                      for heading in headings)
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def insert_record_table(answer, rendered):
    """Nie publikuj równocześnie tabeli modelu i tabeli aplikacji."""
    lines = answer.splitlines()
    has_table = any(
        len(cells(line)) > 1 and len(cells(line)) == len(cells(lines[index + 1]))
        and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells(lines[index + 1]))
        for index, line in enumerate(lines[:-1])
    )
    if has_table or answer.count(TABLE_MARKER) > 1:
        return None
    if TABLE_MARKER in answer:
        in_code = False
        for line in answer.splitlines():
            if line.lstrip().startswith(("```", "~~~")):
                in_code = not in_code
            if TABLE_MARKER in line and in_code:
                return None
        # Znacznik musi zajmować osobny akapit, aby nie utworzyć uszkodzonej tabeli.
        if not re.search(r"(?m)^\s*" + re.escape(TABLE_MARKER) + r"\s*$", answer):
            return None
        return answer.replace(TABLE_MARKER, rendered)
    return rendered + ("\n\n" + answer.strip() if answer.strip() else "")


def plain_label(text):
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = ID.sub("", text)
    text = re.sub(r"[*_`()\[\]]", "", text).replace("\\|", "|")
    return " ".join(unicodedata.normalize("NFC", text).casefold().split()).strip(" .,:;–—-")


def cells(line):
    return [part.strip() for part in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def record_rows(answer):
    """Rozpoznaj tabele rekordów; zwykłe uwagi i inne tabele nie są wykazem."""
    lines = answer.splitlines()
    rows = []
    in_code = False
    index = 0
    while index < len(lines) - 1:
        if lines[index].lstrip().startswith(("```", "~~~")):
            in_code = not in_code
        headers = cells(lines[index])
        separator = cells(lines[index + 1])
        if (not in_code and len(headers) > 1 and len(headers) == len(separator)
                and all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator)):
            names = [plain_label(header) for header in headers]
            name_index = next((i for i, name in enumerate(names) if re.search(
                r"\b(?:osoba|osoby|postać|postacie|postaci|miejscowość|miejscowości|nazwisko|nazwa|rekord|obiekt|artysta|artystka|twórca|name|person|item|place)\b", name,
            )), None)
            id_index = next((i for i, name in enumerate(names)
                             if name in {"qid", "id", "identyfikator"}), None)
            if id_index is None:
                id_index = next((i for i, name in enumerate(names) if re.search(
                    r"\b(?:qid|id|identyfikator|identyfikatory)\b", name,
                )), None)
            index += 2
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                row = cells(lines[index])
                if name_index is not None or id_index is not None:
                    # Nieprawidłowa szerokość wiersza również podlega odrzuceniu.
                    if len(row) != len(headers):
                        rows.append(("", ""))
                    else:
                        name = row[name_index] if name_index is not None else row[id_index]
                        target = row[id_index] if id_index is not None else name
                        rows.append((name, target))
                index += 1
            continue
        index += 1
    return rows


def listing_problem(answer, records):
    rows = record_rows(answer)
    if not rows:
        return "missing_table" if records else None
    if records is None:
        return "missing_selection"
    for name, target in rows:
        ids = set(ID.findall(target))
        if not ids:
            return "missing_id"
        if len(ids) != 1 or not ids <= records.keys():
            return "outside_selection"
        identifier = next(iter(ids))
        name_ids = set(ID.findall(name))
        if name_ids and name_ids != ids:
            return "name_mismatch"
        if plain_label(name) not in {plain_label(label) for label in records[identifier]}:
            return "name_mismatch"
        # Jeżeli nazwa jest odnośnikiem, jego QID musi zgadzać się z kolumną QID.
        linked = set(re.findall(r"/wiki/Item:(Q[1-9][0-9]*)\b", name))
        if linked and linked != ids:
            return "name_mismatch"
    return None
