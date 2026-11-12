"""WikiHum Chat. Uruchomienie: flask --app app run."""

import asyncio
from contextlib import ExitStack
import logging
from time import monotonic

from flask import Flask, Response, g, jsonify, render_template, request
from werkzeug.exceptions import (
    BadRequest, HTTPException, RequestEntityTooLarge, UnsupportedMediaType,
)

from chat_service import ChatService, validate_request
from config import Settings
from errors import ChatError
from event_stream import ChatEventStream
from pdf_export import conversation_pdf, export_filename, validate_export
from traffic_control import TrafficControl
from web_security import SECURITY_HEADERS, check_request, origin


def create_app(settings=None, *, chat_service=None, traffic_control=None):
    """Twórz niezależną aplikację; start nie wywołuje modelu ani MCP."""
    settings = settings if settings is not None else Settings.from_env()
    app = Flask(__name__)
    app.config.from_mapping(settings.flask_config())
    app.logger.setLevel(logging.INFO)
    app.extensions["chat_service"] = (
        chat_service if chat_service is not None else ChatService(settings)
    )
    app.extensions["traffic_control"] = (
        traffic_control if traffic_control is not None else TrafficControl(settings)
    )

    @app.before_request
    def verify_source():
        g.request_started = monotonic()
        g.chat_error_code = "ok"
        check_request(request, settings)

    @app.get("/")
    def index():
        return render_template(
            "index.html", max_question_chars=settings.chat_max_question_chars,
            request_timeout_ms=min(int((settings.chat_timeout_seconds + 15) * 1000), 2_147_483_647),
        )

    @app.get("/healthz")
    def health():
        # Żywotność aplikacji, nie test gotowości usług zewnętrznych.
        return jsonify(status="ok")

    @app.post("/api/chat")
    def chat():
        admission = ExitStack()
        handed_over = False
        try:
            admission.enter_context(app.extensions["traffic_control"].admit(
                request.remote_addr, request.headers.get("X-Forwarded-For"),
            ))
            payload = request.get_json()
            accepts = request.accept_mimetypes
            if accepts["text/event-stream"] > accepts["application/json"]:
                validate_request(payload, settings)
                started = monotonic()

                def finished(code, result_status):
                    app.logger.info(
                        "chat_request status=200 result_status=%d code=%s duration_ms=%d",
                        result_status, code, round((monotonic() - started) * 1000),
                    )

                stream = ChatEventStream(app.extensions["chat_service"], payload,
                                         admission.close, finished)
                response = Response(stream, mimetype="text/event-stream")
                response.headers["X-Accel-Buffering"] = "no"
                response.call_on_close(stream.close)
                g.chat_stream = True
                handed_over = True
                return response
            result = asyncio.run(app.extensions["chat_service"].reply(payload))
            return jsonify(result)
        finally:
            if not handed_over:
                admission.close()

    @app.post("/api/export/pdf")
    def export_pdf():
        with app.extensions["traffic_control"].admit(
            request.remote_addr, request.headers.get("X-Forwarded-For"),
        ):
            messages = validate_export(request.get_json())
            document = conversation_pdf(messages)
        return Response(document, mimetype="application/pdf", headers={
            "Content-Disposition": f'attachment; filename="{export_filename(messages)}"',
        })

    @app.errorhandler(ChatError)
    def handle_chat_error(error):
        # Żadnych treści rozmowy, wyników narzędzi ani surowych wyjątków.
        g.chat_error_code = error.code
        response = jsonify(error={"code": error.code, "message": error.message})
        if error.retry_after is not None:
            response.headers["Retry-After"] = str(error.retry_after)
        return response, error.status

    @app.errorhandler(RequestEntityTooLarge)
    def handle_large_request(_):
        g.chat_error_code = "request_too_large"
        return jsonify(error={
            "code": "request_too_large", "message": "Żądanie jest zbyt duże.",
        }), 413

    @app.errorhandler(BadRequest)
    @app.errorhandler(UnsupportedMediaType)
    def handle_invalid_json(_):
        g.chat_error_code = "invalid_json"
        return jsonify(error={
            "code": "invalid_json", "message": "Prześlij poprawne dane JSON.",
        }), 400

    @app.errorhandler(Exception)
    def handle_unexpected_error(error):
        if isinstance(error, HTTPException):
            g.chat_error_code = "http_error"
            return error
        # Nie loguj tracebacka ani treści nieprzewidzianego wyjątku.
        g.chat_error_code = "internal_error"
        return jsonify(error={
            "code": "internal_error", "message": "Wystąpił błąd aplikacji. Spróbuj ponownie.",
        }), 500

    @app.after_request
    def prevent_caching(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers.update(SECURITY_HEADERS)
        if settings.app_public_url and origin(settings.app_public_url)[0] == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        if request.endpoint == "chat" and not g.get("chat_stream", False):
            elapsed = round((monotonic() - g.get("request_started", monotonic())) * 1000)
            app.logger.info(
                "chat_request status=%d code=%s duration_ms=%d",
                response.status_code, g.get("chat_error_code", "http_error"), elapsed,
            )
        return response

    return app
