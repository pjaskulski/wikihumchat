import assert from "node:assert/strict";
import test from "node:test";
import { fixture, response, settle, type } from "./helpers.mjs";

const key = "wikihum-chat-language";
const choose = (ui, value) => ui.document.querySelector(`[data-language="${value}"]`).click();

function setup(t, fetchImpl, { saved, blocked = false } = {}) {
  return fixture(t, fetchImpl, { prepare(window) {
    if (saved) window.localStorage.setItem(key, saved);
    if (blocked) Object.defineProperty(window, "localStorage", { get() { throw new Error("blocked"); } });
    window.HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
    window.HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
  } });
}

test("PL jest domyślny, a zapisany EN obejmuje stronę, dostępność i przykłady", t => {
  const pl = setup(t, () => {});
  assert.equal(pl.document.documentElement.lang, "pl");
  assert.equal(pl.document.getElementById("language-switch").hidden, false);
  const ui = setup(t, () => {}, { saved: "en" });
  assert.equal(ui.document.documentElement.lang, "en");
  assert.equal(ui.document.getElementById("page-title").textContent, "Ask WikiHum");
  assert.equal(ui.document.getElementById("conversation-title").textContent, "Current conversation");
  assert.equal(ui.input.placeholder, "Ask a question about WikiHum data…");
  assert.equal(ui.log.getAttribute("aria-label"), "Conversation messages");
  assert.match(ui.document.querySelector(".information").textContent, /They may contain errors/);
  assert.equal(ui.document.querySelector('[data-language="en"]').getAttribute("aria-pressed"), "true");
  for (const example of ui.document.querySelectorAll("[data-question]")) {
    assert.equal(example.querySelector("[data-example-text]").textContent, example.dataset.questionEn);
    assert.equal(example.dataset.question, example.dataset.questionEn);
  }
});

test("wybór angielskiego przykładu zamyka okno i wstawia dokładnie jego tekst bez wysyłania", t => {
  let calls = 0;
  const ui = setup(t, () => { calls++; });
  choose(ui, "en");
  ui.document.getElementById("more-examples").click();
  const choice = ui.document.querySelectorAll("#examples-dialog [data-question]")[5];
  choice.click();
  assert.equal(ui.input.value, choice.querySelector("[data-example-text]").textContent);
  assert.equal(ui.input.value, "List participants in the November Uprising.");
  assert.equal(ui.document.getElementById("examples-dialog").open, false);
  assert.equal(calls, 0);
  choose(ui, "pl");
  assert.equal(ui.input.value, "List participants in the November Uprising.");
  assert.equal(ui.window.localStorage.getItem(key), "pl");
});

test("przełączenie zachowuje szkic, odpowiedź i historię wysyłaną przy kolejnym pytaniu", async t => {
  const requests = [];
  const ui = setup(t, async (_, options) => {
    const payload = JSON.parse(options.body);
    requests.push(payload);
    return response(payload, "Odpowiedź z bazy bez tłumaczenia.", { duration_ms: 2500 });
  });
  type(ui, "Kim był Stanisław Bodniak?");
  ui.form.requestSubmit();
  await settle();
  const answer = ui.log.querySelector(".message-assistant");
  const body = answer.querySelector(".message-body");
  type(ui, "Szkic drugiego pytania");
  choose(ui, "en");
  assert.equal(ui.log.querySelector(".message-assistant"), answer);
  assert.equal(body.textContent, "Odpowiedź z bazy bez tłumaczenia.");
  assert.equal(ui.input.value, "Szkic drugiego pytania");
  assert.equal(answer.querySelector("summary").textContent, "Records used in the answer");
  assert.equal(answer.querySelector(".message-duration").textContent, "Response time: 2.5 s");
  assert.equal(ui.log.querySelector(".message-user .message-author").textContent, "You");
  ui.form.requestSubmit();
  await settle();
  assert.equal(requests.length, 2);
  assert.equal(requests[1].message, "Szkic drugiego pytania");
  assert.deepEqual(requests[1].history, [
    { role: "user", content: "Kim był Stanisław Bodniak?" },
    { role: "assistant", content: "Odpowiedź z bazy bez tłumaczenia." },
  ]);
});

test("przełączenie podczas pracy tłumaczy postęp i późniejsze zdarzenia, zachowując jego elementy", async t => {
  let stream;
  const pending = new ReadableStream({ start(controller) { stream = controller; } });
  const ui = setup(t, async () => ({ ok: true, headers: new Headers({ "content-type": "text/event-stream" }), body: pending }));
  const emit = (kind, payload) => stream.enqueue(new TextEncoder().encode(`event: ${kind}\ndata: ${JSON.stringify(payload)}\n\n`));
  type(ui, "Pytanie");
  ui.form.requestSubmit();
  emit("progress", { id: 1, state: "running", message: "Wyszukiwanie: Kraków <script>" });
  await settle();
  const trace = ui.log.querySelector(".query-progress");
  const row = trace.querySelector("li");
  choose(ui, "en");
  assert.equal(trace.querySelector("li"), row);
  assert.equal(row.querySelector(".query-step-label").textContent, "Searching: Kraków <script>");
  assert.equal(trace.querySelector("script"), null);
  assert.equal(row.querySelector(".query-step-state").textContent, "In progress…");
  assert.equal(ui.input.readOnly, true);
  emit("progress", { id: 1, state: "done", message: "Wyszukiwanie: Kraków <script>", seconds: 0.83 });
  emit("error", { error: { message: "Serwer modelu zwrócił pustą odpowiedź. Spróbuj ponownie. Identyfikator diagnostyczny: abc123." } });
  stream.close();
  await settle();
  assert.match(ui.error.textContent, /The model server returned an empty response/);
  assert.match(ui.error.textContent, /Diagnostic ID: abc123/);
  assert.match(ui.error.textContent, /Completed · 0.83 s/);
  assert.match(ui.error.textContent, /Failed search progress/);
  choose(ui, "pl");
  assert.equal(ui.error.querySelector("li"), row);
  assert.match(ui.error.textContent, /Serwer modelu zwrócił pustą odpowiedź/);
  assert.equal(ui.input.value, "Pytanie");
});

test("nieprawidłowa preferencja i zablokowana pamięć nie blokują interfejsu", t => {
  const invalid = setup(t, () => {}, { saved: "<script>" });
  assert.equal(invalid.document.documentElement.lang, "pl");
  const blocked = setup(t, () => {}, { blocked: true });
  choose(blocked, "en");
  assert.equal(blocked.document.documentElement.lang, "en");
  assert.equal(blocked.send.textContent.trim(), "Send");
});

test("zmiana w innej karcie aktualizuje język bez utraty szkicu i motywu", t => {
  const ui = setup(t, () => {});
  type(ui, "Roboczy tekst");
  ui.document.documentElement.dataset.theme = "dark";
  ui.window.localStorage.setItem(key, "en");
  ui.window.dispatchEvent(new ui.window.StorageEvent("storage", { key, newValue: "en" }));
  assert.equal(ui.document.documentElement.lang, "en");
  assert.equal(ui.input.value, "Roboczy tekst");
  assert.equal(ui.document.documentElement.dataset.theme, "dark");
});

test("język odpowiedzi jest wysyłany przy pytaniu; późniejsza zmiana dotyczy kolejnej tury", async t => {
  let finish;
  const pending = new Promise(resolve => { finish = resolve; });
  const requests = [];
  const ui = setup(t, async (_, options) => {
    const payload = JSON.parse(options.body);
    requests.push(payload);
    return requests.length === 1 ? pending : response(payload, "Odpowiedź polska.");
  }, { saved: "en" });
  type(ui, "Who was Stanisław Bodniak?");
  ui.form.requestSubmit();
  choose(ui, "pl");
  assert.equal(requests[0].language, "en");
  finish(response(requests[0], "An English answer."));
  await settle();
  assert.equal(ui.log.querySelector(".message-assistant .message-body").textContent, "An English answer.");
  type(ui, "Podaj więcej informacji.");
  ui.form.requestSubmit();
  await settle();
  assert.equal(requests[1].language, "pl");
  assert.equal(requests[1].history[1].content, "An English answer.");
});
