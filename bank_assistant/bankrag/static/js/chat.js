/* Chat page: sources panel, streaming answers with citations. */
(function () {
  "use strict";
  const { t, api, escapeHtml, toast } = window.App;

  const els = {
    list: document.getElementById("source-list"),
    filter: document.getElementById("source-filter"),
    count: document.getElementById("sel-count"),
    panel: document.getElementById("sources-panel"),
    backdrop: document.getElementById("backdrop"),
    messages: document.getElementById("messages"),
    empty: document.getElementById("empty-state"),
    emptyTitle: document.getElementById("empty-title"),
    emptyText: document.getElementById("empty-text"),
    examples: document.getElementById("examples"),
    noDocs: document.getElementById("no-docs"),
    form: document.getElementById("composer"),
    input: document.getElementById("question"),
    send: document.getElementById("send-btn"),
    modeFull: document.getElementById("mode-full"),
  };

  const STORE_KEY = "bankrag.deselected";
  const MODE_KEY = "bankrag.mode";
  const IS_ADMIN = document.querySelector(".chat").dataset.admin === "1";
  let docs = [];
  let deselected = new Set(loadDeselected());
  let history = [];
  let busy = false;
  let controller = null;

  function loadDeselected() {
    try { return JSON.parse(localStorage.getItem(STORE_KEY) || "[]"); } catch (e) { return []; }
  }
  function saveDeselected() {
    try { localStorage.setItem(STORE_KEY, JSON.stringify([...deselected])); } catch (e) { /* private mode */ }
  }
  try { els.modeFull.checked = localStorage.getItem(MODE_KEY) === "full"; } catch (e) { /* private mode */ }
  els.modeFull.addEventListener("change", () => {
    try { localStorage.setItem(MODE_KEY, els.modeFull.checked ? "full" : "normal"); } catch (e) { /* private mode */ }
  });

  // ------------------------------------------------------------------ sources panel

  function fileTag(ext) {
    const label = (ext || "").replace(".", "");
    return `<span class="file-tag ${escapeHtml(label)}">${escapeHtml(label.toUpperCase())}</span>`;
  }

  function docMeta(doc) {
    if (!doc.pages) return "";
    return doc.ext === ".xlsx" ? `${doc.pages} ${t("doc_sheets")}` : `${doc.pages} ${t("doc_pages")}`;
  }

  async function loadDocs() {
    try {
      const data = await api("GET", "/api/documents");
      docs = data.documents.filter((d) => d.status === "ready");
    } catch (err) {
      els.list.innerHTML = `<p class="muted small pad">${escapeHtml(err.message)}</p>`;
      return;
    }
    const ids = new Set(docs.map((d) => d.id));
    deselected = new Set([...deselected].filter((id) => ids.has(id)));
    renderSources();
    updateEmptyState();
  }

  function renderSources() {
    const query = (els.filter.value || "").trim().toLowerCase();
    const groups = new Map();
    docs.forEach((doc) => {
      if (query && !(doc.title + " " + doc.category).toLowerCase().includes(query)) return;
      const key = doc.category || "";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(doc);
    });
    if (!docs.length) {
      els.list.innerHTML = `<p class="muted small pad">${escapeHtml(t("docs_empty"))}</p>`;
    } else if (!groups.size) {
      els.list.innerHTML = `<p class="muted small pad">—</p>`;
    } else {
      const keys = [...groups.keys()].sort((a, b) => (a === "") - (b === "") || a.localeCompare(b));
      els.list.innerHTML = keys.map((key) => {
        const items = groups.get(key);
        const selectedCount = items.filter((d) => !deselected.has(d.id)).length;
        return `<div class="source-group">
          <label class="group-head">
            <input type="checkbox" data-group="${escapeHtml(key)}" ${selectedCount === items.length ? "checked" : ""}
              ${selectedCount > 0 && selectedCount < items.length ? 'data-indeterminate="1"' : ""}>
            <span>${escapeHtml(key || t("uncategorized"))}</span>
            <span class="badge">${items.length}</span>
          </label>
          ${items.map((doc) => `
            <div class="source-row">
              <label class="source-item">
                <input type="checkbox" data-doc="${escapeHtml(doc.id)}" ${deselected.has(doc.id) ? "" : "checked"}>
                ${fileTag(doc.ext)}
                <span class="doc-text"><span class="doc-name">${escapeHtml(doc.title)}</span><br><span class="doc-meta">${escapeHtml(docMeta(doc))}</span></span>
              </label>
              <button type="button" class="sum-btn" data-summarize="${escapeHtml(doc.id)}" title="${escapeHtml(t("summarize_doc"))}"
                aria-label="${escapeHtml(t("summarize_doc") + " — " + doc.title)}"><svg class="icon"><use href="#i-summary"/></svg></button>
            </div>`).join("")}
        </div>`;
      }).join("");
      els.list.querySelectorAll("[data-indeterminate]").forEach((cb) => { cb.indeterminate = true; });
    }
    const selected = docs.filter((d) => !deselected.has(d.id)).length;
    els.count.textContent = docs.length ? t("selected_count", { n: selected, total: docs.length }) : "";
  }

  els.list.addEventListener("change", (event) => {
    const cb = event.target;
    if (cb.dataset.doc) {
      cb.checked ? deselected.delete(cb.dataset.doc) : deselected.add(cb.dataset.doc);
    } else if (cb.dataset.group !== undefined) {
      docs.filter((d) => (d.category || "") === cb.dataset.group)
        .forEach((d) => (cb.checked ? deselected.delete(d.id) : deselected.add(d.id)));
    }
    saveDeselected();
    renderSources();
  });
  els.list.addEventListener("click", (event) => {
    const btn = event.target.closest("[data-summarize]");
    if (!btn) return;
    const doc = docs.find((d) => d.id === btn.dataset.summarize);
    if (!doc || busy) return;
    setPanel(false);
    summarize(doc, false);
  });
  els.filter.addEventListener("input", renderSources);
  document.getElementById("select-all").addEventListener("click", () => {
    deselected.clear(); saveDeselected(); renderSources();
  });
  document.getElementById("select-none").addEventListener("click", () => {
    docs.forEach((d) => deselected.add(d.id)); saveDeselected(); renderSources();
  });

  function setPanel(open) {
    els.panel.classList.toggle("open", open);
    els.backdrop.classList.toggle("show", open);
  }
  const openBtn = document.getElementById("open-sources");
  if (openBtn) openBtn.addEventListener("click", () => setPanel(true));
  document.getElementById("close-sources").addEventListener("click", () => setPanel(false));
  els.backdrop.addEventListener("click", () => setPanel(false));

  function updateEmptyState() {
    const none = docs.length === 0;
    els.noDocs.classList.toggle("hidden", !none);
    [els.emptyTitle, els.emptyText, els.examples].forEach((el) => el.classList.toggle("hidden", none));
    updateSendState();
  }

  // ------------------------------------------------------------------ markdown + citations

  const AR_DIGITS = "٠١٢٣٤٥٦٧٨٩";
  const toLatin = (s) => s.replace(/[٠-٩]/g, (d) => String(AR_DIGITS.indexOf(d)));

  function inline(text, maxCite) {
    let html = escapeHtml(text);
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
    html = html.replace(/\*\*([^*]+?)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/__([^_]+?)__/g, "<strong>$1</strong>");
    html = html.replace(/\[\s*([\d٠-٩]+(?:\s*[,،]\s*[\d٠-٩]+)*)\s*\]/g, (match, nums) => {
      const parts = nums.split(/[,،]/).map((n) => parseInt(toLatin(n.trim()), 10));
      if (parts.some((n) => !n || n > maxCite)) return match;
      return parts.map((n) => `<button type="button" class="cite" data-cite="${n}">${n}</button>`).join("");
    });
    return html;
  }

  function renderTable(rows, maxCite) {
    const cells = (row) => row.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
    const body = rows.filter((r) => !/^\s*\|?\s*:?-{2,}/.test(r));
    if (!body.length) return "";
    const [head, ...rest] = body;
    return "<table><thead><tr>" + cells(head).map((c) => `<th>${inline(c, maxCite)}</th>`).join("") +
      "</tr></thead><tbody>" + rest.map((r) => "<tr>" + cells(r).map((c) => `<td>${inline(c, maxCite)}</td>`).join("") + "</tr>").join("") +
      "</tbody></table>";
  }

  function renderMarkdown(src, maxCite) {
    const lines = src.replace(/\r/g, "").split("\n");
    let html = "";
    let list = null;
    let para = [];
    let table = [];
    const flushPara = () => {
      if (para.length) { html += "<p>" + para.map((l) => inline(l, maxCite)).join("<br>") + "</p>"; para = []; }
    };
    const closeList = () => { if (list) { html += `</${list}>`; list = null; } };
    const flushTable = () => { if (table.length) { html += renderTable(table, maxCite); table = []; } };
    for (const raw of lines) {
      const line = raw.replace(/\s+$/, "");
      if (/^\s*\|.*\|\s*$/.test(line)) { flushPara(); closeList(); table.push(line); continue; }
      flushTable();
      if (!line.trim()) { flushPara(); closeList(); continue; }
      let m = line.match(/^\s{0,3}#{1,6}\s+(.*)$/);
      if (m) { flushPara(); closeList(); html += `<h4>${inline(m[1].replace(/\*\*/g, ""), maxCite)}</h4>`; continue; }
      m = line.match(/^\s*[-*•●]\s+(.*)$/);
      if (m) {
        flushPara();
        if (list !== "ul") { closeList(); html += "<ul>"; list = "ul"; }
        html += `<li>${inline(m[1], maxCite)}</li>`;
        continue;
      }
      m = line.match(/^\s*([\d٠-٩]{1,3})[.)-]\s+(.*)$/);
      if (m) {
        flushPara();
        if (list !== "ol") { closeList(); html += "<ol>"; list = "ol"; }
        html += `<li>${inline(m[2], maxCite)}</li>`;
        continue;
      }
      closeList();
      para.push(line);
    }
    flushTable(); flushPara(); closeList();
    return html;
  }

  // ------------------------------------------------------------------ messages

  function scrollToBottom(force) {
    const m = els.messages;
    if (force || m.scrollHeight - m.scrollTop - m.clientHeight < 160) m.scrollTop = m.scrollHeight;
  }

  function addUserMessage(text) {
    const div = document.createElement("div");
    div.className = "msg msg-user";
    div.innerHTML = `<div class="bubble" dir="auto">${escapeHtml(text)}</div>`;
    els.messages.appendChild(div);
  }

  function addBotMessage() {
    const div = document.createElement("div");
    div.className = "msg msg-bot";
    div.innerHTML = `<svg class="bot-avatar"><use href="#i-logo"/></svg>
      <div class="bot-body">
        <div class="status"><span class="dots"><i></i><i></i><i></i></span><span data-role="stage">${escapeHtml(t("stage_searching"))}</span></div>
        <div class="answer" data-role="answer" dir="auto"></div>
        <div data-role="sources"></div>
        <div class="msg-tools hidden" data-role="tools">
          <button type="button" class="btn btn-ghost btn-sm" data-role="copy"><svg class="icon"><use href="#i-copy"/></svg>${escapeHtml(t("copy"))}</button>
        </div>
      </div>`;
    els.messages.appendChild(div);
    return {
      root: div,
      status: div.querySelector(".status"),
      stage: div.querySelector('[data-role="stage"]'),
      answer: div.querySelector('[data-role="answer"]'),
      sources: div.querySelector('[data-role="sources"]'),
      tools: div.querySelector('[data-role="tools"]'),
      copy: div.querySelector('[data-role="copy"]'),
    };
  }

  function locationText(src) {
    if (src.page_start == null) return "";
    const unit = src.ext === ".xlsx" ? t("sheet_short") : t("page_short");
    return src.page_end && src.page_end !== src.page_start
      ? `${unit} ${src.page_start}–${src.page_end}` : `${unit} ${src.page_start}`;
  }

  function sourceCard(src) {
    const loc = locationText(src);
    const href = `/files/${encodeURIComponent(src.doc_id)}` + (src.ext === ".pdf" && src.page_start ? `#page=${src.page_start}` : "");
    return `<div class="source-card" data-n="${src.n}">
      <div class="sc-head">
        <span class="sc-num">${src.n}</span>
        <div>
          <div class="sc-title" dir="auto">${escapeHtml(src.title)}</div>
          <div class="sc-loc">${escapeHtml([loc, src.heading].filter(Boolean).join(" · "))}${src.ocr
            ? ` <span class="ocr-badge" title="${escapeHtml(t("ocr_hint"))}">${escapeHtml(t("ocr_badge"))}</span>` : ""}</div>
        </div>
      </div>
      <div class="sc-text" dir="auto">${escapeHtml(src.text)}</div>
      <a class="sc-open" href="${href}" target="_blank" rel="noopener"><svg class="icon"><use href="#i-external"/></svg>${escapeHtml(t("open_document"))}</a>
    </div>`;
  }

  function renderSourcesBlock(view, sources, answerText) {
    if (!sources.length) return;
    const cited = new Set();
    const re = /\[\s*([\d٠-٩]+(?:\s*[,،]\s*[\d٠-٩]+)*)\s*\]/g;
    let m;
    while ((m = re.exec(answerText))) {
      m[1].split(/[,،]/).forEach((n) => cited.add(parseInt(toLatin(n.trim()), 10)));
    }
    const main = sources.filter((s) => cited.has(s.n));
    const rest = sources.filter((s) => !cited.has(s.n));
    let html = "";
    if (main.length) {
      html += `<div class="sources-block"><p class="sources-title">${escapeHtml(t("cited_sources"))}</p>
        <div class="source-cards">${main.map(sourceCard).join("")}</div></div>`;
    }
    if (rest.length) {
      html += `<details class="more-sources"><summary>${escapeHtml(t("other_sources", { n: rest.length }))}</summary>
        <div class="source-cards">${rest.map(sourceCard).join("")}</div></details>`;
    }
    view.sources.innerHTML = html;
  }

  els.messages.addEventListener("click", (event) => {
    const cite = event.target.closest(".cite");
    if (cite) {
      const msg = cite.closest(".msg-bot");
      const card = msg && msg.querySelector(`.source-card[data-n="${cite.dataset.cite}"]`);
      if (card) {
        const details = card.closest("details");
        if (details) details.open = true;
        card.scrollIntoView({ behavior: "smooth", block: "nearest" });
        card.classList.add("flash");
        setTimeout(() => card.classList.remove("flash"), 1600);
      }
      return;
    }
    const text = event.target.closest(".sc-text");
    if (text) text.classList.toggle("expanded");
  });

  // ------------------------------------------------------------------ asking

  async function readEvents(response, onEvent) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let nl;
      while ((nl = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, nl).trim();
        buffer = buffer.slice(nl + 1);
        if (!line) continue;
        let ev;
        try { ev = JSON.parse(line); } catch (e) { continue; }
        onEvent(ev);
      }
    }
  }

  const ERROR_KEYS = { llm_unavailable: "error_llm", error_empty: "error_empty", summary_busy: "summary_busy",
    summary_failed: "summary_failed", summary_too_long: "summary_too_long" };
  const errorKey = (code) => ERROR_KEYS[code] || "error_generic";

  function setBusy(value) {
    busy = value;
    els.send.classList.toggle("busy", value);
    els.send.setAttribute("aria-label", value ? t("stop") : t("send"));
    els.send.title = value ? t("stop") : t("send");
    updateSendState();
  }

  function updateSendState() {
    els.send.disabled = !busy && (!els.input.value.trim() || docs.length === 0);
  }

  async function ask(question) {
    const selectedIds = docs.filter((d) => !deselected.has(d.id)).map((d) => d.id);
    if (!selectedIds.length) {
      toast(t("no_sources_selected"), true);
      setPanel(true);
      return;
    }
    els.empty.classList.add("hidden");
    addUserMessage(question);
    const view = addBotMessage();
    scrollToBottom(true);
    els.input.value = "";
    autoGrow();
    setBusy(true);

    controller = new AbortController();
    let answer = "";
    let sources = [];
    let failed = false;
    let pending = false;
    let finished = false;
    const render = () => {
      pending = false;
      if (finished) return;
      view.answer.innerHTML = renderMarkdown(answer, sources.length);
      view.answer.classList.add("caret");
      scrollToBottom(false);
    };
    const schedule = () => { if (!pending) { pending = true; requestAnimationFrame(render); } };

    try {
      const response = await api("POST", "/api/ask", {
        question,
        history: history.slice(-8),
        doc_ids: selectedIds.length === docs.length ? null : selectedIds,
        mode: els.modeFull.checked ? "full" : "normal",
      }, { raw: true, signal: controller.signal });
      await readEvents(response, (ev) => {
        if (ev.type === "status") {
          view.stage.textContent = t("stage_" + ev.stage);
        } else if (ev.type === "sources") {
          sources = ev.sources || [];
        } else if (ev.type === "delta") {
          if (!answer) view.status.classList.add("hidden");
          answer += ev.text;
          schedule();
        } else if (ev.type === "error") {
          failed = true;
          view.answer.classList.add("error");
          answer = (answer ? answer + "\n\n" : "") + t(errorKey(ev.code));
        }
      });
    } catch (err) {
      failed = true;
      if (err.name === "AbortError") {
        answer = (answer ? answer + "\n\n" : "") + t("stopped");
      } else {
        view.answer.classList.add("error");
        answer = (answer ? answer + "\n\n" : "") + (err.message || t("error_generic"));
      }
    } finally {
      finished = true;
      controller = null;
      view.status.classList.add("hidden");
      view.answer.classList.remove("caret");
      view.answer.innerHTML = renderMarkdown(answer, sources.length);
      renderSourcesBlock(view, sources, answer);
      if (answer && !view.answer.classList.contains("error")) {
        view.tools.classList.remove("hidden");
        view.copy.addEventListener("click", () => copyText(answer));
      }
      if (!failed) {
        history.push({ role: "user", content: question }, { role: "assistant", content: answer });
        history = history.slice(-12);
      }
      setBusy(false);
      scrollToBottom(false);
    }
  }

  // ------------------------------------------------------------------ document summary

  const PAGE_REF = /\((?:p\.?|pp\.?|page|ص\.?|صفحة)\s*([\d٠-٩]+)(?:\s*[-–]\s*[\d٠-٩]+)?\)/gi;

  function renderSummary(text, doc) {
    let html = renderMarkdown(text, 0);
    if (doc.ext === ".pdf") {
      // turn "(p. 3)" / "(ص 3)" into links that open the PDF on that page
      html = html.replace(PAGE_REF, (match, page) =>
        `<a class="page-ref" href="/files/${encodeURIComponent(doc.id)}#page=${toLatin(page)}" target="_blank" rel="noopener">${match}</a>`);
    }
    return html;
  }

  async function summarize(doc, refresh, view) {
    const reuse = Boolean(view);
    els.empty.classList.add("hidden");
    if (!view) {
      view = addBotMessage();
      view.root.classList.add("msg-summary");
      view.answer.insertAdjacentHTML("beforebegin", `<p class="summary-head" dir="auto"><svg class="icon"><use href="#i-summary"/></svg>
        <span class="summary-title">${escapeHtml(t("summary_title", { title: doc.title }))}</span></p><p class="summary-meta muted small" data-role="meta"></p>`);
    } else {
      view.status.classList.remove("hidden");
      view.tools.classList.add("hidden");
      view.answer.classList.remove("error");
      view.answer.innerHTML = "";
    }
    const meta = view.root.querySelector('[data-role="meta"]');
    meta.textContent = "";
    meta.classList.remove("warn");
    view.stage.textContent = t("stage_summarizing");
    // a new summary follows the end of the chat; redoing an older one keeps it in view
    const follow = !reuse;
    if (follow) scrollToBottom(true);
    else view.root.scrollIntoView({ block: "nearest" });
    setBusy(true);
    controller = new AbortController();
    let text = "";
    let cachedAt = null;
    let warning = "";
    let pending = false;
    let finished = false;
    const render = () => {
      pending = false;
      if (finished) return;
      view.answer.innerHTML = renderSummary(text, doc);
      view.answer.classList.add("caret");
      if (follow) scrollToBottom(false);
    };
    const schedule = () => { if (!pending) { pending = true; requestAnimationFrame(render); } };
    try {
      const response = await api("POST", `/api/documents/${encodeURIComponent(doc.id)}/summary`,
        { lang: window.App.LANG, refresh: !!refresh }, { raw: true, signal: controller.signal });
      await readEvents(response, (ev) => {
        if (ev.type === "status") {
          view.stage.textContent = ev.stage === "reading"
            ? t("stage_reading", { part: ev.part, parts: ev.parts }) : t("stage_" + ev.stage);
        } else if (ev.type === "summary") {
          cachedAt = ev.cached ? ev.created_at : null;
        } else if (ev.type === "warning") {
          warning = ev.code;
        } else if (ev.type === "delta") {
          if (!text) view.status.classList.add("hidden");
          text += ev.text;
          schedule();
        } else if (ev.type === "error") {
          view.answer.classList.add("error");
          text = (text ? text + "\n\n" : "") + t(errorKey(ev.code));
        }
      });
    } catch (err) {
      if (err.name === "AbortError") {
        text = (text ? text + "\n\n" : "") + t("stopped");
      } else {
        view.answer.classList.add("error");
        text = (text ? text + "\n\n" : "") + (err.message || t("error_generic"));
        if (err.code === "not_found") loadDocs();  // deleted or being re-processed meanwhile
      }
    } finally {
      finished = true;
      controller = null;
      view.status.classList.add("hidden");
      view.answer.classList.remove("caret");
      view.answer.innerHTML = renderSummary(text, doc);
      const ok = text && !view.answer.classList.contains("error");
      if (ok && warning) {
        meta.textContent = "⚠ " + t(warning);  // shown but not saved: anyone can try again
        meta.classList.add("warn");
      } else {
        meta.textContent = ok ? (cachedAt ? t("summary_cached", { date: window.App.formatDate(cachedAt) }) + " · " : "") + t("summary_note") : "";
      }
      view.tools.innerHTML = "";
      if (ok) {
        const copy = document.createElement("button");
        copy.type = "button";
        copy.className = "btn btn-ghost btn-sm";
        copy.innerHTML = `<svg class="icon"><use href="#i-copy"/></svg>${escapeHtml(t("copy"))}`;
        copy.addEventListener("click", () => copyText(text));
        view.tools.appendChild(copy);
      }
      if (IS_ADMIN || !ok || warning) {
        // employees may retry a failed or unsaved summary; replacing a saved one is for administrators
        const again = document.createElement("button");
        again.type = "button";
        again.className = "btn btn-ghost btn-sm";
        again.innerHTML = `<svg class="icon"><use href="#i-refresh"/></svg>${escapeHtml(t(ok && !warning ? "summary_refresh" : "retry"))}`;
        again.addEventListener("click", () => { if (!busy) summarize(doc, ok && !warning, view); });
        view.tools.appendChild(again);
      }
      view.tools.classList.toggle("hidden", !view.tools.children.length);
      setBusy(false);
      if (follow) scrollToBottom(false);
    }
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
    } catch (e) {
      const area = document.createElement("textarea");
      area.value = text;
      document.body.appendChild(area);
      area.select();
      document.execCommand("copy");
      area.remove();
    }
    toast(t("copied"));
  }

  const coarse = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;

  function autoGrow() {
    els.input.style.height = "auto";
    els.input.style.height = Math.min(els.input.scrollHeight, 180) + "px";
    updateSendState();
  }

  els.input.addEventListener("input", autoGrow);
  els.input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !coarse && !event.isComposing) {
      event.preventDefault();
      els.form.requestSubmit();
    }
  });
  els.form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (busy) {
      if (controller) controller.abort();
      return;
    }
    const question = els.input.value.trim();
    if (question) ask(question);
  });
  els.examples.addEventListener("click", (event) => {
    const chip = event.target.closest(".chip");
    if (chip && !busy && docs.length) ask(chip.textContent.trim());
  });
  document.getElementById("new-chat").addEventListener("click", () => {
    if (controller) controller.abort();
    history = [];
    els.messages.querySelectorAll(".msg").forEach((m) => m.remove());
    els.empty.classList.remove("hidden");
    els.input.focus();
  });

  loadDocs();
  updateSendState();
})();
