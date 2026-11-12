"""Wejście Gunicorna za jednym zaufanym Nginx: gunicorn wsgi:app."""

from werkzeug.middleware.proxy_fix import ProxyFix

from app import create_app


app = create_app()
app.wsgi_app = ProxyFix(
    app.wsgi_app,
    # TrafficControl sam interpretuje X-Forwarded-For z zaufanych adresów proxy.
    x_for=0,
    x_proto=1,
    x_host=0,
    x_port=0,
    x_prefix=1,
)
