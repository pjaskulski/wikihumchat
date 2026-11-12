import shutil
import subprocess

import pytest

from app import create_app
from config import Settings
from errors import ChatError
from pdf_export import conversation_pdf, export_filename, inline, validate_export


def messages(answer="Zażółć gęślą jaźń."):
    return [{"role": "user", "content": "Kim był Stanisław Bodniak?"},
            {"role": "assistant", "content": answer}]


def extract(data):
    if not shutil.which("pdftotext"):
        pytest.skip("Odczyt PDF wymaga Poppler")
    return subprocess.run(["pdftotext", "-", "-"], input=data, capture_output=True,
                          check=True).stdout.decode("utf-8")


def test_export_preserves_polish_and_all_turns_and_returns_download():
    client = create_app(Settings(model_api_key="test")).test_client()
    turns = messages() + [{"role": "user", "content": "A jego publikacje?"},
                          {"role": "assistant", "content": "Łętkowice, Żółkiew, źródła."}]
    response = client.post("/api/export/pdf", json={"messages": turns})
    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert "attachment" in response.headers["Content-Disposition"]
    assert 'filename="wikihum-chat-kim-byl-stanislaw-bodniak.pdf"' in response.headers["Content-Disposition"]
    assert response.headers["Cache-Control"] == "no-store"
    text = extract(response.data)
    for phrase in ("Stanisław Bodniak", "Zażółć gęślą jaźń", "A jego publikacje", "Łętkowice, Żółkiew"):
        assert phrase in text
    assert text.index("Odpowiedzi generowane automatycznie") > text.index("Łętkowice, Żółkiew")
    assert text.count("Odpowiedzi generowane automatycznie") == 1


@pytest.mark.parametrize("question,expected", [
    ("Jakich znasz artystów związanych z Krakowem i żyjących w XVI-XVIII wieku?",
     "wikihum-chat-jakich-znasz-artystow-zwiazanych-z-krakowem.pdf"),
    ('../../Żółć?\r\n"/test', "wikihum-chat-zolc-test.pdf"),
    ("???", "wikihum-chat.pdf"),
])
def test_export_filename_is_short_and_safe(question, expected):
    assert export_filename([{"role": "user", "content": question}]) == expected


def test_table_repeats_headers_and_does_not_drop_last_record():
    table = "| Nr | Miejscowość | Typ |\n| --- | --- | --- |\n" + "\n".join(
        f"| {i} | [Miejscowość {i}](https://example.org/Q{i}) | wieś |" for i in range(1, 101))
    data = conversation_pdf(messages(table))
    text = extract(data)
    assert "Miejscowość 1" in text.split("\f")[0]
    assert text.count("Miejscowość\n") > 1
    for i in range(1, 101):
        assert f"Miejscowość {i}" in text
    assert b"https://example.org/Q100" in data


def test_markdown_escaped_source_text_is_literal_in_pdf():
    value = inline(r"A\`B \*\*tekst\*\* \[link\](https://example.org)")
    assert "A`B **tekst** [link](https://example.org)" == value
    assert "<link" not in value and "<b>" not in value


def test_tall_cells_and_wide_tables_are_not_clipped():
    tall = "| Miejscowość | Opis |\n| --- | --- |\n| Koniec | " + ("Długi opis. " * 1200) + "OSTATNIE_SŁOWO |"
    wide = "\n\n" + " | ".join(f"Kolumna {i}" for i in range(7)) + "\n" + " | ".join(["---"] * 7) + "\n" + " | ".join(["Treść"] * 7)
    text = extract(conversation_pdf(messages(tall + wide)))
    assert "OSTATNIE_SŁOWO" in text
    assert "Kolumna 6" in text


@pytest.mark.parametrize("payload", [None, {}, {"messages": []}, {"messages": messages()[:1]},
    {"messages": [{"role": "system", "content": "x"}, messages()[1]]},
    {"messages": messages() * 101}, {"messages": messages("\x00")},
    {"messages": messages("\ud800")}, {"messages": messages(), "path": "/tmp/test.pdf"},
])
def test_invalid_export_is_rejected(payload):
    with pytest.raises(ChatError):
        validate_export(payload)


def test_html_and_unsafe_links_cannot_be_executed_or_fetched():
    text = inline('<img src="http://private/"> [X](javascript:bad) [OK](https://example.org)')
    assert '<img' not in text
    assert 'href="javascript' not in text
    assert 'href="https://example.org"' in text
    assert "<img" in extract(conversation_pdf(messages('<img src="http://private/">')))


def test_export_obeys_origin_body_size_and_concurrency_limits():
    app = create_app(Settings(model_api_key="test", request_max_bytes=1024))
    client = app.test_client()
    assert client.post("/api/export/pdf", json={"messages": messages()},
                       headers={"Origin": "https://foreign.example"}).status_code == 403
    assert client.post("/api/export/pdf", json={"messages": messages("x" * 2000)}).status_code == 413
    settings = Settings(model_api_key="test", chat_max_concurrent_requests=1)
    app = create_app(settings)
    with app.extensions["traffic_control"].admit("127.0.0.1", None):
        assert app.test_client().post("/api/export/pdf", json={"messages": messages()}).status_code == 503
