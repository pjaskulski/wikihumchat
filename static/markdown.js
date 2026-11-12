import { setTranslatedAttribute } from "./i18n.js";

// Ograniczony Markdown budowany wyłącznie z węzłów DOM, bez interpretacji HTML.
export function safeUrl(value) {
  try {
    const url = new URL(value);
    if (!["https:", "http:"].includes(url.protocol) || url.username || url.password) return null;
    return url.href;
  } catch {
    return null;
  }
}

export function externalLink(document, label, href) {
  const target = safeUrl(href);
  if (!target) return document.createTextNode(label);
  const link = document.createElement("a");
  link.textContent = label;
  link.href = target;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.referrerPolicy = "no-referrer";
  setTranslatedAttribute(link, "aria-label", `${label} (nowa karta)`);
  return link;
}

function inline(document, container, text, depth = 0) {
  if (depth > 4) {
    container.append(document.createTextNode(text));
    return;
  }
  const tokens = /(\\[\\`*_\[\]|]|`[^`\n]+`|\[[^\[\]\n]+\]\([^\s)]+\)|\*\*[^*\n]+\*\*|\*[^*\n]+\*)/g;
  let end = 0;
  for (const match of text.matchAll(tokens)) {
    container.append(document.createTextNode(text.slice(end, match.index)));
    const token = match[0];
    if (token.startsWith("\\")) {
      container.append(document.createTextNode(token.slice(1)));
    } else if (token.startsWith("[")) {
      const parts = /^\[([^\[\]]+)\]\(([^\s)]+)\)$/.exec(token);
      const url = safeUrl(parts[2]);
      if (url) container.append(externalLink(document, parts[1], url));
      else container.append(document.createTextNode(token));
    } else {
      const strong = token.startsWith("**");
      const code = token.startsWith("`");
      const node = document.createElement(code ? "code" : strong ? "strong" : "em");
      const content = token.slice(strong ? 2 : 1, strong ? -2 : -1);
      if (code) node.textContent = content;
      else inline(document, node, content, depth + 1);
      container.append(node);
    }
    end = match.index + token.length;
  }
  container.append(document.createTextNode(text.slice(end)));
}

function tableRow(line) {
  const text = line.trim();
  const cells = [];
  let cell = "";
  let ticks = 0;
  let separators = 0;
  for (let i = 0; i < text.length; i++) {
    if (text[i] === "\\" && "\\`*_[]|".includes(text[i + 1] ?? "\0")) {
      cell += text[i + 1] === "|" ? "|" : text.slice(i, i + 2);
      i++;
    } else if (text[i] === "`") {
      let end = i + 1;
      while (text[end] === "`") end++;
      const length = end - i;
      ticks = ticks === length ? 0 : ticks || length;
      cell += text.slice(i, end);
      i = end - 1;
    } else if (text[i] === "|" && !ticks) {
      cells.push(cell.trim());
      cell = "";
      separators++;
    } else cell += text[i];
  }
  if (!separators) return null;
  cells.push(cell.trim());
  if (cells[0] === "" && text.startsWith("|")) cells.shift();
  if (cells.at(-1) === "" && text.endsWith("|")) cells.pop();
  return cells;
}

function appendTable(document, fragment, headers, separators, rows) {
  const wrapper = document.createElement("div");
  wrapper.className = "markdown-table-scroll";
  wrapper.tabIndex = 0;
  wrapper.setAttribute("role", "region");
  setTranslatedAttribute(wrapper, "aria-label", "Tabela odpowiedzi — możliwość przewijania w poziomie");
  const table = document.createElement("table");
  const head = document.createElement("thead");
  const body = document.createElement("tbody");
  function row(values, heading) {
    const tr = document.createElement("tr");
    headers.forEach((_, index) => {
      const cell = document.createElement(heading ? "th" : "td");
      if (heading) cell.setAttribute("scope", "col");
      const alignment = separators[index];
      if (alignment.endsWith(":")) cell.className = alignment.startsWith(":") ? "align-center" : "align-right";
      if (/^(#|lp\.?|nr\.?)$/i.test(headers[index])) cell.classList.add("table-number");
      inline(document, cell, values[index] || "");
      tr.append(cell);
    });
    return tr;
  }
  head.append(row(headers, true));
  for (const values of rows) body.append(row(values, false));
  table.append(head, body);
  wrapper.append(table);
  fragment.append(wrapper);
}

export function renderMarkdown(document, text) {
  const fragment = document.createDocumentFragment();
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  let paragraph = [];
  let list = null;
  let code = null;
  function flushParagraph() {
    if (!paragraph.length) return;
    const node = document.createElement("p");
    paragraph.forEach((line, index) => {
      if (index) node.append(document.createElement("br"));
      inline(document, node, line);
    });
    fragment.append(node);
    paragraph = [];
  }
  for (let lineIndex = 0; lineIndex < lines.length; lineIndex++) {
    const line = lines[lineIndex];
    if (line.trimStart().startsWith("```")) {
      flushParagraph();
      list = null;
      if (code !== null) code = null;
      else {
        const pre = document.createElement("pre");
        code = document.createElement("code");
        pre.append(code);
        fragment.append(pre);
      }
      continue;
    }
    if (code !== null) {
      code.append(document.createTextNode(`${line}\n`));
      continue;
    }
    const headers = tableRow(line);
    const separators = tableRow(lines[lineIndex + 1] || "");
    if (headers?.length && separators?.length === headers.length &&
        separators.every(cell => /^:?-{3,}:?$/.test(cell))) {
      flushParagraph();
      list = null;
      lineIndex++;
      const rows = [];
      while (lineIndex + 1 < lines.length) {
        const next = tableRow(lines[lineIndex + 1]);
        if (!next || next.length > headers.length) break;
        rows.push(next);
        lineIndex++;
      }
      appendTable(document, fragment, headers, separators, rows);
      continue;
    }
    if (!line.trim()) {
      flushParagraph();
      list = null;
      continue;
    }
    const heading = /^#{1,6}\s+(.+)$/.exec(line);
    const item = /^\s*(?:([-+*])\s+|\d+[.)]\s+)(.+)$/.exec(line);
    const quote = /^>\s?(.*)$/.exec(line);
    if (heading || item || quote) {
      flushParagraph();
      if (item) {
        const tag = item[1] ? "ul" : "ol";
        if (!list || list.tagName.toLowerCase() !== tag) {
          list = document.createElement(tag);
          fragment.append(list);
        }
        const li = document.createElement("li");
        inline(document, li, item[2]);
        list.append(li);
      } else {
        list = null;
        const node = document.createElement(heading ? "h3" : "blockquote");
        inline(document, node, heading ? heading[1] : quote[1]);
        fragment.append(node);
      }
    } else {
      list = null;
      paragraph.push(line);
    }
  }
  flushParagraph();
  return fragment;
}
