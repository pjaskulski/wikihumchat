import assert from "node:assert/strict";
import test from "node:test";
import { fixture, response, settle, type, deferred } from "./helpers.mjs";

function downloads(ui) {
  const saved = [];
  ui.window.URL.createObjectURL = () => "blob:test-pdf";
  ui.window.URL.revokeObjectURL = () => {};
  ui.window.HTMLAnchorElement.prototype.click = function () {
    saved.push({ href: this.href, filename: this.download });
  };
  return saved;
}

test("czat i eksport PDF używają adresów z prefiksem aplikacji", async t => {
  const urls = [];
  const ui = fixture(t, async (url, options) => {
    urls.push(url);
    if (url === "/wikihumchat/api/export/pdf") {
      return new Response("%PDF-test", { headers: { "Content-Type": "application/pdf" } });
    }
    return response(JSON.parse(options.body));
  }, { prepare(window) {
    const root = window.document.getElementById("chat-app");
    root.dataset.chatUrl = "/wikihumchat/api/chat";
    root.dataset.exportUrl = "/wikihumchat/api/export/pdf";
  } });
  downloads(ui);
  type(ui, "Pytanie");
  ui.form.requestSubmit();
  await settle();
  ui.document.getElementById("export-pdf").click();
  await settle();
  assert.deepEqual(urls, ["/wikihumchat/api/chat", "/wikihumchat/api/export/pdf"]);
});

test("PDF obejmuje wszystkie udane tury mimo skrócenia kontekstu modelu", async t => {
  let payload;
  const ui = fixture(t, async (url, options) => {
    const data = JSON.parse(options.body);
    if (url === "/api/export/pdf") {
      payload = data;
      assert.equal(options.headers.Accept, "application/pdf");
      return new Response("%PDF-test", { headers: {
        "Content-Type": "application/pdf",
        "Content-Disposition": 'attachment; filename="wikihum-chat-pierwsze-pytanie.pdf"',
      } });
    }
    return response(data, "Odpowiedź", { history: [
      { role: "user", content: data.message }, { role: "assistant", content: "Odpowiedź" },
    ] });
  });
  const saved = downloads(ui);
  const button = ui.document.getElementById("export-pdf");
  assert.equal(button.disabled, true);
  assert.equal(button.hidden, true);
  for (const question of ["Pierwsze pytanie", "Drugie pytanie"]) {
    type(ui, question);
    ui.form.requestSubmit();
    assert.equal(button.disabled, true);
    await settle();
  }
  assert.equal(button.disabled, false);
  assert.equal(button.hidden, false);
  button.click();
  await settle();
  assert.equal(payload.messages.length, 4);
  assert.equal(payload.messages[0].content, "Pierwsze pytanie");
  assert.match(payload.messages[1].content, /Rekordy wykorzystane w odpowiedzi/);
  assert.match(payload.messages[1].content, /Q158343/);
  assert.deepEqual(saved, [{ href: "blob:test-pdf", filename: "wikihum-chat-pierwsze-pytanie.pdf" }]);
  assert.equal(ui.document.getElementById("export-error").hidden, true);
  ui.reset.click();
  assert.equal(button.disabled, true);
  assert.equal(button.hidden, true);
});

test("błąd eksportu zachowuje rozmowę i umożliwia ponowne pobranie", async t => {
  const ui = fixture(t, async (url, options) => url === "/api/export/pdf"
    ? { ok: false, json: async () => ({ error: { message: "Dokument jest zbyt duży." } }) }
    : response(JSON.parse(options.body)));
  type(ui, "Pytanie"); ui.form.requestSubmit(); await settle();
  const button = ui.document.getElementById("export-pdf");
  button.click(); await settle();
  assert.match(ui.document.getElementById("export-error").textContent, /zbyt duży/);
  assert.equal(button.disabled, false);
  assert.equal(ui.log.querySelectorAll("article").length, 2);
});

test("nowa rozmowa anuluje eksport i odrzuca spóźniony PDF", async t => {
  const pending = deferred();
  let signal;
  const ui = fixture(t, async (url, options) => {
    if (url === "/api/export/pdf") { signal = options.signal; return pending.promise; }
    return response(JSON.parse(options.body));
  });
  const saved = downloads(ui);
  type(ui, "Pytanie"); ui.form.requestSubmit(); await settle();
  ui.document.getElementById("export-pdf").click(); await settle();
  ui.reset.click();
  assert.equal(signal.aborted, true);
  pending.resolve(new Response("%PDF-test", { headers: { "Content-Type": "application/pdf" } }));
  await settle();
  assert.equal(saved.length, 0);
  assert.equal(ui.document.getElementById("export-error").hidden, true);
});
