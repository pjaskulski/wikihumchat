import assert from "node:assert/strict";
import test from "node:test";
import { deferred, fixture, response, settle, type } from "./helpers.mjs";

test("czas całej odpowiedzi jest wyświetlany pod jej treścią", async t => {
  const ui = fixture(t, async (_, options) => response(JSON.parse(options.body), "Odpowiedź", {
    duration_ms: 496444,
  }));
  type(ui, "Pytanie");
  ui.form.requestSubmit();
  await settle();
  const main = ui.log.querySelector(".message-assistant .message-main");
  assert.equal(main.lastElementChild.className, "message-duration");
  assert.equal(main.lastElementChild.textContent, "Czas odpowiedzi: 8 min 16 s");
});

test("przykład wypełnia pole, ale wysłanie wymaga działania użytkownika", t => {
  let calls = 0;
  const ui = fixture(t, () => { calls += 1; });
  assert.equal(ui.send.disabled, true);
  assert.equal(ui.input.disabled, false);
  ui.document.querySelector("[data-question]").click();
  assert.match(ui.input.value, /Jan Kostka/);
  assert.equal(ui.send.disabled, false);
  assert.equal(ui.document.activeElement, ui.input);
  assert.equal(calls, 0);
});

test("odpowiedź ma formatowanie i źródła, następne pytanie otrzymuje historię", async t => {
  const requests = [];
  const ui = fixture(t, async (url, options) => {
    assert.equal(url, "/api/chat");
    assert.equal(options.method, "POST");
    const payload = JSON.parse(options.body);
    requests.push(payload);
    return response(payload);
  });
  type(ui, "Pytanie pierwsze");
  ui.form.requestSubmit();
  assert.equal(ui.send.disabled, true);
  assert.equal(ui.input.readOnly, true);
  assert.match(ui.document.getElementById("status-text").textContent, /Wyszukiwanie/);
  await settle();
  assert.equal(ui.log.querySelector("strong").textContent, "WikiHum");
  assert.equal(ui.log.querySelector(".source-list a").href,
    "https://wikihum.lab.dariah.pl/wiki/Item:Q158343");
  assert.equal(ui.input.value, "");
  assert.equal(ui.input.readOnly, false);
  type(ui, "Pytanie uzupełniające");
  ui.form.requestSubmit();
  await settle();
  assert.deepEqual(requests[0].history, []);
  assert.equal(requests[1].history.length, 2);
  assert.equal(requests[1].history[0].content, "Pytanie pierwsze");
  assert.equal(ui.log.querySelectorAll("article").length, 4);
  assert.equal(ui.window.localStorage.length, 0);
  assert.equal(ui.window.sessionStorage.length, 0);
  assert.equal(ui.document.cookie, "");
});

test("Enter wysyła, Shift+Enter i składanie znaków nie uruchamiają wysyłania", async t => {
  let calls = 0;
  const ui = fixture(t, async (_, options) => {
    calls += 1;
    return response(JSON.parse(options.body));
  });
  type(ui, "Pytanie");
  for (const options of [{ shiftKey: true }, { isComposing: true }]) {
    const event = new ui.window.KeyboardEvent("keydown", { key: "Enter", cancelable: true, ...options });
    ui.input.dispatchEvent(event);
    assert.equal(event.defaultPrevented, false);
    assert.equal(calls, 0);
  }
  const enter = new ui.window.KeyboardEvent("keydown", { key: "Enter", cancelable: true });
  ui.input.dispatchEvent(enter);
  assert.equal(enter.defaultPrevented, true);
  await settle();
  assert.equal(calls, 1);
});

test("błąd sieci zachowuje pytanie i historię, a ponowienie nie dubluje wiadomości", async t => {
  const requests = [];
  const ui = fixture(t, async (_, options) => {
    const payload = JSON.parse(options.body);
    requests.push(payload);
    if (requests.length === 2) throw new TypeError("network failed");
    return response(payload);
  });
  type(ui, "Pierwsze");
  ui.form.requestSubmit();
  await settle();
  type(ui, "Drugie");
  ui.form.requestSubmit();
  await settle();
  assert.equal(ui.input.value, "Drugie");
  assert.equal(ui.log.querySelectorAll("article").length, 2);
  assert.equal(ui.error.hidden, false);
  assert.equal(ui.document.activeElement, ui.input);
  ui.form.requestSubmit();
  await settle();
  assert.deepEqual(requests[2].history, requests[1].history);
  assert.equal(ui.log.querySelectorAll("article").length, 4);
  assert.equal(ui.error.hidden, true);
});

test("komunikat błędu API jest tekstem i pozostawia możliwość ponowienia", async t => {
  const ui = fixture(t, async () => ({ ok: false, json: async () => ({
    error: { code: "context_too_large", message: "Limit <img src=x onerror=alert(1)>" },
  }) }));
  type(ui, "Pytanie");
  ui.form.requestSubmit();
  await settle();
  assert.equal(ui.error.getAttribute("role"), "alert");
  assert.match(ui.error.textContent, /<img/);
  assert.equal(ui.error.querySelector("img"), null);
  assert.equal(ui.input.value, "Pytanie");
  assert.equal(ui.send.disabled, false);
  assert.equal(ui.document.getElementById("welcome").hidden, false);
});

test("odpowiedź HTML zamiast JSON jest obsłużona bez wyświetlania surowej treści", async t => {
  const ui = fixture(t, async () => ({ ok: false, json: async () => { throw new SyntaxError("raw HTML"); } }));
  type(ui, "Pytanie");
  ui.form.requestSubmit();
  await settle();
  assert.match(ui.error.textContent, /Spróbuj ponownie/);
  assert.doesNotMatch(ui.error.textContent, /raw HTML/);
  assert.equal(ui.input.readOnly, false);
});

test("nowa rozmowa ignoruje spóźnioną odpowiedź i nie zakłóca następnego żądania", async t => {
  const first = deferred();
  const second = deferred();
  const requests = [];
  let firstSignal;
  const ui = fixture(t, (_, options) => {
    requests.push(JSON.parse(options.body));
    if (requests.length === 1) { firstSignal = options.signal; return first.promise; }
    return second.promise;
  });
  type(ui, "Stara rozmowa");
  ui.form.requestSubmit();
  ui.reset.click();
  assert.equal(firstSignal.aborted, true);
  assert.equal(ui.log.children.length, 0);
  assert.equal(ui.input.value, "");
  type(ui, "Nowa rozmowa");
  ui.form.requestSubmit();
  first.resolve(response(requests[0], "Spóźniona odpowiedź"));
  await settle();
  assert.equal(ui.input.readOnly, true);
  assert.equal(ui.send.disabled, true);
  assert.doesNotMatch(ui.log.textContent, /Spóźniona/);
  second.resolve(response(requests[1], "Aktualna odpowiedź"));
  await settle();
  assert.deepEqual(requests[1].history, []);
  assert.match(ui.log.textContent, /Aktualna odpowiedź/);
  assert.doesNotMatch(ui.log.textContent, /Stara rozmowa/);
});

test("limit oczekiwania przerywa żądanie i zachowuje pytanie", async t => {
  let expire;
  const ui = fixture(t, (_, options) => new Promise((_, reject) => {
    options.signal.addEventListener("abort", () => reject(new Error("aborted")));
  }), { prepare(window) { window.setTimeout = callback => { expire = callback; return 1; }; } });
  type(ui, "Pytanie");
  ui.form.requestSubmit();
  expire();
  await settle();
  assert.match(ui.error.textContent, /czas oczekiwania/);
  assert.equal(ui.input.value, "Pytanie");
  assert.equal(ui.send.disabled, false);
});

test("ostrzeżenia kontekstu i limitów są widoczne przy odpowiedzi", async t => {
  const ui = fixture(t, async (_, options) => response(JSON.parse(options.body), "Odpowiedź", {
    warnings: ["Usunięto najstarsze wiadomości.", "Osiągnięto limit rund."],
  }));
  type(ui, "Pytanie");
  ui.form.requestSubmit();
  await settle();
  assert.equal(ui.log.querySelectorAll(".message-warning").length, 2);
  assert.match(ui.log.textContent, /limit rund/);
});

test("licznik Unicode i limit pytania odpowiadają liczbie znaków po stronie Pythona", t => {
  const ui = fixture(t, async () => {}, { limit: "2" });
  type(ui, "😀ą");
  assert.equal(ui.send.disabled, false);
  assert.equal(ui.document.getElementById("character-count").textContent, "2 / 2");
  type(ui, "😀ąx");
  assert.equal(ui.send.disabled, true);
  assert.equal(ui.input.getAttribute("aria-invalid"), "true");
});

test("powtórne wysłanie w czasie oczekiwania nie tworzy nowego żądania", async t => {
  const pending = deferred();
  let calls = 0;
  const ui = fixture(t, () => { calls += 1; return pending.promise; });
  type(ui, "Pytanie");
  ui.form.requestSubmit();
  ui.form.requestSubmit();
  assert.equal(calls, 1);
  assert.equal(ui.log.children.length, 1);
  ui.reset.click();
  pending.resolve(response({ history: [], message: "Pytanie" }));
  await settle();
});

test("odtworzenie z pamięci nawigacji czyści kontekst rozmowy", async t => {
  const ui = fixture(t, async (_, options) => response(JSON.parse(options.body)));
  type(ui, "Pytanie");
  ui.form.requestSubmit();
  await settle();
  assert.equal(ui.log.children.length, 2);
  ui.window.dispatchEvent(new ui.window.PageTransitionEvent("pageshow", { persisted: true }));
  assert.equal(ui.log.children.length, 0);
  assert.equal(ui.document.getElementById("welcome").hidden, false);
});

test("uszkodzony kontrakt odpowiedzi nie zmienia historii", async t => {
  const ui = fixture(t, async () => ({ ok: true, json: async () => ({
    answer: "Nieprawidłowa", history: [{ role: "system", content: "x" }], sources: [], warnings: [],
  }) }));
  type(ui, "Pytanie");
  ui.form.requestSubmit();
  await settle();
  assert.match(ui.error.textContent, /niepoprawną odpowiedź/);
  assert.equal(ui.log.children.length, 0);
  assert.equal(ui.input.value, "Pytanie");
});

test("treść użytkownika i modelu nie wykonuje HTML, a źródła mają bezpieczne adresy", async t => {
  const ui = fixture(t, async (_, options) => response(JSON.parse(options.body),
    '<script>alert(1)</script> [zły](javascript:alert) **tekst**', {
      sources: [{ id: "zły", url: "javascript:alert(1)" }],
    }));
  type(ui, "<img src=x onerror=alert(1)>");
  ui.form.requestSubmit();
  await settle();
  assert.equal(ui.log.querySelector("script, img"), null);
  assert.equal(ui.log.querySelector("a"), null);
  assert.match(ui.log.textContent, /<script>/);
  assert.equal(ui.log.querySelector("strong").textContent, "tekst");
});

for (const status of [429, 503]) {
  test(`HTTP ${status} zachowuje pytanie i nie uruchamia automatycznych ponowień`, async t => {
    let calls = 0;
    const ui = fixture(t, async () => {
      calls += 1;
      return { ok: false, status, json: async () => ({
        error: { message: "Spróbuj ponownie za 5 s." },
      }) };
    });
    type(ui, "Pytanie do ponowienia");
    ui.form.requestSubmit();
    await settle();
    assert.equal(ui.input.value, "Pytanie do ponowienia");
    assert.equal(ui.send.disabled, false);
    assert.equal(ui.log.children.length, 0);
    assert.match(ui.error.textContent, /za 5 s/);
    assert.equal(calls, 1);
  });
}
