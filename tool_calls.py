"""Walidacja całej rundy narzędzi i normalizacja metadanych protokołu."""

import json
import re

from jsonschema import ValidationError


REASONS = {
    "invalid_envelope": "nieprawidłowa struktura wywołania",
    "unknown_tool": "narzędzie spoza udostępnionej listy",
    "invalid_arguments_json": "argumenty nie są poprawnym JSON",
    "arguments_not_object": "argumenty nie tworzą obiektu JSON",
    "arguments_schema": "argumenty niezgodne ze schematem narzędzia",
}


class ToolCallProblem(ValueError):
    def __init__(self, code, *, tool=None, field=None, rule=None):
        self.code = code
        self.tool = tool
        self.field = field
        self.rule = rule
        self.detail = REASONS[code]
        if tool:
            self.detail += f" (narzędzie {tool}"
            if field:
                self.detail += f", pole {field}"
            if rule:
                self.detail += f", reguła {rule}"
            self.detail += ")"
        super().__init__(self.detail)


def _reject_constant(_):
    raise ValueError("Non-JSON numeric constant")


def prepare_tool_calls(message, validators, seen_ids, round_number):
    """Nie zmienia wejścia ani seen_ids; wadliwa runda nie ma skutków ubocznych."""
    parsed = []
    normalized = []
    reserved = set(seen_ids)
    changes = 0
    for index, call in enumerate(message["tool_calls"], 1):
        if not isinstance(call, dict) or call.get("type") != "function":
            raise ToolCallProblem("invalid_envelope")
        function = call.get("function")
        if not isinstance(function, dict):
            raise ToolCallProblem("invalid_envelope")
        name = function.get("name")
        if not isinstance(name, str) or name not in validators:
            raise ToolCallProblem("unknown_tool")
        raw = function.get("arguments")
        try:
            if isinstance(raw, str):
                arguments = json.loads(raw, parse_constant=_reject_constant)
                encoded = raw
            elif isinstance(raw, dict):
                # Niektórzy dostawcy zwracają obiekt zamiast tekstowego JSON.
                encoded = json.dumps(raw, ensure_ascii=False, allow_nan=False)
                arguments = json.loads(encoded)
                changes += 1
            else:
                raise ValueError
        except (TypeError, ValueError, RecursionError):
            raise ToolCallProblem("invalid_arguments_json", tool=name) from None
        if not isinstance(arguments, dict):
            raise ToolCallProblem("arguments_not_object", tool=name)
        try:
            validators[name].validate(arguments)
        except ValidationError as error:
            # Tylko nazwy z udostępnionego schematu; nigdy wartości argumentów.
            path = list(error.absolute_path)
            properties = validators[name].schema.get("properties", {})
            field = path[0] if (
                path and isinstance(path[0], str) and path[0] in properties
                and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", path[0])
            ) else None
            rule = error.validator if error.validator in {
                "type", "required", "additionalProperties", "enum", "minimum", "maximum",
                "pattern", "minLength", "maxLength", "oneOf", "anyOf",
            } else None
            raise ToolCallProblem("arguments_schema", tool=name, field=field, rule=rule) from None
        identifier = call.get("id")
        if (
            not isinstance(identifier, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identifier)
            or identifier in reserved
        ):
            identifier = f"wikihum_call_{round_number}_{index}"
            suffix = 0
            while identifier in reserved:
                suffix += 1
                identifier = f"wikihum_call_{round_number}_{index}_{suffix}"
            changes += 1
        reserved.add(identifier)
        parsed.append((name, identifier, arguments))
        normalized.append({"id": identifier, "type": "function", "function": {
            "name": name, "arguments": encoded,
        }})
    return parsed, {"role": "assistant", "content": message.get("content"),
                    "tool_calls": normalized}, changes
