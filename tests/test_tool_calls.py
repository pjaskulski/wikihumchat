import json

from jsonschema.validators import validator_for
import pytest

from tool_calls import ToolCallProblem, prepare_tool_calls


SCHEMA = {"type": "object", "properties": {"query": {"type": "string"}},
          "required": ["query"], "additionalProperties": False}
VALIDATORS = {"search_items": validator_for(SCHEMA)(SCHEMA)}


def message(arguments, identifier="call_1"):
    return {"role": "assistant", "content": None, "tool_calls": [{
        "id": identifier, "type": "function", "function": {
            "name": "search_items", "arguments": arguments,
        },
    }]}


@pytest.mark.parametrize("arguments,reason", [
    ("{", "invalid_arguments_json"), ("[]", "arguments_not_object"),
    ('{"query":NaN}', "invalid_arguments_json"),
    ('{"query":12}', "arguments_schema"), ('{}', "arguments_schema"),
    ('{"query":"x","private-field":"private-value"}', "arguments_schema"),
])
def test_validation_categories_do_not_include_argument_values(arguments, reason):
    with pytest.raises(ToolCallProblem) as captured:
        prepare_tool_calls(message(arguments), VALIDATORS, set(), 1)
    assert captured.value.code == reason
    assert "private" not in str(captured.value)


def test_object_arguments_are_serialized_and_validated_without_changing_input():
    original = message({"query": "Kostka"})
    parsed, normalized, changes = prepare_tool_calls(original, VALIDATORS, set(), 1)
    assert parsed == [("search_items", "call_1", {"query": "Kostka"})]
    assert json.loads(normalized["tool_calls"][0]["function"]["arguments"]) == {"query": "Kostka"}
    assert original["tool_calls"][0]["function"]["arguments"] == {"query": "Kostka"}
    assert changes == 1


def test_failed_round_does_not_reserve_ids_or_change_message():
    original = message('{"query":"ok"}')
    original["tool_calls"].append(message("{")["tool_calls"][0])
    seen = {"old_id"}
    with pytest.raises(ToolCallProblem):
        prepare_tool_calls(original, VALIDATORS, seen, 1)
    assert seen == {"old_id"}
    assert [call["id"] for call in original["tool_calls"]] == ["call_1", "call_1"]


def test_missing_id_gets_an_unambiguous_local_id():
    parsed, normalized, changes = prepare_tool_calls(message('{"query":"ok"}', None), VALIDATORS, set(), 2)
    assert parsed[0][1] == normalized["tool_calls"][0]["id"]
    assert isinstance(parsed[0][1], str)
    assert changes == 1


@pytest.mark.parametrize("call,reason", [
    (None, "invalid_envelope"),
    ({"type": "function", "function": None}, "invalid_envelope"),
    ({"type": "function", "function": {"name": "private-unknown-tool"}}, "unknown_tool"),
])
def test_bad_envelopes_and_unknown_tools_have_safe_categories(call, reason):
    with pytest.raises(ToolCallProblem) as captured:
        prepare_tool_calls({"tool_calls": [call]}, VALIDATORS, set(), 1)
    assert captured.value.code == reason
    assert "private" not in str(captured.value)
