"""Eksport rozmowy do PDF w pamięci, bez wykonywania HTML i pobierania zasobów."""

from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlsplit
from xml.sax.saxutils import escape, quoteattr

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import CondPageBreak, LongTable, Paragraph, SimpleDocTemplate, Spacer, TableStyle

from errors import ChatError


FONT_DIR = Path(__file__).resolve().parent / "assets/fonts"
for name, filename in (
    ("WikiHum", "DejaVuSans.ttf"), ("WikiHum-Bold", "DejaVuSans-Bold.ttf"),
    ("WikiHum-Italic", "DejaVuSans-Oblique.ttf"),
    ("WikiHum-BoldItalic", "DejaVuSans-BoldOblique.ttf"),
):
    pdfmetrics.registerFont(TTFont(name, str(FONT_DIR / filename)))
pdfmetrics.registerFontFamily("WikiHum", normal="WikiHum", bold="WikiHum-Bold",
                              italic="WikiHum-Italic", boldItalic="WikiHum-BoldItalic")

INK = colors.HexColor("#233345")
ACCENT = colors.HexColor("#315b7b")
LINE = colors.HexColor("#dce2e8")
BODY = ParagraphStyle("body", fontName="WikiHum", fontSize=10, leading=15,
                      textColor=INK, spaceAfter=8, splitLongWords=True)
HEADING = ParagraphStyle("heading", parent=BODY, fontName="WikiHum-Bold", fontSize=12,
                         leading=17, spaceBefore=12, keepWithNext=True)
CELL = ParagraphStyle("cell", parent=BODY, fontSize=9, leading=13, spaceAfter=0)
TOKENS = re.compile(r"(\\[\\`*_\[\]|]|`[^`\n]+`|\[[^\[\]\n]+\]\([^\s)]+\)|\*\*[^*\n]+\*\*|\*[^*\n]+\*)")


def export_filename(messages):
    question = next((item["content"] for item in messages if item["role"] == "user"), "")
    normalized = unicodedata.normalize("NFKD", question.casefold().replace("ł", "l"))
    normalized = normalized.encode("ascii", "ignore").decode("ascii")
    slug = "-".join(re.findall(r"[a-z0-9]+", normalized)[:6])[:80].rstrip("-")
    return "wikihum-chat" + ("-" + slug if slug else "") + ".pdf"


def safe_link(value):
    try:
        url = urlsplit(value)
        return (url.scheme in {"http", "https"} and bool(url.hostname)
                and not url.username and not url.password
                and not any(char.isspace() or ord(char) < 32 for char in value))
    except ValueError:
        return False


def inline(text, depth=0):
    if depth > 4:
        return escape(text)
    parts, end = [], 0
    for match in TOKENS.finditer(text):
        parts.append(escape(text[end:match.start()]))
        token = match[0]
        if token.startswith("\\"):
            parts.append(escape(token[1:]))
        elif token.startswith("["):
            label, url = re.fullmatch(r"\[([^\[\]]+)\]\(([^\s)]+)\)", token).groups()
            parts.append(f'<link href={quoteattr(url)} color="#315b7b"><u>{escape(label)}</u></link>'
                         if safe_link(url) else escape(token))
        elif token.startswith("`"):
            parts.append(escape(token[1:-1]))
        else:
            strong = token.startswith("**")
            tag, size = ("b", 2) if strong else ("i", 1)
            parts.append(f"<{tag}>{inline(token[size:-size], depth + 1)}</{tag}>")
        end = match.end()
    parts.append(escape(text[end:]))
    return "".join(parts)


def table_row(line):
    text = line.strip()
    cells, cell, ticks, separators = [], "", 0, 0
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text) and text[index + 1] in "\\`*_[]|":
            cell += "|" if text[index + 1] == "|" else text[index:index + 2]
            index += 2
            continue
        if char == "`":
            end = index + 1
            while end < len(text) and text[end] == "`":
                end += 1
            size = end - index
            ticks = 0 if ticks == size else ticks or size
            cell += text[index:end]
            index = end
            continue
        if char == "|" and not ticks:
            cells.append(cell.strip())
            cell, separators = "", separators + 1
        else:
            cell += char
        index += 1
    if not separators:
        return None
    cells.append(cell.strip())
    if cells[0] == "" and text.startswith("|"):
        cells.pop(0)
    if cells and cells[-1] == "" and text.endswith("|"):
        cells.pop()
    return cells


def markdown_blocks(text, width):
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks, paragraph = [], []

    def flush():
        if paragraph:
            blocks.append(Paragraph("<br/>".join(inline(line) for line in paragraph), BODY))
            paragraph.clear()

    index, code = 0, False
    while index < len(lines):
        line = lines[index]
        if line.lstrip().startswith("```"):
            flush()
            code = not code
            index += 1
            continue
        if code:
            if line:
                blocks.append(Paragraph(escape(line), CELL))
            else:
                blocks.append(Spacer(1, 8))
            index += 1
            continue
        header = table_row(line)
        separator = table_row(lines[index + 1]) if index + 1 < len(lines) else None
        if (header and separator and len(header) == len(separator)
                and all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator)):
            flush()
            rows = [header]
            index += 2
            while index < len(lines):
                row = table_row(lines[index])
                if row is None or len(row) > len(header):
                    break
                rows.append(row + [""] * (len(header) - len(row)))
                index += 1
            # Bardzo szerokie tabele zachowaj jako czytelne grupy pól.
            if len(header) > 6 or any(len(cell) > 400 for cell in header):
                for number, row in enumerate(rows[1:], 1):
                    blocks.append(Paragraph(f"<b>Wiersz {number}</b>", BODY))
                    for title, value in zip(header, row):
                        blocks.append(Paragraph(f"<b>{inline(title)}:</b> {inline(value)}", BODY))
                continue
            weights = [0.28 if re.fullmatch(r"(#|lp\.?|nr\.?)", title, re.I) else
                       max(1, min(3, max(len(row[col]) for row in rows) / 25))
                       for col, title in enumerate(header)]
            if blocks and isinstance(blocks[-1], Paragraph) and blocks[-1].style is HEADING:
                # Nie trzymaj nagłówka razem z całą wielostronicową tabelą.
                # Zostaw miejsce na nagłówek i początek tabeli.
                blocks[-1].keepWithNext = False
                blocks.insert(len(blocks) - 1, CondPageBreak(90))
            table = LongTable(
                [[Paragraph((f"<b>{inline(cell)}</b>" if row_index == 0 else inline(cell)) or " ", CELL)
                  for cell in row] for row_index, row in enumerate(rows)],
                colWidths=[width * weight / sum(weights) for weight in weights],
                repeatRows=1, splitByRow=1, splitInRow=1, hAlign="LEFT",
            )
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef3f7")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafbfd")]),
                ("GRID", (0, 0), (-1, -1), 0.4, LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]))
            blocks.extend([table, Spacer(1, 10)])
            continue
        if not line.strip():
            flush()
        elif re.match(r"^#{1,6}\s+", line):
            flush()
            blocks.append(Paragraph(inline(re.sub(r"^#{1,6}\s+", "", line)), HEADING))
        elif re.match(r"^\s*(?:[-+*]|\d+[.)])\s+", line):
            flush()
            marker, value = re.match(r"^\s*([-+*]|\d+[.)])\s+(.+)$", line).groups()
            blocks.append(Paragraph(inline(value), BODY, bulletText="•" if marker in "-+*" else marker))
        else:
            paragraph.append(line)
        index += 1
    flush()
    return blocks


def validate_export(payload):
    if not isinstance(payload, dict) or set(payload) != {"messages"}:
        raise ChatError("invalid_export", "Prześlij rozmowę do wyeksportowania.", 400)
    messages = payload["messages"]
    if not isinstance(messages, list) or not messages or len(messages) > 200 or len(messages) % 2:
        raise ChatError("invalid_export", "Eksport wymaga zakończonej rozmowy (do 100 par wiadomości).", 400)
    for index, message in enumerate(messages):
        if (not isinstance(message, dict) or set(message) != {"role", "content"}
                or message.get("role") != ("user" if index % 2 == 0 else "assistant")
                or not isinstance(message.get("content"), str) or not message["content"].strip()):
            raise ChatError("invalid_export", "Rozmowa zawiera niepoprawne wiadomości.", 400)
        content = message["content"]
        try:
            content.encode("utf-8")
        except UnicodeError:
            raise ChatError("invalid_export", "Rozmowa zawiera niepoprawne znaki.", 400) from None
        if any(ord(char) < 32 and char not in "\n\r\t" for char in content):
            raise ChatError("invalid_export", "Rozmowa zawiera niepoprawne znaki.", 400)
    return messages


def conversation_pdf(messages):
    output = BytesIO()
    document = SimpleDocTemplate(output, pagesize=A4, leftMargin=44, rightMargin=44,
                                 topMargin=55, bottomMargin=48, title="Rozmowa z WikiHum",
                                 author="Pracownia Historii Cyfrowej")
    width = A4[0] - 88 - 12  # uwzględnij wewnętrzny margines ramki Platypus
    story = [Paragraph("Rozmowa z WikiHum", HEADING), Paragraph(
        "Eksport: " + datetime.now(timezone.utc).strftime("%d.%m.%Y, %H:%M UTC"), BODY)]
    for index, message in enumerate(messages):
        label = "Pytanie" if message["role"] == "user" else "Odpowiedź WikiHum"
        heading = Paragraph(f"{label} {index // 2 + 1}", HEADING)
        if message["role"] == "user":
            story.append(heading)
            story.append(Paragraph(escape(message["content"]).replace("\n", "<br/>"), BODY))
        else:
            blocks = markdown_blocks(message["content"], width)
            if blocks and isinstance(blocks[0], LongTable):
                heading.keepWithNext = False
                story.append(CondPageBreak(90))
            story.append(heading)
            story.extend(blocks)
    story.append(Spacer(1, 10))
    story.append(Paragraph(
        "Odpowiedzi generowane automatycznie wymagają weryfikacji w rekordach i źródłach.",
        BODY,
    ))

    def page(canvas, doc):
        canvas.saveState()
        canvas.setFont("WikiHum", 8)
        canvas.setFillColor(ACCENT)
        canvas.drawString(44, A4[1] - 30, "WikiHum Chat")
        canvas.setStrokeColor(LINE)
        canvas.line(44, 36, A4[0] - 44, 36)
        canvas.drawString(44, 24, "Pracownia Historii Cyfrowej")
        canvas.drawRightString(A4[0] - 44, 24, f"Strona {doc.page}")
        canvas.restoreState()

    document.build(story, onFirstPage=page, onLaterPages=page)
    return output.getvalue()
