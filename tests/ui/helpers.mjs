import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";
import { mountChat } from "../../static/chat.js";

const root = fileURLToPath(new URL("../../", import.meta.url));
const python = process.env.PYTHON || (existsSync(`${root}venv/bin/python`) ? `${root}venv/bin/python` : "python3");
const html = execFileSync(python, ["-c", [
  "from app import create_app",
  "from config import Settings",
  "app = create_app(Settings(model_api_key='test-only-secret'))",
  "print(app.test_client().get('/').get_data(as_text=True))",
].join("\n")], { cwd: root, encoding: "utf8" });

export function fixture(t, fetchImpl, options = {}) {
  const dom = new JSDOM(html, { url: "http://localhost/" });
  const { document } = dom.window;
  if (options.limit) document.getElementById("chat-app").dataset.questionLimit = options.limit;
  if (options.prepare) options.prepare(dom.window);
  const chat = mountChat(document, fetchImpl);
  t.after(() => { chat.destroy(); dom.window.close(); });
  return {
    window: dom.window, document,
    input: document.getElementById("question"),
    form: document.getElementById("chat-form"),
    send: document.getElementById("send-question"),
    reset: document.getElementById("new-conversation"),
    log: document.getElementById("messages"),
    error: document.getElementById("chat-error"),
  };
}

export function type(ui, text) {
  ui.input.value = text;
  ui.input.dispatchEvent(new ui.window.Event("input", { bubbles: true }));
}

export const settle = () => new Promise(resolve => setImmediate(resolve));

export function response(payload, answer = "Odpowiedź **WikiHum** Q158343.", extra = {}) {
  return {
    ok: true,
    json: async () => ({
      answer,
      history: [...payload.history, { role: "user", content: payload.message },
        { role: "assistant", content: answer }],
      sources: [{ id: "Q158343", url: "https://wikihum.lab.dariah.pl/wiki/Item:Q158343" }],
      warnings: [], ...extra,
    }),
  };
}

export function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}
