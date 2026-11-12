import assert from "node:assert/strict";
import test from "node:test";
import { fixture, response, settle, type } from "./helpers.mjs";

function stream() {
  let controller;
  let cancelled = false;
  const body = new ReadableStream({
    start(value) { controller = value; }, cancel() { cancelled = true; },
  });
  return {
    response: { ok: true, status: 200, headers: new Headers({ "content-type": "text/event-stream" }), body },
    emit(kind, data) { controller.enqueue(new TextEncoder().encode(`event: ${kind}\ndata: ${JSON.stringify(data)}\n\n`)); },
    close() { controller.close(); },
    get cancelled() { return cancelled; },
  };
}

test("postęp jest widoczny przed odpowiedzią, a zakończony przebieg pozostaje rozwijany", async t => {
  const live = stream();
  const requests = [];
  const ui = fixture(t, async (_, options) => {
    assert.equal(options.headers.Accept, "text/event-stream");
    requests.push(JSON.parse(options.body));
    return requests.length === 1 ? live.response : response(requests[1]);
  });
  type(ui, "Pytanie");
  ui.form.requestSubmit();
  live.emit("progress", { id: 1, state: "running", message: "Wykonywanie kwerendy SPARQL", tool: "execute_sparql" });
  await settle();
  const details = ui.log.querySelector(".query-progress");
  assert.ok(details);
  assert.equal(details.open, true);
  assert.equal(details.querySelector("code"), null);
  assert.doesNotMatch(details.textContent, /execute_sparql/);
  assert.equal(ui.document.getElementById("chat-status").hidden, true);
  assert.match(details.querySelector(".query-step-label").textContent, /SPARQL/);
  assert.equal(details.querySelectorAll(".query-step-spinner:not([hidden])").length, 1);
  assert.equal(ui.log.querySelector(".message-assistant"), null);
  live.emit("progress", { id: 1, state: "done", message: "Wykonywanie kwerendy SPARQL", tool: "execute_sparql", seconds: 1.234 });
  live.emit("result", await response(requests[0]).json());
  live.close();
  await settle();
  assert.equal(details.open, false);
  assert.equal(details.querySelectorAll(".query-step-spinner:not([hidden])").length, 0);
  assert.equal(details.closest("article").className, "message message-assistant");
  assert.equal(details.querySelectorAll("li").length, 1);
  assert.match(details.textContent, /1,23 s/);
  type(ui, "Kolejne pytanie");
  ui.form.requestSubmit();
  await settle();
  assert.equal(requests[1].history.length, 2);
  assert.doesNotMatch(JSON.stringify(requests[1].history), /execute_sparql|Przebieg kwerendy/);
});

test("błąd po rozpoczęciu transmisji zachowuje pytanie i przebieg, bez fałszywego sukcesu", async t => {
  const live = stream();
  const ui = fixture(t, async () => live.response);
  type(ui, "Pytanie");
  ui.form.requestSubmit();
  live.emit("progress", { id: 1, state: "running", message: "Pobieranie danych" });
  live.emit("error", { error: { code: "context_too_large", message: "Wyniki przekroczyły limit kontekstu." }, status: 413 });
  live.close();
  await settle();
  assert.equal(ui.input.value, "Pytanie");
  assert.equal(ui.send.disabled, false);
  assert.equal(ui.log.children.length, 0);
  assert.match(ui.error.textContent, /limit kontekstu/);
  assert.match(ui.error.textContent, /Przebieg nieudanej kwerendy/);
  assert.match(ui.error.textContent, /Nie otrzymano zakończenia etapu/);
});

test("nowa rozmowa anuluje czytnik SSE i usuwa poprzedni przebieg", async t => {
  const live = stream();
  let calls = 0;
  const ui = fixture(t, async (_, options) => {
    calls += 1;
    return calls === 1 ? live.response : response(JSON.parse(options.body));
  });
  type(ui, "Stare pytanie");
  ui.form.requestSubmit();
  live.emit("progress", { id: 1, state: "running", message: "Stary etap" });
  await settle();
  ui.reset.click();
  await settle();
  assert.equal(live.cancelled, true);
  assert.equal(ui.log.children.length, 0);
  type(ui, "Nowe pytanie");
  ui.form.requestSubmit();
  await settle();
  assert.match(ui.log.textContent, /Nowe pytanie/);
  assert.doesNotMatch(ui.log.textContent, /Stary etap/);
  assert.equal(ui.error.hidden, true);
});

test("etykiety postępu i nazwy narzędzi nie interpretują HTML", async t => {
  const live = stream();
  const ui = fixture(t, async () => live.response);
  type(ui, "Pytanie");
  ui.form.requestSubmit();
  live.emit("progress", { id: 1, state: "running", message: "<img src=x onerror=alert(1)>", tool: "<script>bad</script>" });
  await settle();
  assert.equal(ui.log.querySelector("img, script"), null);
  assert.match(ui.log.textContent, /<img/);
  ui.reset.click();
  await settle();
});

for (const failed of [false, true]) {
  test(`widok pokazuje ostatnie trzy etapy i zachowuje pełny przebieg po ${failed ? "błędzie" : "sukcesie"}`, async t => {
    const live = stream();
    const ui = fixture(t, async () => live.response);
    type(ui, "Pytanie");
    ui.form.requestSubmit();
    for (let id = 1; id <= 5; id++) {
      live.emit("progress", { id, state: id === 5 ? "running" : "done", message: `Etap ${id}` });
    }
    await settle();
    const details = ui.log.querySelector(".query-progress");
    const visibleIds = () => [...details.querySelectorAll("li:not([hidden])")].map(row => row.value);
    const toggle = details.querySelector("button");
    assert.deepEqual(visibleIds(), [3, 4, 5]);
    assert.equal(details.querySelectorAll(".query-step-spinner:not([hidden])").length, 1);
    assert.equal(details.querySelector("li[data-state='running'] .query-step-spinner").hidden, false);
    assert.equal(toggle.hidden, false);
    assert.equal(toggle.getAttribute("aria-expanded"), "false");
    assert.equal(toggle.getAttribute("aria-controls"), details.querySelector("ol").id);
    toggle.click();
    assert.deepEqual(visibleIds(), [1, 2, 3, 4, 5]);
    live.emit("progress", { id: 5, state: "done", message: "Etap 5", seconds: 1 });
    live.emit("progress", { id: 6, state: "running", message: "Etap 6" });
    await settle();
    assert.deepEqual(visibleIds(), [1, 2, 3, 4, 5, 6]);
    toggle.click();
    assert.deepEqual(visibleIds(), [4, 5, 6]);
    assert.equal(details.querySelectorAll(".query-step-spinner:not([hidden])").length, 1);
    // Spóźniona aktualizacja nie tworzy nowego wiersza ani nie wypiera bieżącego.
    live.emit("progress", { id: 1, state: "done", message: "Etap 1", seconds: 2 });
    await settle();
    assert.deepEqual(visibleIds(), [4, 5, 6]);
    if (failed) live.emit("error", { error: { code: "model_timeout", message: "Przekroczono czas." }, status: 504 });
    else live.emit("result", await response({ message: "Pytanie", history: [] }).json());
    live.close();
    await settle();
    assert.equal(details.open, false);
    assert.equal(details.querySelectorAll(".query-step-spinner:not([hidden])").length, 0);
    assert.equal(toggle.hidden, true);
    details.open = true;
    assert.deepEqual(visibleIds(), [1, 2, 3, 4, 5, 6]);
    assert.equal(details.closest(failed ? "#chat-error" : ".message-assistant") !== null, true);
  });
}
