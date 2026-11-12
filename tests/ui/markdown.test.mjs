import assert from "node:assert/strict";
import test from "node:test";
import { JSDOM } from "jsdom";
import { renderMarkdown, safeUrl } from "../../static/markdown.js";

test("dane tabeli z poprzedzonymi ukośnikiem znakami Markdown pozostają tekstem", () => {
  const dom = new JSDOM();
  try {
    const { document } = dom.window;
    document.body.append(renderMarkdown(document,
      "| Nazwa | QID |\n|---|---|\n| A\\`B \\*\\*tekst\\*\\* \\[link\\](https://evil.example) \\| C | [Q1](https://example.org/wiki/Item:Q1) |"));
    assert.equal(document.querySelectorAll("tbody td").length, 2);
    assert.equal(document.querySelector("tbody td").textContent,
      "A`B **tekst** [link](https://evil.example) | C");
    assert.equal(document.querySelectorAll("tbody a").length, 1);
    assert.equal(document.querySelector("tbody strong"), null);
  } finally { dom.window.close(); }
});

test("akapity, listy, nagłówki, cytaty i kod są czytelnymi węzłami DOM", () => {
  const dom = new JSDOM();
  try {
    const { document } = dom.window;
    document.body.append(renderMarkdown(document,
      "## Nagłówek\n\nAkapit **pogrubiony** i *kursywa* oraz `kod`.\n\n- jeden\n- dwa\n\n1. pierwszy\n2. drugi\n\n> cytat\n\n```\n<script>literalny kod</script>\n```"));
    assert.equal(document.querySelectorAll("li").length, 4);
    assert.equal(document.querySelector("h3").textContent, "Nagłówek");
    assert.equal(document.querySelector("strong").textContent, "pogrubiony");
    assert.equal(document.querySelector("em").textContent, "kursywa");
    assert.equal(document.querySelector("blockquote").textContent, "cytat");
    assert.match(document.querySelector("pre code").textContent, /<script>/);
    assert.equal(document.querySelector("script"), null);
  } finally { dom.window.close(); }
});

test("łącza otwierają nową kartę z ochroną opener i referrer", () => {
  const dom = new JSDOM();
  try {
    dom.window.document.body.append(renderMarkdown(dom.window.document,
      "[Jan Kostka (Q158343)](https://wikihum.lab.dariah.pl/wiki/Item:Q158343)"));
    const link = dom.window.document.querySelector("a");
    assert.equal(link.textContent, "Jan Kostka (Q158343)");
    assert.equal(link.target, "_blank");
    assert.equal(link.rel, "noopener noreferrer");
    assert.equal(link.referrerPolicy, "no-referrer");
  } finally { dom.window.close(); }
});

test("adresy aktywne, względne i zawierające dane logowania nie stają się linkami", () => {
  for (const value of ["javascript:alert(1)", "JaVaScRiPt:alert(1)", "java\nscript:alert(1)",
    "data:text/html,bad", "file:///etc/passwd", "//example.org/", "/relative",
    "https://user:password@example.org/"]) {
    assert.equal(safeUrl(value), null, value);
  }
  assert.equal(safeUrl("https://example.org/"), "https://example.org/");
});

test("tabela ma nagłówki, bezpieczne odnośniki, wyrównanie i przewijanie klawiaturą", () => {
  const dom = new JSDOM();
  try {
    const { document } = dom.window;
    document.body.append(renderMarkdown(document,
      "Wynik:\n\n| # | Miejscowość | Typ |\n| ---: | :--- | :---: |\n" +
      "| 1 | [Biadaczów](https://example.org/Q1) | **wieś** |\n" +
      "| 2 | Będzin | miasto |\n\nUwagi."));
    assert.equal(document.querySelectorAll("thead th[scope=col]").length, 3);
    assert.equal(document.querySelectorAll("tbody tr").length, 2);
    assert.equal(document.querySelector("tbody a").target, "_blank");
    assert.equal(document.querySelector("tbody strong").textContent, "wieś");
    assert.ok(document.querySelector("tbody td").classList.contains("align-right"));
    assert.ok(document.querySelector("tbody td").classList.contains("table-number"));
    assert.equal(document.querySelector(".markdown-table-scroll").tabIndex, 0);
    assert.equal(document.querySelectorAll("p").length, 2);
  } finally { dom.window.close(); }
});

test("tabele bez skrajnych kresek, z pustymi komórkami i znakami | w tekście oraz kodzie", () => {
  const dom = new JSDOM();
  try {
    const { document } = dom.window;
    document.body.append(renderMarkdown(document,
      "Nazwa | Opis\n--- | ---\nA\\|B | `x|y`\nC |\n"));
    assert.equal(document.querySelectorAll("tbody tr").length, 2);
    assert.equal(document.querySelector("tbody td").textContent, "A|B");
    assert.equal(document.querySelector("tbody code").textContent, "x|y");
    assert.equal(document.querySelectorAll("tbody tr")[1].lastChild.textContent, "");
  } finally { dom.window.close(); }
});

test("tabela nie interpretuje HTML ani aktywnych adresów", () => {
  const dom = new JSDOM();
  try {
    const { document } = dom.window;
    document.body.append(renderMarkdown(document,
      '| Nazwa | Opis |\n| --- | --- |\n| <img src=x onerror=alert(1)> | [X](javascript:alert) |'));
    assert.equal(document.querySelectorAll("tbody td").length, 2);
    assert.equal(document.querySelector("img, script, a, [onerror]"), null);
    assert.match(document.querySelector("tbody td").textContent, /<img/);
  } finally { dom.window.close(); }
});

test("kod i niepoprawne tabele pozostają tekstem; nadmiarowe komórki nie giną", () => {
  const dom = new JSDOM();
  try {
    const { document } = dom.window;
    document.body.append(renderMarkdown(document,
      "```\n| A | B |\n| --- | --- |\n```\n\nA | B\n---\n\n" +
      "| C | D |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 | dodatkowa |"));
    assert.equal(document.querySelectorAll("table").length, 1);
    assert.equal(document.querySelectorAll("tbody tr").length, 1);
    assert.match(document.body.textContent, /dodatkowa/);
    assert.match(document.querySelector("pre").textContent, /---/);
  } finally { dom.window.close(); }
});
