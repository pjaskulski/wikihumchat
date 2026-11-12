"""Bezpieczne komunikaty błędów przeznaczone dla API."""

import httpx


class ChatError(Exception):
    def __init__(self, code, message, status=502, *, retry_after=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retry_after = retry_after


def upstream_error(error, service):
    """Nie przekazuj klientowi treści wyjątków usług zewnętrznych."""
    leaves = []

    def collect(item):
        if isinstance(item, BaseExceptionGroup):
            for child in item.exceptions:
                collect(child)
        else:
            leaves.append(item)

    collect(error)
    for item in leaves:
        if isinstance(item, ChatError):
            return item
    if any(isinstance(item, (TimeoutError, httpx.TimeoutException)) for item in leaves):
        return ChatError(
            f"{service}_timeout", "Przekroczono czas oczekiwania na usługę. Spróbuj ponownie.",
            504,
        )
    return ChatError(
        f"{service}_unavailable", "Usługa jest chwilowo niedostępna. Spróbuj ponownie.",
    )
