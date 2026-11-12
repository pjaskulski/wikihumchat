import assert from "node:assert/strict";
import test from "node:test";
import { readEventReply } from "../../static/events.js";

const encoder = new TextEncoder();
const event = (kind, data) => `event: ${kind}\ndata: ${JSON.stringify(data)}\n\n`;
const settle = () => new Promise(resolve => setImmediate(resolve));

test("fragmenty UTF-8 i CRLF są składane, a postęp dociera przed wynikiem", async () => {
  let controller;
  const body = new ReadableStream({ start(value) { controller = value; } });
  const progress = [];
  const result = readEventReply({ body }, value => progress.push(value));
  const bytes = encoder.encode(event("progress", { message: "Łączenie…" }).replaceAll("\n", "\r\n"));
  for (const byte of bytes) controller.enqueue(Uint8Array.of(byte));
  await settle();
  assert.deepEqual(progress, [{ message: "Łączenie…" }]);
  controller.enqueue(encoder.encode(": keep-alive\n\n" + event("result", { answer: "Gotowe" })));
  controller.close();
  assert.deepEqual(await result, { answer: "Gotowe" });
});

test("błąd strumienia jest odróżniany od gotowej odpowiedzi", async () => {
  const body = new ReadableStream({ start(controller) {
    controller.enqueue(encoder.encode(event("error", {
      error: { code: "context_too_large", message: "Przekroczono limit kontekstu." }, status: 413,
    })));
    controller.close();
  } });
  await assert.rejects(readEventReply({ body }, () => {}), /limit kontekstu/);
});

test("zakończenie po samym postępie nie jest traktowane jako sukces", async () => {
  const body = new ReadableStream({ start(controller) {
    controller.enqueue(encoder.encode(event("progress", { id: 1 })));
    controller.close();
  } });
  await assert.rejects(readEventReply({ body }, () => {}), /przed otrzymaniem pełnej odpowiedzi/);
});

test("niepoprawny JSON daje bezpieczny błąd protokołu", async () => {
  const body = new ReadableStream({ start(controller) {
    controller.enqueue(encoder.encode("event: result\ndata: private-invalid-json\n\n"));
    controller.close();
  } });
  await assert.rejects(readEventReply({ body }, () => {}), error => (
    /niepoprawne zdarzenie/.test(error.message) && !error.message.includes("private")
  ));
});

test("anulowanie odblokowuje czytnik czekający na kolejne zdarzenie", async () => {
  let cancelled = false;
  const controller = new AbortController();
  const body = new ReadableStream({ cancel() { cancelled = true; } });
  const result = readEventReply({ body }, () => {}, controller.signal);
  controller.abort();
  await assert.rejects(result, /Przerwano/);
  assert.equal(cancelled, true);
});

test("bufor nie rośnie bez ograniczeń przy braku separatora zdarzeń", async () => {
  const body = new ReadableStream({ start(controller) {
    controller.enqueue(encoder.encode("x".repeat(2_000_001)));
    controller.close();
  } });
  await assert.rejects(readEventReply({ body }, () => {}), /zbyt duże/);
});
