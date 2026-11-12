import { externalLink, renderMarkdown, safeUrl } from "./markdown.js";
import { readEventReply } from "./events.js";

let progressSequence = 0;

function queryProgress(document) {
  const details = document.createElement("details");
  details.className = "query-progress";
  details.open = true;
  const summary = document.createElement("summary");
  summary.textContent = "Przebieg kwerendy — w toku";
  const list = document.createElement("ol");
  list.id = `query-steps-${++progressSequence}`;
  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "query-progress-toggle";
  toggle.setAttribute("aria-controls", list.id);
  toggle.hidden = true;
  details.append(summary, list, toggle);
  const rows = new Map();
  let showAll = false;
  let finished = false;
  function refresh() {
    const compact = !finished && !showAll;
    details.classList.toggle("is-compact", compact);
    details.classList.toggle("is-expanded", !finished && showAll);
    const recentIds = new Set([...rows.keys()].slice(-3));
    for (const [id, row] of rows) row.item.hidden = compact && !recentIds.has(id);
    toggle.hidden = finished || rows.size <= 3;
    toggle.setAttribute("aria-expanded", String(showAll));
    toggle.textContent = showAll ? "Pokaż ostatnie 3 etapy" : `Pokaż cały przebieg (${rows.size})`;
  }
  toggle.addEventListener("click", () => {
    showAll = !showAll;
    refresh();
  });
  return {
    element: details,
    update(event) {
      if (!event || !Number.isSafeInteger(event.id) || event.id < 1
          || typeof event.message !== "string"
          || !["running", "done", "error", "blocked"].includes(event.state)) return false;
      let row = rows.get(event.id);
      if (!row) {
        if (rows.size >= 200) return false;
        const item = document.createElement("li");
        item.value = event.id;
        const title = document.createElement("span");
        title.className = "query-step-title";
        title.title = event.message.slice(0, 300);
        const label = document.createElement("span");
        label.className = "query-step-label";
        label.textContent = event.message.slice(0, 300);
        const spinner = document.createElement("span");
        spinner.className = "query-step-spinner";
        spinner.setAttribute("aria-hidden", "true");
        spinner.hidden = true;
        title.append(label, spinner);
        const state = document.createElement("span");
        state.className = "query-step-state";
        item.append(title, state);
        list.append(item);
        row = { item, state, spinner };
        rows.set(event.id, row);
      }
      row.item.dataset.state = event.state;
      row.spinner.hidden = event.state !== "running";
      const seconds = typeof event.seconds === "number" && Number.isFinite(event.seconds)
        && event.seconds >= 0 ? `${event.seconds.toLocaleString("pl-PL", { maximumFractionDigits: 2 })} s` : "";
      const label = { running: "W toku…", done: "Zakończono", error: "Błąd", blocked: "Wymaga weryfikacji" }[event.state];
      row.state.textContent = seconds ? `${label} · ${seconds}` : label;
      refresh();
      return true;
    },
    finish(failed = false) {
      finished = true;
      summary.textContent = failed ? "Przebieg nieudanej kwerendy" : "Przebieg kwerendy";
      details.open = false;
      for (const row of rows.values()) {
        row.spinner.hidden = true;
        if (row.item.dataset.state === "running") {
          row.item.dataset.state = "unknown";
          row.state.textContent = "Nie otrzymano zakończenia etapu";
        }
      }
      refresh();
    },
  };
}

// Stan rozmowy pozostaje w domknięciu. Nie używamy cookies ani Web Storage.
export function mountChat(document, fetchImpl = (...args) => document.defaultView.fetch(...args)) {
  const window = document.defaultView;
  const root = document.getElementById("chat-app");
  const chatUrl = root.dataset.chatUrl || "api/chat";
  const exportUrl = root.dataset.exportUrl || "api/export/pdf";
  const form = document.getElementById("chat-form");
  const input = document.getElementById("question");
  const send = document.getElementById("send-question");
  const reset = document.getElementById("new-conversation");
  const exportButton = document.getElementById("export-pdf");
  const exportError = document.getElementById("export-error");
  const welcome = document.getElementById("welcome");
  const log = document.getElementById("messages");
  const scroll = document.getElementById("conversation-scroll");
  const status = document.getElementById("chat-status");
  const statusText = document.getElementById("status-text");
  const errorBox = document.getElementById("chat-error");
  const count = document.getElementById("character-count");
  const examples = [...document.querySelectorAll("[data-question]")];
  const limit = Number(root.dataset.questionLimit);
  const timeout = Number(root.dataset.requestTimeout);
  let history = [];
  let exportMessages = [];
  let exportController = null;
  let exportGeneration = 0;
  let active = null;
  let generation = 0;

  function updateInput() {
    const length = Array.from(input.value).length;
    count.textContent = `${length} / ${limit}`;
    count.classList.toggle("is-over-limit", length > limit);
    input.setAttribute("aria-invalid", String(length > limit));
    send.disabled = Boolean(active) || !input.value.trim() || length > limit;
    exportButton.hidden = !exportMessages.length;
    exportButton.disabled = Boolean(active || exportController) || !exportMessages.length;
  }

  function setBusy(busy) {
    input.readOnly = busy;
    log.setAttribute("aria-busy", String(busy));
    status.hidden = busy || !statusText.textContent;
    for (const example of examples) example.disabled = busy;
    updateInput();
  }

  function message(role, text, sources = [], warnings = []) {
    const article = document.createElement("article");
    article.className = `message message-${role}`;
    const avatar = document.createElement("span");
    avatar.className = "message-avatar";
    avatar.setAttribute("aria-hidden", "true");
    avatar.textContent = role === "user" ? "TY" : "WH";
    const main = document.createElement("div");
    main.className = "message-main";
    const author = document.createElement("h3");
    author.className = "message-author";
    author.textContent = role === "user" ? "Ty" : "WikiHum";
    const body = document.createElement("div");
    body.className = "message-body";
    if (role === "user") body.textContent = text;
    else body.append(renderMarkdown(document, text));
    main.append(author, body);
    const validSources = sources.filter(item => (
      item && typeof item.id === "string" && typeof item.url === "string" && safeUrl(item.url)
    ));
    if (validSources.length) {
      const section = document.createElement("details");
      section.className = "message-sources";
      const title = document.createElement("summary");
      title.className = "sources-title";
      title.textContent = "Rekordy wykorzystane w odpowiedzi";
      const list = document.createElement("ul");
      list.className = "source-list";
      for (const source of validSources) {
        const li = document.createElement("li");
        li.append(externalLink(document, `${source.id} ↗`, source.url));
        list.append(li);
      }
      section.append(title, list);
      main.append(section);
    }
    for (const warning of warnings) {
      const note = document.createElement("p");
      note.className = "message-warning";
      note.textContent = warning;
      main.append(note);
    }
    article.append(avatar, main);
    return article;
  }

  function validResponse(data) {
    return data && typeof data.answer === "string" && data.answer.trim()
      && Array.isArray(data.history) && data.history.length >= 2
      && data.history.length % 2 === 0 && data.history.every((item, index) => (
        item && item.role === (index % 2 === 0 ? "user" : "assistant")
        && typeof item.content === "string"
      )) && Array.isArray(data.sources) && Array.isArray(data.warnings)
      && data.warnings.every(item => typeof item === "string");
  }

  async function submit(event) {
    event.preventDefault();
    if (active) return;
    const question = input.value.trim();
    if (!question || Array.from(question).length > limit) return;
    errorBox.hidden = true;
    errorBox.textContent = "";
    welcome.hidden = true;
    const userMessage = message("user", question);
    const trace = queryProgress(document);
    log.append(userMessage);
    const id = ++generation;
    const controller = new window.AbortController();
    let timedOut = false;
    const timer = window.setTimeout(() => { timedOut = true; controller.abort(); }, timeout);
    active = { id, controller, timer };
    setBusy(true);
    statusText.textContent = "Wyszukiwanie w WikiHum…";
    scroll.scrollTop = scroll.scrollHeight;
    try {
      const response = await fetchImpl(chatUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Accept": "text/event-stream" },
        credentials: "same-origin",
        cache: "no-store",
        signal: controller.signal,
        body: JSON.stringify({ message: question, history }),
      });
      const streamed = response.ok && response.headers?.get("content-type")?.includes("text/event-stream");
      const data = streamed ? await readEventReply(response, event => {
        if (id !== generation) return;
        const nearBottom = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 96;
        if (trace.update(event)) {
          if (!trace.element.isConnected) userMessage.querySelector(".message-main").append(trace.element);
          if (nearBottom) scroll.scrollTop = scroll.scrollHeight;
        }
      }, controller.signal) : await response.json();
      if (id !== generation) return;
      if (!response.ok) {
        const detail = data?.error?.message;
        throw new Error(typeof detail === "string" ? detail.slice(0, 1000) : "Nie udało się uzyskać odpowiedzi. Spróbuj ponownie.");
      }
      if (!validResponse(data)) throw new Error("Otrzymano niepoprawną odpowiedź. Spróbuj ponownie.");
      const answer = message("assistant", data.answer, data.sources, data.warnings);
      if (trace.element.isConnected) {
        trace.finish();
        answer.querySelector(".message-main").append(trace.element);
      }
      if (typeof data.duration_ms === "number" && Number.isFinite(data.duration_ms)
          && data.duration_ms >= 0) {
        const seconds = Math.round(data.duration_ms / 1000);
        const elapsed = seconds < 60
          ? `${(data.duration_ms / 1000).toLocaleString("pl-PL", { maximumFractionDigits: 1 })} s`
          : `${Math.floor(seconds / 60)} min ${seconds % 60} s`;
        const timing = document.createElement("p");
        timing.className = "message-duration";
        timing.textContent = `Czas odpowiedzi: ${elapsed}`;
        answer.querySelector(".message-main").append(timing);
      }
      log.append(answer);
      history = data.history.map(item => ({ role: item.role, content: item.content }));
      const sourceLinks = data.sources.filter(source => source && /^Q[1-9][0-9]*$/.test(source.id)
        && typeof source.url === "string" && safeUrl(source.url))
        .map(source => `[${source.id}](${source.url})`);
      const notes = data.warnings.filter(warning => typeof warning === "string");
      const exportedAnswer = data.answer
        + (notes.length ? `\n\n## Uwagi aplikacji\n${notes.map(note => `- ${note}`).join("\n")}` : "")
        + (sourceLinks.length ? `\n\n## Rekordy wykorzystane w odpowiedzi\n${sourceLinks.join(", ")}` : "");
      exportMessages.push({ role: "user", content: question }, { role: "assistant", content: exportedAnswer });
      input.value = "";
      statusText.textContent = "Odpowiedź gotowa. Możesz zadać pytanie uzupełniające.";
      // Po przeniesieniu i zwinięciu przebiegu mierz aktualne położenie odpowiedzi.
      scroll.scrollTop += answer.getBoundingClientRect().top
        - scroll.getBoundingClientRect().top - scroll.clientTop;
    } catch (error) {
      if (id !== generation) return;
      controller.abort();
      const hasTrace = trace.element.isConnected;
      userMessage.remove();
      input.value = question;
      welcome.hidden = log.children.length > 0;
      const detail = timedOut ? "Przekroczono czas oczekiwania. Spróbuj ponownie."
        : error instanceof TypeError || error instanceof SyntaxError
          ? "Nie udało się połączyć z usługą. Spróbuj ponownie."
          : error.message || "Nie udało się uzyskać odpowiedzi. Spróbuj ponownie.";
      const errorMessage = document.createElement("p");
      errorMessage.className = "chat-error-message";
      errorMessage.textContent = detail;
      errorBox.replaceChildren(errorMessage);
      if (hasTrace) {
        trace.finish(true);
        errorBox.append(trace.element);
      }
      errorBox.hidden = false;
      statusText.textContent = "Nie udało się uzyskać odpowiedzi.";
    } finally {
      window.clearTimeout(timer);
      if (id === generation) {
        active = null;
        setBusy(false);
        input.focus({ preventScroll: true });
      }
    }
  }

  function newConversation() {
    generation += 1;
    exportGeneration += 1;
    if (active) {
      active.controller.abort();
      window.clearTimeout(active.timer);
    }
    active = null;
    exportController?.abort();
    exportController = null;
    exportButton.textContent = "Pobierz PDF";
    exportMessages = [];
    exportError.hidden = true;
    exportError.textContent = "";
    history = [];
    log.replaceChildren();
    welcome.hidden = false;
    errorBox.hidden = true;
    errorBox.textContent = "";
    input.value = "";
    setBusy(false);
    statusText.textContent = "";
    status.hidden = true;
    scroll.scrollTop = 0;
    input.focus({ preventScroll: true });
  }

  function onKeyDown(event) {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      if (!send.disabled) form.requestSubmit();
    }
  }

  function selectExample(event) {
    if (active) return;
    input.value = event.currentTarget.dataset.question;
    updateInput();
    input.focus({ preventScroll: true });
  }

  async function exportPdf() {
    if (exportButton.disabled) return;
    const controller = new window.AbortController();
    const id = exportGeneration;
    exportController = controller;
    exportError.hidden = true;
    exportButton.textContent = "Przygotowywanie PDF…";
    updateInput();
    const timer = window.setTimeout(() => controller.abort(), 30_000);
    try {
      const reply = await fetchImpl(exportUrl, {
        method: "POST", headers: { "Content-Type": "application/json", "Accept": "application/pdf" },
        credentials: "same-origin", cache: "no-store", signal: controller.signal,
        body: JSON.stringify({ messages: exportMessages }),
      });
      if (!reply.ok) {
        const data = await reply.json();
        throw new Error(data?.error?.message || "Nie udało się przygotować PDF.");
      }
      if (!reply.headers.get("content-type")?.includes("application/pdf")) {
        throw new Error("Serwer nie zwrócił dokumentu PDF.");
      }
      const blob = await reply.blob();
      if (id !== exportGeneration || controller.signal.aborted) return;
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      const filename = reply.headers.get("content-disposition")?.match(/filename="([^"]+)"/)?.[1];
      link.download = /^wikihum-chat(?:-[a-z0-9]+)*\.pdf$/.test(filename || "")
        ? filename : "wikihum-chat.pdf";
      document.body.append(link);
      link.click();
      link.remove();
      window.setTimeout(() => window.URL.revokeObjectURL(url), 1000);
    } catch (error) {
      if (id !== exportGeneration) return;
      exportError.textContent = controller.signal.aborted ? "Przekroczono czas przygotowania PDF. Spróbuj ponownie."
        : error instanceof TypeError ? "Nie udało się pobrać PDF. Sprawdź połączenie."
          : String(error.message || "Nie udało się przygotować PDF.").slice(0, 1000);
      exportError.hidden = false;
    } finally {
      window.clearTimeout(timer);
      if (exportController === controller) {
        exportController = null;
        exportButton.textContent = "Pobierz PDF";
        updateInput();
      }
    }
  }

  form.addEventListener("submit", submit);
  input.addEventListener("input", updateInput);
  input.addEventListener("keydown", onKeyDown);
  reset.addEventListener("click", newConversation);
  exportButton.addEventListener("click", exportPdf);
  for (const example of examples) example.addEventListener("click", selectExample);
  input.disabled = false;
  reset.disabled = false;
  setBusy(false);
  // Odtworzenie karty z bfcache również rozpoczyna nową rozmowę.
  function onPageShow(event) { if (event.persisted) newConversation(); }
  window.addEventListener("pageshow", onPageShow);

  return {
    destroy() {
      generation += 1;
      exportGeneration += 1;
      active?.controller.abort();
      exportController?.abort();
      if (active) window.clearTimeout(active.timer);
      form.removeEventListener("submit", submit);
      input.removeEventListener("input", updateInput);
      input.removeEventListener("keydown", onKeyDown);
      reset.removeEventListener("click", newConversation);
      exportButton.removeEventListener("click", exportPdf);
      for (const example of examples) example.removeEventListener("click", selectExample);
      window.removeEventListener("pageshow", onPageShow);
    },
  };
}

if (typeof document !== "undefined" && document.getElementById("chat-app")) mountChat(document);
