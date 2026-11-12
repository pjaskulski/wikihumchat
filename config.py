"""Konfiguracja serwera; bez połączeń sieciowych i zmian środowiska procesu."""

from dataclasses import dataclass, field, fields
from math import isfinite
from ipaddress import ip_network
import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parent


class ConfigurationError(ValueError):
    """Błąd ustawień; komunikat zawiera nazwę pola, bez jego wartości."""


@dataclass(frozen=True)
class Settings:
    model_api_key: str = field(default="", repr=False)
    model_base_url: str = "https://ai-test.ihpan.edu.pl/v1"
    model_name: str = "qwen3.8-flash-next-fp8"
    model_timeout_seconds: float = 90.0
    model_max_output_tokens: int = 1500
    model_temperature: float = 0.0
    model_enable_thinking: bool = True
    model_reasoning_effort: str = ""
    model_diagnostics_file: str = "logs/model-diagnostics.jsonl"
    mcp_url: str = "https://wb-mcp.wmcloud.org/wikihum/mcp"
    wikihum_base_url: str = "https://wikihum.lab.dariah.pl"
    mcp_timeout_seconds: float = 30.0
    chat_timeout_seconds: float = 300.0
    tool_max_rounds: int = 20
    tool_max_calls_per_round: int = 10
    tool_max_repair_attempts: int = 2
    tool_max_result_bytes: int = 100_000
    chat_max_messages: int = 20
    chat_max_question_chars: int = 8000
    chat_max_context_chars: int = 60_000
    request_max_bytes: int = 262_144
    chat_max_concurrent_requests: int = 4
    rate_limit_requests: int = 10
    rate_limit_window_seconds: int = 60
    rate_limit_max_clients: int = 10_000
    trusted_proxy_cidrs: str = ""
    app_public_url: str = ""

    def __post_init__(self):
        if not self.model_api_key.strip():
            raise ConfigurationError("Wymagany MODEL_API_KEY lub AI_TEST_KEY.")
        if not self.model_name.strip():
            raise ConfigurationError("MODEL_NAME nie może być pusty.")
        if self.model_reasoning_effort not in ("", "low", "medium", "xhigh"):
            raise ConfigurationError("MODEL_REASONING_EFFORT: dozwolone wartości: low, medium, xhigh lub pusta.")
        for name in ("model_base_url", "mcp_url", "wikihum_base_url", "app_public_url"):
            if name == "app_public_url" and not self.app_public_url:
                continue
            try:
                url = urlsplit(getattr(self, name))
                valid = (
                    url.scheme in {"https", "http"} and url.hostname
                    and not url.username and not url.password
                    and not url.query and not url.fragment
                    and not any(char.isspace() for char in getattr(self, name))
                )
                # Wymuś także sprawdzenie składni i zakresu portu.
                if url.port == 0:
                    valid = False
                if url.hostname:
                    url.hostname.encode("idna")
                if name == "app_public_url" and url.path not in {"", "/"}:
                    valid = False
            except (ValueError, UnicodeError):
                valid = False
            if not valid:
                raise ConfigurationError(
                    f"{name.upper()}: wymagany adres HTTP(S) bez danych logowania, "
                    "zapytania i fragmentu."
                )
        try:
            for value in self.trusted_proxy_cidrs.split(","):
                if value.strip():
                    ip_network(value.strip(), strict=False)
        except ValueError:
            raise ConfigurationError("TRUSTED_PROXY_CIDRS: wymagane adresy lub sieci IP.") from None
        for item in fields(self):
            value = getattr(self, item.name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            if item.name == "model_temperature":
                valid = isfinite(value) and 0 <= value <= 2
            elif item.name == "tool_max_repair_attempts":
                valid = isfinite(value) and value >= 0
            else:
                valid = isfinite(value) and value > 0
            if not valid:
                raise ConfigurationError(f"{item.name.upper()}: niepoprawny zakres liczbowy.")
        if self.chat_max_question_chars > self.chat_max_context_chars:
            raise ConfigurationError(
                "CHAT_MAX_QUESTION_CHARS nie może przekraczać CHAT_MAX_CONTEXT_CHARS."
            )
        if self.chat_max_messages < 2:
            raise ConfigurationError("CHAT_MAX_MESSAGES musi wynosić co najmniej 2.")

    @classmethod
    def from_env(cls, env_file=ROOT / ".env", *, environ=None):
        """Zmienne procesu mają pierwszeństwo przed lokalnym plikiem .env."""
        process = os.environ if environ is None else environ
        path = Path(env_file)
        local = dotenv_values(path, interpolate=False) if path.is_file() else {}
        merged = {**local, **process}
        values = {}
        for item in fields(cls):
            if item.name == "model_api_key":
                # Także alias procesu ma pierwszeństwo przed kluczem z pliku.
                values[item.name] = next((
                    source.get(name, "").strip()
                    for source in (process, local)
                    for name in ("MODEL_API_KEY", "AI_TEST_KEY")
                    if source.get(name) and source[name].strip()
                ), "")
                continue
            name = item.name.upper()
            if name not in merged:
                continue
            raw = merged[name]
            if raw is None:
                raise ConfigurationError(f"{name}: wymagana wartość.")
            raw = raw.strip()
            try:
                if isinstance(item.default, bool):
                    if raw.lower() not in {"true", "false", "1", "0"}:
                        raise ValueError
                    values[item.name] = raw.lower() in {"true", "1"}
                else:
                    values[item.name] = type(item.default)(raw)
            except (ValueError, TypeError):
                raise ConfigurationError(f"{name}: niepoprawny typ wartości.") from None
        if "model_base_url" in values:
            values["model_base_url"] = values["model_base_url"].rstrip("/")
        return cls(**values)

    def flask_config(self):
        """Wyłącznie konfiguracja serwera; nie przekazywać jej do szablonów."""
        result = {item.name.upper(): getattr(self, item.name) for item in fields(self)}
        result["MAX_CONTENT_LENGTH"] = self.request_max_bytes
        return result
