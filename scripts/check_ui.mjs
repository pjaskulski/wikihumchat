// Test DOM z rzeczywistym API. Nie zastępuje kontroli wizualnej w przeglądarce.
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";
import { mountChat } from "../static/chat.js";

const base = new URL(process.argv[2] || "http://127.0.0.1:5000/");
const report = { timestamp: new Date().toISOString(), assets: [], requests: [], turns: [] };
let dom;
let chat;
try {
  const page = await fetch(base);
  if (!page.ok) throw new Error(`Strona główna: HTTP ${page.status}`);
  dom = new JSDOM(await page.text(), { url: base.href });
  const { document } = dom.window;
  for (const path of ["/static/style.css", "/static/chat.js", "/static/markdown.js", "/static/events.js"]) {
    const asset = await fetch(new URL(path, base));
    report.assets.push({ path, status: asset.status });
    if (!asset.ok) throw new Error(`Zasób ${path}: HTTP ${asset.status}`);
  }
  chat = mountChat(document, async (path, options) => {
    const payload = JSON.parse(options.body);
    const started = performance.now();
    // Node fetch wymaga własnego AbortSignal, zamiast sygnału środowiska jsdom.
    const controller = new AbortController();
    const abort = () => controller.abort();
    options.signal.addEventListener("abort", abort, { once: true });
    if (options.signal.aborted) controller.abort();
    try {
      const response = await fetch(new URL(path, base), { ...options, signal: controller.signal });
      report.requests.push({ status: response.status, history_sent: payload.history.length,
        headers_seconds: Number(((performance.now() - started) / 1000).toFixed(3)),
        content_type: response.headers.get("content-type") });
      return response;
    } finally {
      options.signal.removeEventListener("abort", abort);
    }
  });
  function waitForAnswer(count) {
    return new Promise((resolve, reject) => {
      let liveProgress = false;
      const timer = setTimeout(() => finish(new Error("Przekroczono czas testu.")), 330_000);
      const observer = new dom.window.MutationObserver(check);
      function finish(error) {
        clearTimeout(timer);
        observer.disconnect();
        if (error) reject(error);
        else resolve(liveProgress);
      }
      function check() {
        const questions = document.querySelectorAll(".message-user");
        const currentQuestion = questions[questions.length - 1];
        if (currentQuestion?.querySelector(".query-progress li")
            && document.querySelectorAll(".message-assistant").length < count
            && document.getElementById("question").readOnly) liveProgress = true;
        const error = document.getElementById("chat-error");
        if (!error.hidden) finish(new Error(error.querySelector(".chat-error-message")?.textContent || error.textContent));
        else if (document.querySelectorAll(".message-assistant").length === count
                 && !document.getElementById("question").readOnly) finish();
      }
      observer.observe(document.getElementById("chat-app"), { subtree: true, childList: true, attributes: true });
      check();
    });
  }
  document.querySelector("[data-question]").click();
  for (let index = 1; index <= 2; index += 1) {
    if (index === 2) {
      const input = document.getElementById("question");
      input.value = "Który z nich był opatem oliwskim? Podaj odnośnik do jego rekordu.";
      input.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
    }
    const started = performance.now();
    const ready = waitForAnswer(index);
    document.getElementById("chat-form").requestSubmit();
    const liveProgress = await ready;
    if (!liveProgress) throw new Error("Nie zaobserwowano postępu przed odpowiedzią.");
    const messages = document.querySelectorAll(".message-assistant");
    const sources = [...messages[index - 1].querySelectorAll(".source-list a")].map(link => link.href);
    if (!sources.length) throw new Error("Brak odnośników do rekordów w odpowiedzi.");
    report.turns.push({ number: index, sources, live_progress: liveProgress,
      seconds: Number(((performance.now() - started) / 1000).toFixed(3)) });
    console.log(`Tura ${index}: odpowiedź i odnośniki wyświetlone w DOM.`);
  }
  if (report.requests[0].history_sent !== 0 || report.requests[1].history_sent !== 2) {
    throw new Error("Nieprawidłowe przekazanie historii.");
  }
  document.getElementById("new-conversation").click();
  if (document.getElementById("messages").children.length) throw new Error("Nie wyczyszczono rozmowy.");
  if (dom.window.localStorage.length || dom.window.sessionStorage.length) {
    throw new Error("Nieoczekiwany zapis historii w Web Storage.");
  }
  report.status = "ok";
} catch (error) {
  report.status = "failed";
  report.error = error.message;
  process.exitCode = 1;
  console.error(error.message);
} finally {
  chat?.destroy();
  dom?.window.close();
  const directory = fileURLToPath(new URL("../reports/", import.meta.url));
  await mkdir(directory, { recursive: true });
  await writeFile(`${directory}ui.json`, `${JSON.stringify(report, null, 2)}\n`);
  console.log(`Raport: ${directory}ui.json`);
}
