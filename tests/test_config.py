from dataclasses import replace

import pytest

from config import ConfigurationError, Settings


def test_export_format_and_process_precedence_without_environment_mutation(tmp_path):
    path = tmp_path / ".env"
    path.write_text('export AI_TEST_KEY="local-secret"\nMODEL_TIMEOUT_SECONDS=10\n')
    process = {"AI_TEST_KEY": "process-secret", "MODEL_TIMEOUT_SECONDS": "25"}
    original = process.copy()
    settings = Settings.from_env(path, environ=process)
    assert settings.model_api_key == "process-secret"
    assert settings.model_timeout_seconds == 25
    assert process == original
    assert "process-secret" not in repr(settings)


def test_process_alias_takes_precedence_over_file_primary_key(tmp_path):
    path = tmp_path / ".env"
    path.write_text("MODEL_API_KEY=file-secret\n")
    settings = Settings.from_env(path, environ={"AI_TEST_KEY": "process-secret"})
    assert settings.model_api_key == "process-secret"


def test_canonical_key_takes_precedence_in_same_source(tmp_path):
    settings = Settings.from_env(tmp_path / "absent", environ={
        "MODEL_API_KEY": "primary-secret", "AI_TEST_KEY": "alias-secret",
    })
    assert settings.model_api_key == "primary-secret"


def test_missing_key_prevents_startup(tmp_path):
    with pytest.raises(ConfigurationError, match="MODEL_API_KEY"):
        Settings.from_env(tmp_path / "absent", environ={})


@pytest.mark.parametrize("name,value", [
    ("MODEL_TIMEOUT_SECONDS", "0"),
    ("MCP_TIMEOUT_SECONDS", "nan"),
    ("CHAT_TIMEOUT_SECONDS", "inf"),
    ("TOOL_MAX_ROUNDS", "1.5"),
    ("CHAT_MAX_MESSAGES", "-1"),
    ("CHAT_MAX_MESSAGES", "1"),
    ("REQUEST_MAX_BYTES", ""),
    ("MODEL_TEMPERATURE", "2.1"),
    ("MODEL_ENABLE_THINKING", "perhaps"),
    ("MODEL_REASONING_EFFORT", "private-secret"),
    ("MODEL_NAME", " "),
    ("MODEL_BASE_URL", "https://user:private-secret@example.org/v1"),
    ("MCP_URL", "file:///tmp/private-secret"),
    ("MCP_URL", "https://example.org:invalid/private-secret"),
    ("WIKIHUM_BASE_URL", "https://user:private-secret@example.org"),
    ("APP_PUBLIC_URL", "https://example.org/subpath"),
    ("APP_PUBLIC_URL", "https://user:private-secret@example.org"),
    ("TRUSTED_PROXY_CIDRS", "not-a-network"),
    ("RATE_LIMIT_MAX_CLIENTS", "0"),
    ("TOOL_MAX_REPAIR_ATTEMPTS", "-1"),
])
def test_invalid_config_reports_field_without_value(tmp_path, name, value):
    with pytest.raises(ConfigurationError) as captured:
        Settings.from_env(tmp_path / "absent", environ={
            "AI_TEST_KEY": "api-secret", name: value,
        })
    assert name in str(captured.value)
    assert "private-secret" not in str(captured.value)
    assert "api-secret" not in str(captured.value)


def test_question_cannot_exceed_context_limit():
    with pytest.raises(ConfigurationError, match="CHAT_MAX_QUESTION_CHARS"):
        replace(Settings(model_api_key="secret"), chat_max_context_chars=100)


@pytest.mark.parametrize("effort", ["", "low", "medium", "xhigh"])
def test_reasoning_effort_from_env_with_process_precedence(tmp_path, effort):
    path = tmp_path / ".env"
    path.write_text('MODEL_API_KEY=test-key\nMODEL_REASONING_EFFORT=low\n')
    assert Settings.from_env(path, environ={}).model_reasoning_effort == "low"
    assert Settings.from_env(path, environ={"MODEL_REASONING_EFFORT": effort}).model_reasoning_effort == effort
