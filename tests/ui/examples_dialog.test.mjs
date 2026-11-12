import assert from "node:assert/strict";
import test from "node:test";
import { deferred, fixture, response, settle, type } from "./helpers.mjs";

function setup(t, fetchImpl) {
  const ui = fixture(t, fetchImpl, { prepare(window) {
    // JSDOM nie implementuje modalności; Chrome obsługuje ją natywnie.
    window.HTMLDialogElement.prototype.showModal = function () {
      this.setAttribute("open", "");
      this.querySelector("button")?.focus();
    };
    window.HTMLDialogElement.prototype.close = function () {
      this.removeAttribute("open");
      this.dispatchEvent(new window.Event("close"));
    };
  } });
  return { ...ui, more: ui.document.getElementById("more-examples"),
    dialog: ui.document.getElementById("examples-dialog"),
    close: ui.document.getElementById("close-examples") };
}

test("cztery przykłady są na stronie, a wybór z większej listy zamyka okno bez wysłania", t => {
  let requests = 0;
  const ui = setup(t, () => { requests += 1; });
  assert.equal(ui.document.querySelectorAll(".examples [data-question]").length, 4);
  const choices = ui.dialog.querySelectorAll("[data-question]");
  assert.ok(choices.length > 4);
  assert.equal(ui.more.hidden, false);
  assert.equal(ui.dialog.open, false);
  ui.more.click();
  assert.equal(ui.dialog.open, true);
  choices[4].click();
  assert.equal(ui.dialog.open, false);
  assert.equal(ui.input.value, choices[4].dataset.question);
  assert.equal(ui.document.activeElement, ui.input);
  assert.equal(ui.send.disabled, false);
  assert.equal(requests, 0);
});

test("zamknięcie przyciskiem i Escape zachowuje szkic i przywraca fokus", t => {
  const ui = setup(t, () => {});
  type(ui, "Robocze pytanie");
  ui.more.click();
  ui.close.click();
  assert.equal(ui.dialog.open, false);
  assert.equal(ui.input.value, "Robocze pytanie");
  assert.equal(ui.document.activeElement, ui.more);
  ui.more.click();
  const cancel = new ui.window.Event("cancel", { cancelable: true });
  ui.dialog.dispatchEvent(cancel);
  assert.equal(cancel.defaultPrevented, true);
  assert.equal(ui.dialog.open, false);
  assert.equal(ui.document.activeElement, ui.more);
});

test("kliknięcie tła zamyka okno, ale kliknięcie pustego miejsca wewnątrz nie", t => {
  const ui = setup(t, () => {});
  ui.dialog.getBoundingClientRect = () => ({ left: 10, top: 10, right: 300, bottom: 300 });
  ui.more.click();
  ui.dialog.dispatchEvent(new ui.window.MouseEvent("click", { clientX: 20, clientY: 20 }));
  assert.equal(ui.dialog.open, true);
  ui.dialog.dispatchEvent(new ui.window.MouseEvent("click", { clientX: 0, clientY: 0 }));
  assert.equal(ui.dialog.open, false);
});

test("wysłanie pytania zamyka okno i blokuje kolejne wybory podczas pracy", async t => {
  const pending = deferred();
  const ui = setup(t, () => pending.promise);
  type(ui, "Pytanie");
  ui.more.click();
  ui.form.requestSubmit();
  assert.equal(ui.dialog.open, false);
  assert.equal(ui.more.disabled, true);
  assert.ok([...ui.dialog.querySelectorAll("button[data-question]")].every(button => button.disabled));
  pending.resolve(response({ message: "Pytanie", history: [] }));
  await settle();
  assert.equal(ui.more.disabled, false);
});
