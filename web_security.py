"""Weryfikacja hosta i źródła żądań do publicznego API."""

from urllib.parse import urlsplit

from errors import ChatError


def origin(value, *, allow_path=False):
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password
            or parsed.port == 0
            or any(char.isspace() for char in value)
            or (not allow_path and (
                parsed.path not in {"", "/"} or parsed.query or parsed.fragment
            ))
        ):
            return None
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        hostname = parsed.hostname.encode("idna").decode("ascii").lower()
        return parsed.scheme, hostname, port
    except (ValueError, UnicodeError):
        return None


def check_request(request, settings):
    actual = origin(request.host_url)
    public = origin(settings.app_public_url) if settings.app_public_url else None
    hosts = {public[1]} if public else {"localhost", "127.0.0.1", "::1"}
    if actual is None or actual[1] not in hosts:
        raise ChatError("untrusted_host", "Nieprawidłowy adres usługi.", 400)
    if request.endpoint not in {"chat", "export_pdf"} or request.method != "POST":
        return
    target = public or actual
    source = request.headers.get("Origin")
    referer = request.headers.get("Referer")
    fetch_site = request.headers.get("Sec-Fetch-Site")
    if (
        (source is not None and origin(source) != target)
        or (source is None and referer is not None and origin(referer, allow_path=True) != target)
        or (fetch_site is not None and fetch_site not in {"same-origin", "none"})
    ):
        raise ChatError("origin_not_allowed", "Wyślij pytanie ze strony WikiHum Chat.", 403)


SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; "
        "connect-src 'self'; img-src 'self'; font-src 'self'; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'; object-src 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Resource-Policy": "same-origin",
}
