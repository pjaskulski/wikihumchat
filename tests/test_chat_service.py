import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from chat_service import ChatService, requests_examples, summary_messages, validate_request
from config import Settings
from errors import ChatError


def call(identifier="call_1", *, name="search_items", arguments='{"query":"Kostka"}'):
    return {"id": identifier, "type": "function",
            "function": {"name": name, "arguments": arguments}}


def assistant(content=None, calls=None):
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = calls
    return message


def build_service(responses, *, settings=None, result=None):
    settings = settings or Settings(model_api_key="secret")
    model = SimpleNamespace(complete=AsyncMock(side_effect=responses))
    tool = SimpleNamespace(name="search_items", description="Search", inputSchema={
        "type": "object", "properties": {"query": {"type": "string"}},
        "required": ["query"], "additionalProperties": False,
    })
    mcp = SimpleNamespace(
        list_tools=AsyncMock(return_value=[tool]),
        call_tool=AsyncMock(return_value=result or {
            "isError": False, "content": [{"type": "text", "text": "Q158342: Jan Kostka"}],
        }),
    )

    @asynccontextmanager
    async def model_factory(_):
        yield model

    @asynccontextmanager
    async def mcp_factory(_):
        yield mcp

    service = ChatService(settings, model_factory=model_factory, mcp_factory=mcp_factory)
    return service, model, mcp


def test_follow_up_context_and_multiple_call_ids_are_preserved():
    service, model, mcp = build_service([
        assistant(calls=[call(), call("call_2")]), assistant("Jan Kostka Q158342"),
    ])
    history = [{"role": "user", "content": "Znajdź Jana Kostkę"},
               {"role": "assistant", "content": "Którego?"}]
    result = asyncio.run(service.reply({"message": "Pierwszego", "history": history}))
    messages = model.complete.await_args.args[0]
    assert messages[1:3] == history
    assert [item["tool_call_id"] for item in messages if item["role"] == "tool"] == [
        "call_1", "call_2",
    ]
    assert mcp.call_tool.await_count == 1  # Identyczne wyszukiwanie korzysta z pamięci tej tury.
    assert result["sources"] == [{
        "id": "Q158342", "url": "https://wikihum.lab.dariah.pl/wiki/Item:Q158342",
    }]
    assert result["history"] == history + [
        {"role": "user", "content": "Pierwszego"},
        {"role": "assistant", "content": "Jan Kostka Q158342"},
    ]
    assert history[-1]["content"] == "Którego?"
    assert all(item["role"] in {"user", "assistant"} for item in result["history"])


def test_duration_covers_entire_service_reply_and_is_not_added_to_history(monkeypatch):
    ticks = iter([100.0, 596.444])
    monkeypatch.setattr("chat_service.monotonic", lambda: next(ticks))
    service, _, _ = build_service([assistant(calls=[call()]), assistant("Q158342")])
    result = asyncio.run(service.reply({"message": "Pytanie"}))
    assert result["duration_ms"] == 496444
    assert result["history"][-1] == {"role": "assistant", "content": "Q158342"}


def test_empty_final_response_retries_without_repeating_tools():
    settings = Settings(model_api_key="secret", tool_max_rounds=1)
    service, model, mcp = build_service([
        assistant(calls=[call()]),
        ChatError("model_empty_response", "Pusta odpowiedź"),
        assistant("Potwierdzono Q158342"),
    ], settings=settings)
    progress = []
    result = asyncio.run(service.reply({"message": "Pytanie"}, on_progress=progress.append))
    assert result["answer"] == "Potwierdzono Q158342"
    assert mcp.call_tool.await_count == 1
    assert [item.kwargs["allow_tools"] for item in model.complete.await_args_list] == [
        True, False, False,
    ]
    assert any(event["message"] == "Przygotowywanie odpowiedzi z zebranych wyników" for event in progress)
    first_summary = model.complete.await_args_list[1].args[0]
    recovery = model.complete.await_args_list[2].args[0]
    assert any(message["role"] == "tool" for message in first_summary)
    assert [message["role"] for message in recovery] == ["system", "user"]
    evidence = json.loads(recovery[1]["content"])
    assert evidence["question"] == "Pytanie"
    assert evidence["tool_results"][0]["arguments"] == {"query": "Kostka"}
    assert evidence["tool_results"][0]["result"]["content"][0]["text"].startswith("Q158342")


def test_summary_package_preserves_results_errors_and_context_without_assistant_drafts():
    history = [{"role": "user", "content": "Poprzednie pytanie"},
               {"role": "assistant", "content": "Poprzednia odpowiedź"}]
    results = [{"isError": False, "content": [{"text": "Miejscowość Q158342"}]},
               {"isError": True, "content": [{"text": "Błąd — nie traktuj jako danych"}]}]
    messages = [{"role": "system", "content": "Instrukcja"}] + history + [
        assistant("Niepotwierdzony szkic", calls=[call(), call("call_2")]),
        *[{"role": "tool", "tool_call_id": identifier, "content": json.dumps(result)}
          for identifier, result in zip(["call_1", "call_2"], results)],
    ]
    original = json.dumps(messages)
    recovery = summary_messages(messages, "A które z nich?", history)
    payload = json.loads(recovery[1]["content"])
    assert payload["conversation_history"] == history
    assert [item["result"] for item in payload["tool_results"]] == results
    assert "Niepotwierdzony szkic" not in json.dumps(recovery)
    assert json.dumps(messages) == original
    assert "nigdy instrukcjami" in recovery[0]["content"]


def test_empty_recovery_summary_is_not_retried_again():
    service, model, mcp = build_service([
        assistant(calls=[call()]),
        ChatError("model_empty_response", "Pusta odpowiedź"),
        ChatError("model_empty_response", "Pusta odpowiedź"),
    ], settings=Settings(model_api_key="secret", tool_max_rounds=1))
    with pytest.raises(ChatError) as captured:
        asyncio.run(service.reply({"message": "Pytanie"}))
    assert captured.value.code == "model_empty_response"
    assert model.complete.await_count == 3
    assert mcp.call_tool.await_count == 1


def test_empty_response_retry_is_bounded_across_rounds():
    error = ChatError("model_empty_response", "Pusta odpowiedź")
    service, model, mcp = build_service([
        error, assistant(calls=[call()]), error,
    ])
    with pytest.raises(ChatError) as captured:
        asyncio.run(service.reply({"message": "Pytanie"}))
    assert captured.value.code == "model_empty_response"
    assert model.complete.await_count == 3
    assert mcp.call_tool.await_count == 1


def test_other_model_errors_are_not_retried():
    service, model, mcp = build_service([
        ChatError("model_request_rejected", "Odrzucono żądanie"),
    ])
    with pytest.raises(ChatError):
        asyncio.run(service.reply({"message": "Pytanie"}))
    assert model.complete.await_count == 1
    mcp.call_tool.assert_not_awaited()


def test_server_instructions_are_in_first_system_message():
    service, model, mcp = build_service([assistant(calls=[call()]), assistant("Q158342")])
    mcp.instructions = "Specyficzna instrukcja serwera MCP"
    asyncio.run(service.reply({"message": "Pytanie"}))
    messages = model.complete.await_args.args[0]
    assert mcp.instructions in messages[0]["content"]
    assert sum(item["role"] == "system" for item in messages) == 1


def test_local_verification_requires_all_list_ids_in_answer_and_never_calls_mcp():
    args = json.dumps(dict(list_call_id="list", count_call_id="count",
                           item_column="item", count_column="n"))
    service, model, mcp = build_service([
        assistant(calls=[call("list", name="execute_sparql", arguments='{"sparql":"SELECT ?item WHERE {}"}'),
                         call("count", name="execute_sparql", arguments='{"sparql":"SELECT ?n WHERE {}"}')]),
        assistant(calls=[call("verify", name="verify_result_set", arguments=args)]),
        assistant(calls=[call("extra", name="execute_sparql", arguments='{"sparql":"SELECT ?other WHERE {}"}')]),
        assistant("Q1"),
        assistant("Q1 i Q2"),
    ])
    mcp.list_tools.return_value = [SimpleNamespace(name="execute_sparql", description="Query", inputSchema={
        "type": "object", "properties": {"sparql": {"type": "string"}}, "required": ["sparql"],
    })]
    mcp.call_tool.side_effect = [
        {"content": [{"type": "text", "text": ";item\n0;Q1\n1;Q2\n"}]},
        {"content": [{"type": "text", "text": ";n\n0;2\n"}]},
        {"content": [{"type": "text", "text": ";other\n0;Q3\n"}]},
    ]
    answer = asyncio.run(service.reply({"message": "Pytanie"}))
    assert answer["answer"] == "Q1 i Q2"
    assert answer["verified_result_ids"] == ["Q1", "Q2"]
    assert mcp.call_tool.await_count == 3
    assert model.complete.await_count == 5


@pytest.mark.parametrize("bad_row,repeat_bad", [
    ("| Adelin | — |", False),
    ("| Arkuszewo | Q3 |", False),
    ("| Augustów | Q1 |", False),
    ("| Adelin | — |", True),
])
def test_listing_control_rejects_added_records_and_repairs_without_requery(bad_row, repeat_bad, caplog):
    good = "| Miejscowość | QID |\n|---|---|\n| Biadaczów | Q1 |\n| Bębło | Q2 |"
    bad = good + "\n" + bad_row
    prepare = json.dumps({"render_table": False, "sources": [dict(
        call_id="list", item_column="item", label_column="itemLabel",
    )]})
    service, model, mcp = build_service([
        assistant(calls=[call("discovery")]),
        assistant(calls=[call("list", name="execute_sparql", arguments='{"sparql":"SELECT ?item ?itemLabel WHERE {}"}')]),
        assistant(calls=[call("prepare", name="prepare_record_list", arguments=prepare)]),
        assistant(bad), assistant(bad if repeat_bad else good),
    ])
    mcp.list_tools.return_value += [SimpleNamespace(name="execute_sparql", description="Query", inputSchema={
        "type": "object", "properties": {"sparql": {"type": "string"}}, "required": ["sparql"],
    })]
    mcp.call_tool.side_effect = [
        {"content": [{"type": "text", "text": "Q3: Arkuszewo — kandydat z wcześniejszego wyszukiwania"}]},
        {"content": [{"type": "text", "text": "item;itemLabel\nQ1;Biadaczów\nQ2;Bębło\n"}]},
    ]
    if repeat_bad:
        with pytest.raises(ChatError) as captured:
            asyncio.run(service.reply({"message": "Jakie miejscowości?"}))
        assert captured.value.code == "ungrounded_listing"
    else:
        result = asyncio.run(service.reply({"message": "Jakie miejscowości?"}))
        assert result["answer"] == good
        assert {source["id"] for source in result["sources"]} == {"Q1", "Q2"}
    assert mcp.call_tool.await_count == 2  # Lokalne przygotowanie nie wywołuje MCP.
    assert model.complete.await_count == 5
    assert "chat_listing_rejected" in caplog.text
    assert all(name not in caplog.text for name in ("Adelin", "Arkuszewo", "Augustów"))


def test_table_without_selection_gets_one_request_to_select_original_results():
    good = "| Miejscowość | QID |\n|---|---|\n| Biadaczów | Q1 |\n| Bębło | Q2 |"
    service, model, mcp = build_service([
        assistant(calls=[call("list", name="execute_sparql", arguments='{"sparql":"SELECT ?item ?itemLabel WHERE {}"}')]),
        assistant(good),
        assistant(calls=[call("prepare", name="prepare_record_list", arguments=json.dumps({"render_table": False, "sources": [dict(
            call_id="list", item_column="item", label_column="itemLabel",
        )]}))]),
        assistant(good),
    ], result={"content": [{"type": "text", "text": "item;itemLabel\nQ1;Biadaczów\nQ2;Bębło\n"}]})
    mcp.list_tools.return_value = [SimpleNamespace(name="execute_sparql", description="Query", inputSchema={
        "type": "object", "properties": {"sparql": {"type": "string"}}, "required": ["sparql"],
    })]
    result = asyncio.run(service.reply({"message": "Jakie miejscowości?"}))
    assert result["answer"] == good
    assert mcp.call_tool.await_count == 1
    assert model.complete.await_count == 4


def test_listing_selection_and_validation_use_original_archived_table():
    good = "| Miejscowość | QID |\n|---|---|\n| Biadaczów | Q1 |\n| Bębło | Q2 |"
    service, model, mcp = build_service([
        assistant(calls=[call("list", name="execute_sparql", arguments='{"sparql":"SELECT ?item ?itemLabel ?description WHERE {}"}')]),
        assistant(calls=[call("prepare", name="prepare_record_list", arguments=json.dumps({"render_table": False, "sources": [dict(
            call_id="list", item_column="item", label_column="itemLabel",
        )]}))]),
        assistant(good),
    ], settings=Settings(model_api_key="secret", chat_max_context_chars=30000), result={
        "content": [{"type": "text", "text": "item;itemLabel;description\nQ1;Biadaczów;" + "opis " * 4000
                     + "\nQ2;Bębło;" + "opis " * 4000 + "\n"}],
    })
    mcp.list_tools.return_value = [SimpleNamespace(name="execute_sparql", description="Query", inputSchema={
        "type": "object", "properties": {"sparql": {"type": "string"}}, "required": ["sparql"],
    })]
    result = asyncio.run(service.reply({"message": "Jakie miejscowości?"}))
    assert result["answer"] == good
    assert mcp.call_tool.await_count == 1
    sent = model.complete.await_args.args[0]
    assert any('"archived": true' in (message.get("content") or "") for message in sent)


@pytest.mark.parametrize("recovery", [None, "bad_table", "timeout"])
def test_application_renders_and_deduplicates_table_and_model_only_writes_notes(recovery):
    marker = "[[WIKIHUM_RECORD_LIST]]"
    responses = [
        assistant(calls=[call("list", name="execute_sparql", arguments='{"sparql":"SELECT ?item ?itemLabel ?typeLabel WHERE {}"}')]),
        assistant(calls=[call("prepare", name="prepare_record_list", arguments=json.dumps({
            "sources": [dict(call_id="list", item_column="item", label_column="itemLabel",
                             columns=[dict(column="typeLabel", heading="Typ")])],
            "name_heading": "Miejscowość",
        }))]),
    ]
    if recovery == "bad_table":
        responses.append(assistant("| Miejscowość | QID |\n|---|---|\n| Adelin | — |"))
    elif recovery == "timeout":
        responses.append(ChatError("model_timeout", "Timeout", 504))
    responses.append(assistant("Wykaz historyczny.\n\n" + marker + "\n\nUwagi historyczne."))
    service, model, mcp = build_service(responses, result={"content": [{"type": "text", "text":
        "item;itemLabel;typeLabel\nQ1;Biadaczów;wieś\nQ1;Biadaczów;zamek\nQ2;Bębło;wieś\n",
    }]})
    mcp.list_tools.return_value = [SimpleNamespace(name="execute_sparql", description="Query", inputSchema={
        "type": "object", "properties": {"sparql": {"type": "string"}}, "required": ["sparql"],
    })]
    result = asyncio.run(service.reply({"message": "Jakie miejscowości?"}))
    assert marker not in result["answer"]
    assert result["answer"].count("| Biadaczów |") == 1
    assert "wieś; zamek" in result["answer"]
    assert "Bębło" in result["answer"] and "Adelin" not in result["answer"]
    assert result["history"][-1]["content"] == result["answer"]
    assert "Uwagi historyczne." in result["answer"]
    assert mcp.call_tool.await_count == 1
    assert model.complete.await_args.kwargs["allow_tools"] is False
    evidence = json.loads(model.complete.await_args.args[0][1]["content"])["tool_results"]
    assert all(item["tool"] != "execute_sparql" for item in evidence)
    assert any(item["tool"] == "prepare_record_list" for item in evidence)
    assert model.complete.await_count == (3 if recovery is None else 4)


def test_ready_table_does_not_skip_explicit_completeness_verification():
    service, model, mcp = build_service([
        assistant(calls=[call("list", name="execute_sparql", arguments='{"sparql":"SELECT ?item ?itemLabel WHERE {}"}'),
                         call("count", name="execute_sparql", arguments='{"sparql":"SELECT ?n WHERE {}"}')]),
        assistant(calls=[call("prepare", name="prepare_record_list", arguments=json.dumps({"sources": [dict(
            call_id="list", item_column="item", label_column="itemLabel",
        )]}))]),
        assistant(calls=[call("verify", name="verify_result_set", arguments=json.dumps(dict(
            list_call_id="list", count_call_id="count", item_column="item", count_column="n",
        )))]),
        assistant("[[WIKIHUM_RECORD_LIST]]"),
    ])
    mcp.list_tools.return_value = [SimpleNamespace(name="execute_sparql", description="Query", inputSchema={
        "type": "object", "properties": {"sparql": {"type": "string"}}, "required": ["sparql"],
    })]
    mcp.call_tool.side_effect = [
        {"content": [{"type": "text", "text": "item;itemLabel\nQ1;Jan\nQ2;Piotr\n"}]},
        {"content": [{"type": "text", "text": "n\n2\n"}]},
    ]
    result = asyncio.run(service.reply({"message": "Podaj pełny wykaz osób"}))
    assert result["verified_result_ids"] == ["Q1", "Q2"]
    assert [entry.kwargs["allow_tools"] for entry in model.complete.await_args_list] == [True, True, True, False]
    assert mcp.call_tool.await_count == 2


def test_public_progress_reports_actual_stages_without_private_contents():
    service, _, _ = build_service([
        assistant(calls=[call()]), assistant("Potwierdzono Q158342"),
    ], result={"isError": False, "content": [{"text": "Q158342 private-tool-result"}]})
    progress = []
    result = asyncio.run(service.reply({"message": "private-user-question"}, on_progress=progress.append))
    assert result["answer"] == "Potwierdzono Q158342"
    starts = [event for event in progress if event["state"] == "running"]
    ends = [event for event in progress if event["state"] != "running"]
    assert {event["id"] for event in starts} == {event["id"] for event in ends}
    assert any(event.get("tool") == "search_items" for event in starts)
    assert all(event["seconds"] >= 0 for event in ends)
    serialized = json.dumps(progress)
    assert "private" not in serialized
    assert "arguments" not in serialized
    assert "history" not in serialized
    assert "reasoning" not in serialized
    assert "get_wikibase_info" not in serialized  # Nie zgłaszaj niewykonanego narzędzia.


@pytest.mark.parametrize("bad_call", [
    call(name="not_advertised"), call(arguments="[]"), call(arguments="{"),
    call(arguments='{"query":12}'), call(arguments='{"query":"x","extra":1}'),
])
def test_invalid_round_executes_no_tools(bad_call):
    settings = replace(Settings(model_api_key="secret"), tool_max_repair_attempts=0)
    service, _, mcp = build_service([assistant(calls=[call(), bad_call])], settings=settings)
    with pytest.raises(ChatError) as captured:
        asyncio.run(service.reply({"message": "Pytanie"}))
    assert captured.value.code == "invalid_tool_call"
    mcp.call_tool.assert_not_awaited()


def test_duplicate_call_ids_are_normalized_with_matching_tool_responses():
    service, model, mcp = build_service([
        assistant(calls=[call(), call()]), assistant("Potwierdzono Q158342"),
    ])
    asyncio.run(service.reply({"message": "Pytanie"}))
    assert mcp.call_tool.await_count == 1
    messages = model.complete.await_args.args[0]
    calls = next(item["tool_calls"] for item in messages if item.get("tool_calls"))
    identifiers = [item["id"] for item in calls]
    assert len(set(identifiers)) == 2
    assert [item["tool_call_id"] for item in messages if item["role"] == "tool"] == identifiers


def test_invalid_arguments_are_repaired_without_execution_or_loss_of_previous_results():
    service, model, mcp = build_service([
        assistant(calls=[call()]),
        assistant(calls=[call("call_2", arguments='{"query":12}')]),
        assistant(calls=[call("call_3", arguments='{"query":"Poprawione"}')]),
        assistant("Potwierdzono Q158342"),
    ])
    progress = []
    result = asyncio.run(service.reply({"message": "Pytanie"}, on_progress=progress.append))
    assert result["answer"] == "Potwierdzono Q158342"
    assert mcp.call_tool.await_count == 2
    assert mcp.call_tool.await_args.args[1] == {"query": "Poprawione"}
    messages = model.complete.await_args.args[0]
    assert any(item.get("tool_call_id") == "call_1" for item in messages)
    assert not any(item.get("tool_call_id") == "call_2" for item in messages)
    assert [index for index, item in enumerate(messages) if item["role"] == "system"] == [0]
    assert any("Poprawianie wywołania" in event["message"] for event in progress)


def test_tool_repairs_are_bounded_and_error_does_not_reveal_values(caplog):
    settings = replace(Settings(model_api_key="secret"), tool_max_repair_attempts=1)
    bad = call(arguments='{"query":12,"private-sensitive-argument":"private-value"}')
    service, model, mcp = build_service([assistant(calls=[bad]), assistant(calls=[bad])], settings=settings)
    with pytest.raises(ChatError) as captured:
        asyncio.run(service.reply({"message": "private-question"}))
    assert captured.value.code == "invalid_tool_call"
    assert "schematem" in captured.value.message
    assert "private" not in captured.value.message
    assert "private" not in caplog.text
    assert model.complete.await_count == 2
    mcp.call_tool.assert_not_awaited()


def test_reused_id_across_rounds_does_not_abort_a_valid_query():
    service, model, mcp = build_service([
        assistant(calls=[call()]), assistant(calls=[call()]), assistant("Potwierdzono Q158342"),
    ])
    asyncio.run(service.reply({"message": "Pytanie"}))
    assert mcp.call_tool.await_count == 1
    messages = model.complete.await_args.args[0]
    ids = [item["tool_call_id"] for item in messages if item["role"] == "tool"]
    assert len(set(ids)) == 2


def test_per_round_call_limit_prevents_all_execution():
    settings = replace(Settings(model_api_key="secret"), tool_max_calls_per_round=1)
    service, _, mcp = build_service([
        assistant(calls=[call(), call("call_2")]),
    ], settings=settings)
    with pytest.raises(ChatError) as captured:
        asyncio.run(service.reply({"message": "Pytanie"}))
    assert captured.value.code == "tool_call_limit"
    mcp.call_tool.assert_not_awaited()


def test_sources_exclude_unobserved_ids_and_failed_results():
    service, _, _ = build_service([
        assistant(calls=[call()]), assistant("Q158342 i Q999999"),
        assistant("Q158342 i Q999999"),
    ], result={"isError": True, "content": [{"type": "text", "text": "Q158342"}]})
    with pytest.raises(ChatError) as captured:
        asyncio.run(service.reply({"message": "Pytanie"}))
    assert captured.value.code == "no_evidence"


def test_successful_results_do_not_confirm_invented_source():
    service, _, _ = build_service([
        assistant(calls=[call()]), assistant("Q158342 i Q999999"),
    ])
    result = asyncio.run(service.reply({"message": "Pytanie"}))
    assert [item["id"] for item in result["sources"]] == ["Q158342"]


def test_ids_after_newlines_in_tool_text_are_not_lost_during_json_serialization():
    service, _, _ = build_service([
        assistant(calls=[call()]), assistant("Znaleziono Q158342 i Q158343."),
    ], result={"isError": False, "content": [
        {"type": "text", "text": "Q158342: pierwsza osoba\nQ158343: druga osoba"},
    ]})
    result = asyncio.run(service.reply({"message": "Pytanie"}))
    assert {source["id"] for source in result["sources"]} == {"Q158342", "Q158343"}


def test_sparql_with_unconfirmed_ids_is_not_executed_and_model_can_repair():
    invalid = "SELECT ?x WHERE { ?x wdt:P1448 wd:Q5 }"
    valid = "SELECT ?x WHERE { ?x wdt:P27 wd:Q5 }"
    service, model, mcp = build_service([
        assistant(calls=[call()]),
        assistant(calls=[call("sql_1", name="execute_sparql", arguments=json.dumps({"sparql": invalid}))]),
        assistant(calls=[call("sql_2", name="execute_sparql", arguments=json.dumps({"sparql": valid}))]),
        assistant("Potwierdzono Q158342"),
    ], result={"isError": False, "content": [{"text": "Q158342: osoba\nP27: typ\nQ5: człowiek"}]})
    mcp.list_tools.return_value.append(SimpleNamespace(
        name="execute_sparql", description="SPARQL", inputSchema={
            "type": "object", "properties": {"sparql": {"type": "string"}}, "required": ["sparql"],
        },
    ))
    result = asyncio.run(service.reply({"message": "Sprawdź osoby"}))
    assert result["answer"] == "Potwierdzono Q158342"
    assert mcp.call_tool.await_count == 2
    assert mcp.call_tool.await_args.args == ("execute_sparql", {"sparql": valid})
    tool_messages = [message for message in model.complete.await_args.args[0] if message["role"] == "tool"]
    assert any(json.loads(message["content"]).get("isError") for message in tool_messages)


def test_sparql_can_use_ids_explicitly_provided_by_user():
    query = "SELECT ?x WHERE { ?x wdt:P1448 wd:Q5 }"
    service, _, mcp = build_service([
        assistant(calls=[call(name="execute_sparql", arguments=json.dumps({"sparql": query}))]),
        assistant("Potwierdzono Q158342"),
    ])
    mcp.list_tools.return_value = [SimpleNamespace(
        name="execute_sparql", description="SPARQL", inputSchema={
            "type": "object", "properties": {"sparql": {"type": "string"}}, "required": ["sparql"],
        },
    )]
    asyncio.run(service.reply({"message": "Sprawdź P1448 dla Q5"}))
    mcp.call_tool.assert_awaited_once()


def test_round_limit_disables_tools_and_logs_without_user_warning(caplog):
    settings = replace(Settings(model_api_key="secret"), tool_max_rounds=1)
    service, model, mcp = build_service([
        assistant(calls=[call()]), assistant("Znaleziono Q158342."),
    ], settings=settings)
    result = asyncio.run(service.reply({"message": "Pytanie"}))
    assert model.complete.await_args.kwargs["allow_tools"] is False
    assert mcp.call_tool.await_count == 1
    assert not any("limit rund" in item for item in result["warnings"])
    assert "chat_tool_round_limit max_rounds=1" in caplog.text
    messages = model.complete.await_args.args[0]
    assert [index for index, item in enumerate(messages) if item["role"] == "system"] == [0]
    assert "Osiągnięto limit rund" in messages[0]["content"]


def test_unverified_listing_at_round_limit_is_logged_without_user_warning(caplog):
    service, _, mcp = build_service([
        assistant(calls=[call(name="execute_sparql", arguments='{"sparql":"SELECT ?item WHERE {}"}')]),
        assistant("Q1 i Q2"),
    ], settings=Settings(model_api_key="secret", tool_max_rounds=1), result={
        "content": [{"type": "text", "text": ";item\n0;Q1\n1;Q2\n"}],
    })
    mcp.list_tools.return_value = [SimpleNamespace(name="execute_sparql", description="Query", inputSchema={
        "type": "object", "properties": {"sparql": {"type": "string"}}, "required": ["sparql"],
    })]
    result = asyncio.run(service.reply({"message": "Pytanie"}))
    assert result["verified_result_ids"] is None
    assert not result["warnings"]
    assert "chat_result_set_unverified" in caplog.text


def test_model_cannot_ignore_round_limit():
    settings = replace(Settings(model_api_key="secret"), tool_max_rounds=1)
    service, _, mcp = build_service([
        assistant(calls=[call()]), assistant(calls=[call("call_2")]),
    ], settings=settings)
    with pytest.raises(ChatError) as captured:
        asyncio.run(service.reply({"message": "Pytanie"}))
    assert captured.value.code == "tool_round_limit"
    assert mcp.call_tool.await_count == 1


def test_result_size_limit_is_measured_in_utf8_bytes():
    settings = replace(Settings(model_api_key="secret"), tool_max_result_bytes=150)
    service, model, _ = build_service([assistant(calls=[call()])], settings=settings,
                                    result={"content": "ą" * 100, "isError": False})
    with pytest.raises(ChatError) as captured:
        asyncio.run(service.reply({"message": "Pytanie"}))
    assert captured.value.code == "tool_result_too_large"
    assert model.complete.await_count == 1


def test_large_tool_results_are_archived_before_exceeding_context():
    settings = replace(Settings(model_api_key="secret"), chat_max_context_chars=18000)
    service, model, _ = build_service([assistant(calls=[call()]), assistant("Potwierdzono Q158342")], settings=settings,
                                    result={"content": "Q158342 " + "x" * 30000, "isError": False})
    result = asyncio.run(service.reply({"message": "Pytanie"}))
    assert result["answer"] == "Potwierdzono Q158342"
    assert model.complete.await_count == 2
    sent = model.complete.await_args.args[0]
    assert any('"archived": true' in (item.get("content") or "") for item in sent)
    service._check_context(sent, model.complete.await_args.args[1])


def test_archived_result_can_be_read_without_another_mcp_request():
    # Budżet obejmuje również definicję przygotowywania tabel i instrukcje.
    settings = Settings(model_api_key="secret", chat_max_context_chars=24000)
    service, model, mcp = build_service([
        assistant(calls=[call()]),
        assistant(calls=[call("read", name="read_tool_result", arguments=json.dumps({
            "call_id": "call_1", "offset": 29000, "limit": 2000,
        }))]),
        assistant("Potwierdzono Q158342"),
    ], settings=settings, result={"isError": False, "content": [{
        "type": "text", "text": "x" * 30000 + " Q158342: Potwierdzony opis",
    }]})
    result = asyncio.run(service.reply({"message": "Pytanie"}))
    assert mcp.call_tool.await_count == 1
    assert result["sources"][0]["id"] == "Q158342"
    sent = model.complete.await_args.args[0]
    restored = [item for item in sent if item.get("tool_call_id") == "read"][0]
    assert "Potwierdzony opis" in restored["content"]


def test_reading_an_archived_error_does_not_confirm_entity_ids():
    query = "SELECT ?item WHERE { ?item wdt:P999 wd:Q999 }"
    service, model, mcp = build_service([
        assistant(calls=[call("failed")]),
        assistant(calls=[call("read", name="read_tool_result", arguments='{"call_id":"failed"}')]),
        assistant(calls=[call("query", name="execute_sparql", arguments=json.dumps({"sparql": query}))]),
        assistant(calls=[call("good", arguments='{"query":"inna osoba"}')]),
        assistant("Potwierdzono Q158342"),
    ])
    mcp.list_tools.return_value += [SimpleNamespace(name="execute_sparql", description="Query", inputSchema={
        "type": "object", "properties": {"sparql": {"type": "string"}}, "required": ["sparql"],
    })]
    mcp.call_tool.side_effect = [
        {"isError": True, "content": [{"type": "text", "text": "Błąd Q999 P999"}]},
        {"isError": False, "content": [{"type": "text", "text": "Q158342"}]},
    ]
    result = asyncio.run(service.reply({"message": "Pytanie"}))
    assert mcp.call_tool.await_count == 2  # Nie wykonano SPARQL opartego na treści błędu.
    assert "Q999" not in [source["id"] for source in result["sources"]]


def test_request_for_examples_does_not_force_exhaustive_count():
    service, model, mcp = build_service([
        assistant(calls=[call(name="execute_sparql", arguments='{"sparql":"SELECT ?item WHERE {}"}')]),
        assistant("Przykłady: Q1 i Q2"),
    ], result={"content": [{"type": "text", "text": ";item\n0;Q1\n1;Q2\n"}]})
    mcp.list_tools.return_value = [SimpleNamespace(name="execute_sparql", description="Query", inputSchema={
        "type": "object", "properties": {"sparql": {"type": "string"}}, "required": ["sparql"],
    })]
    result = asyncio.run(service.reply({"message": "Podaj przykłady artystów"}))
    assert model.complete.await_count == 2
    assert result["verified_result_ids"] is None
    assert not result["warnings"]


@pytest.mark.parametrize("question,extra_calls", [
    ("Jakich znasz artystów związanych z Krakowem?", 0),
    ("Podaj pełny wykaz artystów związanych z Krakowem", 1),
])
def test_only_explicit_complete_listing_requires_extra_verification(question, extra_calls):
    service, model, mcp = build_service([
        assistant(calls=[call(name="execute_sparql", arguments='{"sparql":"SELECT ?item WHERE {}"}')]),
        assistant("Jan Q1 i Piotr Q2"), assistant("Jan Q1 i Piotr Q2"),
    ], result={"content": [{"type": "text", "text": ";item\n0;Q1\n1;Q2\n"}]})
    mcp.list_tools.return_value = [SimpleNamespace(name="execute_sparql", description="Query", inputSchema={
        "type": "object", "properties": {"sparql": {"type": "string"}}, "required": ["sparql"],
    })]
    answer = asyncio.run(service.reply({"message": question}))
    assert answer["answer"] == "Jan Q1 i Piotr Q2"
    assert model.complete.await_count == 2 + extra_calls
    assert mcp.call_tool.await_count == 1


@pytest.mark.parametrize("synthesis_fails", [False, True])
def test_timeout_after_listing_attempts_one_synthesis_without_more_tools(synthesis_fails, caplog):
    timeout = ChatError("model_timeout", "Timeout", 504)
    service, model, mcp = build_service([
        assistant(calls=[call(name="execute_sparql", arguments='{"sparql":"SELECT ?item WHERE {}"}')]),
        timeout, timeout if synthesis_fails else assistant("Jan Q1 i Piotr Q2"),
    ], result={"content": [{"type": "text", "text": ";item;name\n0;Q1;Jan\n1;Q2;Piotr\n"}]})
    mcp.list_tools.return_value = [SimpleNamespace(name="execute_sparql", description="Query", inputSchema={
        "type": "object", "properties": {"sparql": {"type": "string"}}, "required": ["sparql"],
    })]
    if synthesis_fails:
        with pytest.raises(ChatError) as captured:
            asyncio.run(service.reply({"message": "Jakich znasz artystów?"}))
        assert captured.value.code == "model_timeout"
    else:
        result = asyncio.run(service.reply({"message": "Jakich znasz artystów?"}))
        assert result["answer"] == "Jan Q1 i Piotr Q2"
    assert model.complete.await_count == 3
    assert mcp.call_tool.await_count == 1
    last = model.complete.await_args
    assert last.kwargs["allow_tools"] is False
    assert [message["role"] for message in last.args[0]] == ["system", "user"]
    evidence = json.loads(last.args[0][1]["content"])["tool_results"]
    assert "Piotr" in evidence[0]["result"]["content"][0]["text"]
    assert "chat_timeout_synthesis" in caplog.text
    assert "chat_tool_round_limit" not in caplog.text


def test_timeout_without_listing_does_not_synthesize_from_search_alone():
    service, model, _ = build_service([
        assistant(calls=[call()]), ChatError("model_timeout", "Timeout", 504),
    ])
    with pytest.raises(ChatError) as captured:
        asyncio.run(service.reply({"message": "Jakich znasz artystów?"}))
    assert captured.value.code == "model_timeout"
    assert model.complete.await_count == 2


def test_timeout_synthesis_does_not_reset_whole_request_deadline():
    service, model, mcp = build_service([], settings=Settings(
        model_api_key="secret", chat_timeout_seconds=0.02,
    ), result={"content": [{"type": "text", "text": ";item\n0;Q1\n1;Q2\n"}]})
    mcp.list_tools.return_value = [SimpleNamespace(name="execute_sparql", description="Query", inputSchema={
        "type": "object", "properties": {"sparql": {"type": "string"}}, "required": ["sparql"],
    })]

    async def respond(*_, **__):
        if model.complete.await_count == 1:
            return assistant(calls=[call(name="execute_sparql", arguments='{"sparql":"SELECT ?item WHERE {}"}')])
        if model.complete.await_count == 2:
            raise ChatError("model_timeout", "Timeout", 504)
        await asyncio.sleep(10)

    model.complete.side_effect = respond
    with pytest.raises(ChatError) as captured:
        asyncio.run(service.reply({"message": "Jakich znasz artystów?"}))
    assert captured.value.code == "chat_timeout"
    assert model.complete.await_count == 3


def test_explicit_full_list_takes_priority_over_examples_wording():
    assert not requests_examples("Jakich znasz artystów?")
    assert not requests_examples("Jakie znasz miejscowości?")
    assert requests_examples("Podaj kilka przykładów artystów")
    assert requests_examples("Podaj przykłady artystów pełniących funkcje miejskie")
    assert not requests_examples("Jakich znasz artystów? Podaj pełny wykaz.")


def test_broad_question_has_no_injected_selection_or_numeric_cap():
    service, model, _ = build_service([assistant(calls=[call()]), assistant("Q158342")])
    asyncio.run(service.reply({"message": "Jakich znasz artystów związanych z Krakowem?"}))
    system = model.complete.await_args.args[0][0]["content"]
    assert "8–12" not in system
    assert "Zakres tej odpowiedzi: użytkownik prosi o przykłady." not in system
    assert "możliwie szeroki, zweryfikowany wykaz" in system


def test_whole_request_deadline_covers_wait_for_model():
    settings = replace(Settings(model_api_key="secret"), chat_timeout_seconds=0.01)
    service, model, _ = build_service([], settings=settings)

    async def stalled(*_, **__):
        await asyncio.sleep(10)

    model.complete.side_effect = stalled
    with pytest.raises(ChatError) as captured:
        asyncio.run(service.reply({"message": "Pytanie"}))
    assert captured.value.code == "chat_timeout"


def test_separate_requests_do_not_retain_conversation_state():
    service, model, _ = build_service([
        assistant(calls=[call()]), assistant("Odpowiedź A"),
        assistant(calls=[call()]), assistant("Odpowiedź B"),
    ])
    asyncio.run(service.reply({"message": "Pytanie A"}))
    result = asyncio.run(service.reply({"message": "Pytanie B"}))
    assert "Pytanie A" not in str(model.complete.await_args.args[0])
    assert len(result["history"]) == 2


@pytest.mark.parametrize("payload", [
    None, [], {}, {"message": " "}, {"message": 12},
    {"message": "x", "model": "other"},
    {"message": "x", "history": [{"role": "user", "content": "x"}]},
    {"message": "x", "history": [
        {"role": "system", "content": "x"}, {"role": "assistant", "content": "y"},
    ]},
    {"message": "x", "history": [
        {"role": "user", "content": "x", "tool_calls": []},
        {"role": "assistant", "content": "y"},
    ]},
])
def test_invalid_input_is_rejected_before_opening_connections(payload):
    factory = AsyncMock()
    service = ChatService(Settings(model_api_key="secret"), model_factory=factory)
    with pytest.raises(ChatError) as captured:
        asyncio.run(service.reply(payload))
    assert captured.value.status == 400
    factory.assert_not_called()


def test_history_is_trimmed_in_whole_pairs_and_reports_warning():
    settings = replace(Settings(model_api_key="secret"), chat_max_messages=4)
    service, _, _ = build_service([
        assistant(calls=[call()]), assistant("Nowa odpowiedź"),
    ], settings=settings)
    history = [{"role": role, "content": str(index)}
               for index in range(4) for role in ("user", "assistant")]
    result = asyncio.run(service.reply({"message": "Nowe pytanie", "history": history}))
    assert len(result["history"]) == 4
    assert result["history"][:2] == history[-2:]
    assert result["warnings"]


def test_answer_from_history_without_fresh_query_is_rejected():
    service, model, _ = build_service([assistant("Odpowiedź z historii")] * 2)
    with pytest.raises(ChatError) as captured:
        asyncio.run(service.reply({"message": "Pytanie"}))
    assert captured.value.code == "no_evidence"
    assert model.complete.await_args.kwargs["require_tools"] is True


def test_metadata_only_does_not_confirm_historical_answer():
    service, _, mcp = build_service([
        assistant(calls=[call(name="get_wikibase_info", arguments="{}")]),
        assistant("Odpowiedź historyczna"), assistant("Odpowiedź historyczna"),
    ])
    mcp.list_tools.return_value = [SimpleNamespace(
        name="get_wikibase_info", description="Metadata", inputSchema={"type": "object"},
    )]
    with pytest.raises(ChatError) as captured:
        asyncio.run(service.reply({"message": "Pytanie"}))
    assert captured.value.code == "no_evidence"


def test_premature_answer_gets_one_repair_and_is_not_returned_as_verified():
    service, model, mcp = build_service([
        assistant("Przedwczesna odpowiedź z historii"),
        assistant(calls=[call()]), assistant("Potwierdzono Q158342"),
    ])
    result = asyncio.run(service.reply({"message": "Pytanie uzupełniające"}))
    assert result["answer"] == "Potwierdzono Q158342"
    assert "Przedwczesna" not in str(result["history"])
    assert mcp.call_tool.await_count == 1
    assert model.complete.await_args_list[1].kwargs["require_tools"] is True
    messages = model.complete.await_args.args[0]
    assert [index for index, item in enumerate(messages) if item["role"] == "system"] == [0]
    assert "Ta odpowiedź nie została przyjęta" in messages[0]["content"]


def test_long_question_and_history_are_rejected():
    settings = replace(Settings(model_api_key="secret"), chat_max_question_chars=5,
                       chat_max_context_chars=10)
    for payload, code in [
        ({"message": "123456"}, "question_too_long"),
        ({"message": "12345", "history": [
            {"role": "user", "content": "12345"},
            {"role": "assistant", "content": "12345"},
        ]}, "context_too_large"),
    ]:
        with pytest.raises(ChatError) as captured:
            validate_request(payload, settings)
        assert captured.value.code == code
