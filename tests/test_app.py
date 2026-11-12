import pytest

from app import create_app
from config import Settings


@pytest.fixture
def app():
    result = create_app(Settings(model_api_key="never-show-this-secret"))
    result.config["TESTING"] = True
    return result


def test_public_endpoints_do_not_expose_server_configuration(app):
    with app.test_client() as client:
        for path in ("/", "/healthz"):
            response = client.get(path)
            assert response.status_code == 200
            assert response.headers["Cache-Control"] == "no-store"
            assert "never-show-this-secret" not in response.get_data(as_text=True)
            assert "ai-test.ihpan.edu.pl" not in response.get_data(as_text=True)
        assert client.get("/healthz").json == {"status": "ok"}


def test_apps_keep_separate_configuration(app):
    other = create_app(Settings(model_api_key="another-secret", request_max_bytes=1024))
    assert app.config["MODEL_API_KEY"] == "never-show-this-secret"
    assert other.config["MODEL_API_KEY"] == "another-secret"
    assert other.config["MAX_CONTENT_LENGTH"] == 1024
    assert app.config["MAX_CONTENT_LENGTH"] != other.config["MAX_CONTENT_LENGTH"]


def test_flask_body_limit_is_bound_to_configuration(app):
    # Odczyt ciała żądania podlega limitowi także w przyszłych trasach czatu.
    from flask import request

    @app.post("/_test/read-body")
    def read_body():
        return request.get_data()

    with app.test_client() as client:
        response = client.post(
            "/_test/read-body", data=b"x" * (app.config["MAX_CONTENT_LENGTH"] + 1),
        )
    assert response.status_code == 413


def test_chat_page_loads_local_assets_and_only_public_limits():
    from html.parser import HTMLParser

    class Assets(HTMLParser):
        def __init__(self):
            super().__init__()
            self.urls = []

        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            if tag == "script" and "src" in attributes:
                self.urls.append(attributes["src"])
            if tag == "link" and attributes.get("rel") == "stylesheet":
                self.urls.append(attributes["href"])

    app = create_app(Settings(model_api_key="private-secret", chat_max_question_chars=120))
    with app.test_client() as client:
        response = client.get("/")
        html = response.get_data(as_text=True)
        assert 'data-question-limit="120"' in html
        assert "private-secret" not in html
        parser = Assets()
        parser.feed(html)
        assert len(parser.urls) == 2
        for url in parser.urls:
            assert url.startswith("/static/")
            assert client.get(url).status_code == 200
