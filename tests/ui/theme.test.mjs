import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { JSDOM } from "jsdom";

const script = readFileSync(new URL("../../static/theme.js", import.meta.url), "utf8");
const key = "wikihum-chat-theme";

function setup(t, { saved, dark = false, blocked = false } = {}) {
  const dom = new JSDOM('<html><body><button id="theme-toggle" hidden><svg data-theme-icon="sun" hidden></svg><svg data-theme-icon="moon"></svg></button></body></html>',
    { url: "https://ai.ihpan.edu.pl/wikihumchat/", runScripts: "outside-only" });
  t.after(() => dom.window.close());
  const { window } = dom;
  let listener;
  const media = { matches: dark, addEventListener(_, callback) { listener = callback; } };
  window.matchMedia = () => media;
  if (saved) window.localStorage.setItem(key, saved);
  if (blocked) Object.defineProperty(window, "localStorage", { get() { throw new Error("blocked"); } });
  window.eval(script);
  window.document.dispatchEvent(new window.Event("DOMContentLoaded"));
  return { window, button: window.document.getElementById("theme-toggle"),
    system(value) { media.matches = value; listener(); } };
}

test("motyw systemowy jest stosowany od razu, a ikona wskazuje możliwą zmianę", t => {
  const ui = setup(t, { dark: true });
  assert.equal(ui.window.document.documentElement.dataset.theme, "dark");
  assert.equal(ui.button.hidden, false);
  assert.equal(ui.button.getAttribute("aria-label"), "Włącz tryb dzienny");
  assert.equal(ui.button.querySelector('[data-theme-icon="sun"]').hasAttribute("hidden"), false);
  assert.equal(ui.button.querySelector('[data-theme-icon="moon"]').hasAttribute("hidden"), true);
  assert.equal(ui.window.localStorage.length, 0);
});

test("ręczny wybór jest zapamiętywany i ma pierwszeństwo przed zmianą systemu", t => {
  const ui = setup(t, { saved: "light", dark: true });
  assert.equal(ui.window.document.documentElement.dataset.theme, "light");
  ui.button.click();
  assert.equal(ui.window.localStorage.getItem(key), "dark");
  ui.system(false);
  assert.equal(ui.window.document.documentElement.dataset.theme, "dark");
  ui.button.click();
  assert.equal(ui.window.localStorage.getItem(key), "light");
});

test("bez ręcznego wyboru zmiana motywu systemowego aktualizuje stronę", t => {
  const ui = setup(t);
  ui.system(true);
  assert.equal(ui.window.document.documentElement.dataset.theme, "dark");
});

test("niedostępny localStorage nie blokuje przełączania motywu", t => {
  const ui = setup(t, { blocked: true });
  ui.button.click();
  assert.equal(ui.window.document.documentElement.dataset.theme, "dark");
});

test("zmiana preferencji w innej karcie aktualizuje motyw", t => {
  const ui = setup(t);
  ui.window.dispatchEvent(new ui.window.StorageEvent("storage", { key, newValue: "dark" }));
  assert.equal(ui.window.document.documentElement.dataset.theme, "dark");
});

test("etykieta motywu podąża za PL / EN, zachowując wybrany motyw", t => {
  const ui = setup(t, { dark: true });
  ui.window.document.documentElement.lang = "en";
  ui.window.dispatchEvent(new ui.window.Event("languagechange"));
  assert.equal(ui.button.title, "Switch to light mode");
  assert.equal(ui.window.document.documentElement.dataset.theme, "dark");
  ui.button.click();
  assert.equal(ui.button.getAttribute("aria-label"), "Switch to dark mode");
  ui.window.document.documentElement.lang = "pl";
  ui.window.dispatchEvent(new ui.window.Event("languagechange"));
  assert.equal(ui.button.title, "Włącz tryb nocny");
});
