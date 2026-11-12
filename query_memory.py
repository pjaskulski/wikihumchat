"""Pamięć wyników jednej kwerendy i odwracalne ograniczanie kontekstu modelu."""

import json
from types import SimpleNamespace


READ_TOOL = SimpleNamespace(
    name="read_tool_result",
    description=(
        "Odczytaj zachowany wynik wcześniejszego narzędzia z bieżącego pytania. "
        "Wyniki oznaczone archived mają tylko podgląd, nie pełne dane. Podaj call_id "
        "oraz offset (początkowo 0); next_offset wskazuje dalszy fragment. Wynik zawiera "
        "oryginalne argumenty i fragment JSON wyniku. Nie powtarzaj zewnętrznej kwerendy."
    ),
    inputSchema={"type": "object", "additionalProperties": False, "properties": {
        "call_id": {"type": "string", "minLength": 1},
        "offset": {"type": "integer", "minimum": 0},
        "limit": {"type": "integer", "minimum": 100, "maximum": 12000},
    }, "required": ["call_id"]},
)


def encoded_size(messages, tools):
    return len(json.dumps({"messages": messages, "tools": tools}, ensure_ascii=False))


class QueryMemory:
    def __init__(self):
        self.results = {}
        self.cache = {}

    @staticmethod
    def cache_key(name, arguments):
        return name, json.dumps(arguments, sort_keys=True, ensure_ascii=False)

    def remember(self, identifier, name, arguments, result):
        # Pamięć jest lokalna dla reply, nie współdzielona między użytkownikami.
        self.results[identifier] = (name, arguments, result)
        if not result.get("isError"):
            self.cache[self.cache_key(name, arguments)] = result

    def cached(self, name, arguments):
        return self.cache.get(self.cache_key(name, arguments))

    def read(self, arguments):
        identifier = arguments["call_id"]
        if identifier not in self.results:
            return {"isError": True, "content": [{"type": "text", "text": "Nieznane call_id bieżącej kwerendy."}]}
        name, original_arguments, result = self.results[identifier]
        raw = json.dumps(result, ensure_ascii=False)
        offset = arguments.get("offset", 0)
        limit = arguments.get("limit", 8000)
        end = min(offset + limit, len(raw))
        return {"isError": False, "content": [{"type": "text", "text": json.dumps({
            "call_id": identifier, "tool": name, "arguments": original_arguments,
            "original_is_error": bool(result.get("isError")),
            "result_json_fragment": raw[offset:end], "offset": offset,
            "next_offset": end if end < len(raw) else None, "total_chars": len(raw),
        }, ensure_ascii=False)}]}

    def pack(self, messages, tools, maximum):
        """Zastępuj duże wyniki podglądem, zachowując pełne pary protokołu."""
        packed = [dict(message) for message in messages]
        current_size = encoded_size(packed, tools)
        if current_size <= maximum * 0.85:
            return packed, 0
        changed = set()
        candidates = []
        for index, message in enumerate(messages):
            if message["role"] != "tool" or message["tool_call_id"] not in self.results:
                continue
            name, _, result = self.results[message["tool_call_id"]]
            counts = result.get("table_statistics", {}).get("unique_entity_counts", {})
            listing = name == "execute_sparql" and any(count > 1 for count in counts.values())
            # Zachowuj przede wszystkim tabele rekordów i fragmenty świadomie
            # odczytane przez model. Wcześniej usuwaliśmy je wyłącznie z racji wieku.
            priority = (0 if result.get("isError") else
                        3 if name in {READ_TOOL.name, "prepare_record_list"} else 2 if listing else 1)
            candidates.append((priority, index))
        candidates.sort()
        # Twardy limit nadal obowiązuje: zbyt duża tabela także może wymagać
        # archiwizacji. Nie skracaj pytania, instrukcji ani argumentów narzędzi.
        for priority in sorted({priority for priority, _ in candidates}):
            for preview_limit in (1000, 100):
                for candidate_priority, index in candidates:
                    if candidate_priority != priority:
                        continue
                    message = messages[index]
                    identifier = message["tool_call_id"]
                    name, arguments, result = self.results[identifier]
                    raw = message["content"]
                    if len(raw) < preview_limit + 600:
                        continue
                    stub = {
                        "archived": True, "call_id": identifier, "tool": name,
                        "isError": bool(result.get("isError")), "original_chars": len(raw),
                        "preview": raw[:preview_limit], "preview_is_complete": False,
                        "read": "read_tool_result(call_id, offset=0); kolejne fragmenty przez next_offset",
                    }
                    if "table_statistics" in result:
                        stub["table_statistics"] = result["table_statistics"]
                    replacement = {**message, "content": json.dumps(stub, ensure_ascii=False)}
                    current_size += len(json.dumps(replacement, ensure_ascii=False)) - len(
                        json.dumps(packed[index], ensure_ascii=False),
                    )
                    packed[index] = replacement
                    changed.add(identifier)
                    if current_size <= maximum * 0.75:
                        return packed, len(changed)
            # Nie archiwizuj ważniejszych danych tylko po to, by osiągnąć
            # miękki cel 75%, jeśli już pozostaje bezpieczny zapas kontekstu.
            if current_size <= maximum * 0.85:
                return packed, len(changed)
        return packed, len(changed)
