/* Admin page: documents, users, activity log, system status. */
(function () {
  "use strict";
  const { t, api, escapeHtml, toast, formatDate, showMsg } = window.App;

  // ------------------------------------------------------------------ tabs
  const tabs = document.querySelectorAll(".tab");
  const loaders = {};
  function showTab(name) {
    tabs.forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === name));
    document.querySelectorAll(".tab-panel").forEach((p) => { p.hidden = p.id !== "tab-" + name; });
    if (loaders[name]) loaders[name]();
    try { history.replaceState(null, "", "#" + name); } catch (e) { /* ignore */ }
  }
  tabs.forEach((tab) => tab.addEventListener("click", () => showTab(tab.dataset.tab)));

  // ------------------------------------------------------------------ documents
  const docList = document.getElementById("doc-list");
  const docFilter = document.getElementById("doc-filter");
  const docCount = document.getElementById("doc-count");
  const categoryList = document.getElementById("category-list");
  const dropzone = document.getElementById("dropzone");
  const fileInput = document.getElementById("file-input");
  const uploadStatus = document.getElementById("upload-status");
  const uploadCategory = document.getElementById("upload-category");
  let documents = [];
  let pollTimer = null;

  function sizeText(bytes) {
    if (bytes > 1048576) return (bytes / 1048576).toFixed(1) + " MB";
    return Math.max(1, Math.round(bytes / 1024)) + " KB";
  }

  function problemText(codes) {
    if (!codes) return "";
    return codes.split(",").filter(Boolean).map((code) => { const key = I18N_KEY(code); return key ? t(key) : code; }).join(" ");
  }
  function I18N_KEY(code) {
    const map = {
      scanned_pdf: "warn_scanned_pdf", ocr: "warn_ocr", ocr_failed: "warn_ocr_failed", ocr_partial: "warn_ocr_partial",
      processing_failed: "err_processing_failed",
      embedding_failed: "warn_embedding_failed", no_text: "err_no_text",
      encrypted_pdf: "err_encrypted_pdf", unsupported_type: "err_unsupported_type",
    };
    return map[code];
  }

  async function loadDocuments() {
    try {
      documents = (await api("GET", "/api/documents")).documents;
    } catch (err) {
      docList.innerHTML = `<p class="muted pad">${escapeHtml(err.message)}</p>`;
      return;
    }
    renderDocuments();
    const cats = [...new Set(documents.map((d) => d.category).filter(Boolean))].sort();
    categoryList.innerHTML = cats.map((c) => `<option value="${escapeHtml(c)}">`).join("");
    clearTimeout(pollTimer);
    if (documents.some((d) => d.status === "pending" || d.status === "processing")) {
      pollTimer = setTimeout(loadDocuments, 2500);
    }
  }
  loaders.documents = loadDocuments;

  function renderDocuments() {
    const q = (docFilter.value || "").trim().toLowerCase();
    const rows = documents.filter((d) => !q || (d.title + " " + d.category + " " + d.filename).toLowerCase().includes(q));
    docCount.textContent = documents.length;
    if (!documents.length) {
      docList.innerHTML = `<p class="muted pad">${escapeHtml(t("docs_empty"))}</p>`;
      return;
    }
    docList.innerHTML = rows.map((d) => {
      const ext = d.ext.replace(".", "");
      const unit = d.ext === ".xlsx" ? t("doc_sheets") : t("doc_pages");
      const meta = [
        d.category ? `<span class="badge">${escapeHtml(d.category)}</span>` : "",
        `<span class="badge badge-${d.status}">${escapeHtml(t("status_" + d.status))}</span>`,
        d.embedded ? `<span class="badge badge-admin">${escapeHtml(t("semantic_ready"))}</span>` : "",
        d.pages ? `<span>${d.pages} ${escapeHtml(unit)}</span>` : "",
        d.chunks ? `<span>${d.chunks} ${escapeHtml(t("doc_chunks"))}</span>` : "",
        `<span>${sizeText(d.size)}</span>`,
        `<span>${escapeHtml(formatDate(d.created_at))}</span>`,
      ].filter(Boolean).join("");
      return `<div class="doc-row" data-id="${escapeHtml(d.id)}">
        <div class="doc-main">
          <span class="file-tag ${escapeHtml(ext)}">${escapeHtml(ext.toUpperCase())}</span>
          <div class="doc-info">
            <div class="doc-title">${escapeHtml(d.title)}</div>
            <div class="doc-sub">${meta}</div>
            ${d.warning ? `<div class="doc-warning">⚠ ${escapeHtml(problemText(d.warning))}</div>` : ""}
            ${d.status === "error" ? `<div class="doc-error">${escapeHtml(problemText(d.error))}</div>` : ""}
          </div>
        </div>
        <div class="doc-actions">
          <a class="icon-btn" href="/files/${encodeURIComponent(d.id)}" target="_blank" rel="noopener" title="${escapeHtml(t("open"))}"><svg class="icon"><use href="#i-external"/></svg></a>
          <button class="icon-btn" data-action="edit" title="${escapeHtml(t("edit"))}"><svg class="icon"><use href="#i-edit"/></svg></button>
          <button class="icon-btn" data-action="reindex" title="${escapeHtml(t("reindex"))}"><svg class="icon"><use href="#i-refresh"/></svg></button>
          <button class="icon-btn danger" data-action="delete" title="${escapeHtml(t("delete"))}"><svg class="icon"><use href="#i-trash"/></svg></button>
        </div>
      </div>`;
    }).join("");
  }
  docFilter.addEventListener("input", renderDocuments);

  const docDialog = document.getElementById("doc-dialog");
  const docForm = document.getElementById("doc-form");
  let editingId = null;

  docList.addEventListener("click", async (event) => {
    const btn = event.target.closest("[data-action]");
    if (!btn) return;
    const id = btn.closest(".doc-row").dataset.id;
    const doc = documents.find((d) => d.id === id);
    if (!doc) return;
    try {
      if (btn.dataset.action === "delete") {
        if (!confirm(t("confirm_delete_doc", { title: doc.title }))) return;
        await api("DELETE", `/api/documents/${id}`);
        loadDocuments();
      } else if (btn.dataset.action === "reindex") {
        await api("POST", `/api/documents/${id}/reindex`);
        loadDocuments();
      } else if (btn.dataset.action === "edit") {
        editingId = id;
        docForm.title.value = doc.title;
        docForm.category.value = doc.category;
        docDialog.showModal();
      }
    } catch (err) {
      toast(err.message, true);
    }
  });

  docForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api("PATCH", `/api/documents/${editingId}`, {
        title: docForm.title.value, category: docForm.category.value,
      });
      docDialog.close();
      loadDocuments();
    } catch (err) {
      toast(err.message, true);
    }
  });

  // uploads: one request per file so each shows its own progress
  function uploadFile(file, category) {
    return new Promise((resolve) => {
      const row = document.createElement("div");
      row.className = "up-item";
      row.innerHTML = `<span class="name"></span><span class="bar"><i></i></span><span class="pct small muted">0%</span>`;
      row.querySelector(".name").textContent = file.name;
      uploadStatus.appendChild(row);
      const bar = row.querySelector(".bar i");
      const pct = row.querySelector(".pct");
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/documents");
      xhr.setRequestHeader("X-CSRF-Token", window.App.CSRF);
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) {
          const p = Math.round((e.loaded / e.total) * 100);
          bar.style.width = p + "%";
          pct.textContent = p + "%";
        }
      };
      xhr.onload = () => {
        let data = {};
        try { data = JSON.parse(xhr.responseText); } catch (e) { /* ignore */ }
        if (xhr.status === 200 && !(data.rejected || []).length) {
          bar.style.width = "100%";
          pct.textContent = "✓";
          setTimeout(() => row.remove(), 1500);
        } else {
          const reason = (data.rejected && data.rejected[0] && data.rejected[0].reason) || data.message || t("error_generic");
          pct.textContent = "✕";
          row.title = reason;
          toast(t("upload_rejected", { name: file.name, reason }), true);
          setTimeout(() => row.remove(), 6000);
        }
        resolve();
      };
      xhr.onerror = () => {
        pct.textContent = "✕";
        toast(t("error_network"), true);
        resolve();
      };
      const form = new FormData();
      form.append("files", file);
      form.append("category", category);
      xhr.send(form);
    });
  }

  async function uploadFiles(files) {
    const list = [...files];
    if (!list.length) return;
    const category = uploadCategory.value.trim();
    for (const file of list) {
      await uploadFile(file, category);
      loadDocuments();
    }
    toast(t("upload_done"));
  }

  fileInput.addEventListener("change", () => { uploadFiles(fileInput.files); fileInput.value = ""; });
  ["dragenter", "dragover"].forEach((name) => dropzone.addEventListener(name, (e) => {
    e.preventDefault(); dropzone.classList.add("drag");
  }));
  ["dragleave", "drop"].forEach((name) => dropzone.addEventListener(name, (e) => {
    e.preventDefault(); dropzone.classList.remove("drag");
  }));
  dropzone.addEventListener("drop", (e) => uploadFiles(e.dataTransfer.files));

  // ------------------------------------------------------------------ users
  const userList = document.getElementById("user-list");
  const userForm = document.getElementById("user-form");
  const userMsg = document.getElementById("user-msg");
  const resetDialog = document.getElementById("reset-dialog");
  const resetForm = document.getElementById("reset-form");
  let users = [];
  let me = null;
  let resetId = null;

  async function loadUsers() {
    try {
      const data = await api("GET", "/api/users");
      users = data.users;
      me = data.me;
    } catch (err) {
      userList.innerHTML = `<p class="muted pad">${escapeHtml(err.message)}</p>`;
      return;
    }
    userList.innerHTML = users.map((u) => {
      const self = u.id === me;
      return `<div class="user-row ${u.active ? "" : "inactive"}" data-id="${u.id}">
        <span class="avatar">${escapeHtml((u.display_name || u.username).slice(0, 1).toUpperCase())}</span>
        <div class="user-info">
          <div><strong>${escapeHtml(u.display_name)}</strong> ${self ? `<span class="muted">${escapeHtml(t("you"))}</span>` : ""}</div>
          <div class="muted"><span dir="ltr">@${escapeHtml(u.username)}</span> · ${escapeHtml(t("last_login"))}: ${escapeHtml(u.last_login ? formatDate(u.last_login) : t("never"))}</div>
        </div>
        <span class="badge ${u.role === "admin" ? "badge-admin" : ""}">${escapeHtml(t(u.role === "admin" ? "role_admin" : "role_user"))}</span>
        <span class="badge ${u.active ? "badge-ready" : "badge-error"}">${escapeHtml(t(u.active ? "active" : "disabled"))}</span>
        <div class="doc-actions">
          <button class="btn btn-ghost btn-sm" data-action="password">${escapeHtml(t("reset_password"))}</button>
          ${self ? "" : `
          <button class="btn btn-ghost btn-sm" data-action="role">${escapeHtml(t(u.role === "admin" ? "make_user" : "make_admin"))}</button>
          <button class="btn btn-ghost btn-sm" data-action="toggle">${escapeHtml(t(u.active ? "disable" : "enable"))}</button>
          <button class="icon-btn danger" data-action="delete" title="${escapeHtml(t("delete"))}"><svg class="icon"><use href="#i-trash"/></svg></button>`}
        </div>
      </div>`;
    }).join("");
  }
  loaders.users = loadUsers;

  userForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(userForm));
    if ((data.password || "").length < 8) return showMsg(userMsg, t("password_min"));
    try {
      await api("POST", "/api/users", data);
      userForm.reset();
      showMsg(userMsg, t("user_created"), true);
      loadUsers();
    } catch (err) {
      showMsg(userMsg, err.message);
    }
  });

  userList.addEventListener("click", async (event) => {
    const btn = event.target.closest("[data-action]");
    if (!btn) return;
    const id = Number(btn.closest(".user-row").dataset.id);
    const u = users.find((x) => x.id === id);
    try {
      if (btn.dataset.action === "password") {
        resetId = id;
        resetForm.reset();
        resetForm.querySelector('[data-role="title"]').textContent = t("new_password_for", { name: u.display_name });
        resetForm.querySelector('[data-role="msg"]').className = "alert hidden";
        resetDialog.showModal();
        return;
      }
      if (btn.dataset.action === "role") {
        await api("PATCH", `/api/users/${id}`, { role: u.role === "admin" ? "user" : "admin" });
      } else if (btn.dataset.action === "toggle") {
        await api("PATCH", `/api/users/${id}`, { active: !u.active });
      } else if (btn.dataset.action === "delete") {
        if (!confirm(t("confirm_delete_user", { name: u.display_name }))) return;
        await api("DELETE", `/api/users/${id}`);
      }
      loadUsers();
    } catch (err) {
      toast(err.message, true);
    }
  });

  resetForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const password = resetForm.password.value;
    const msg = resetForm.querySelector('[data-role="msg"]');
    if (password.length < 8) return showMsg(msg, t("password_min"));
    try {
      await api("PATCH", `/api/users/${resetId}`, { password });
      resetDialog.close();
      toast(t("password_changed"));
    } catch (err) {
      showMsg(msg, err.message);
    }
  });

  // ------------------------------------------------------------------ activity
  const auditList = document.getElementById("audit-list");
  const auditFilter = document.getElementById("audit-filter");
  let events = [];

  async function loadAudit() {
    try {
      events = (await api("GET", "/api/audit")).events;
    } catch (err) {
      auditList.innerHTML = `<p class="muted pad">${escapeHtml(err.message)}</p>`;
      return;
    }
    renderAudit();
  }
  loaders.activity = loadAudit;

  function renderAudit() {
    const q = (auditFilter.value || "").trim().toLowerCase();
    const rows = events.filter((e) => !q || [e.username, e.detail, t("act_" + e.action), e.ip].join(" ").toLowerCase().includes(q));
    if (!rows.length) {
      auditList.innerHTML = `<p class="muted pad">${escapeHtml(t("activity_empty"))}</p>`;
      return;
    }
    auditList.innerHTML = `<table class="table"><thead><tr>
      <th>${escapeHtml(t("time"))}</th><th>${escapeHtml(t("user"))}</th><th>${escapeHtml(t("action"))}</th>
      <th>${escapeHtml(t("details"))}</th><th>${escapeHtml(t("ip"))}</th></tr></thead><tbody>` +
      rows.map((e) => `<tr>
        <td class="nowrap">${escapeHtml(formatDate(e.ts))}</td>
        <td class="nowrap" dir="ltr">${escapeHtml(e.username)}</td>
        <td class="nowrap act act-${escapeHtml(e.action)}">${escapeHtml(t("act_" + e.action))}</td>
        <td class="detail">${escapeHtml(e.detail)}</td>
        <td class="nowrap" dir="ltr">${escapeHtml(e.ip)}</td></tr>`).join("") + "</tbody></table>";
  }
  auditFilter.addEventListener("input", renderAudit);

  // ------------------------------------------------------------------ system
  async function loadSystem() {
    let info;
    try {
      info = await api("GET", "/api/system");
    } catch (err) {
      toast(err.message, true);
      return;
    }
    const onOff = (v) => `<span class="${v ? "ok" : "muted"}">${escapeHtml(t(v ? "feature_on" : "feature_off"))}</span>`;
    document.getElementById("stats").innerHTML = [
      [info.documents, "stats_documents"], [info.chunks, "stats_chunks"], [info.users, "stats_users"],
    ].map(([n, key]) => `<div class="stat"><b>${n}</b><span>${escapeHtml(t(key))}</span></div>`).join("");
    document.getElementById("system-info").innerHTML = `
      <dt>${escapeHtml(t("llm_server"))}</dt><dd dir="ltr">${escapeHtml(info.llm_base_url)}</dd>
      <dt>${escapeHtml(t("model"))}</dt><dd dir="ltr">${escapeHtml(info.llm_model)}</dd>
      <dt>${escapeHtml(t("thinking_mode"))}</dt><dd>${onOff(info.thinking)}</dd>
      <dt>${escapeHtml(t("query_rewrite"))}</dt><dd>${onOff(info.query_rewrite)}</dd>
      <dt>${escapeHtml(t("embeddings"))}</dt><dd>${info.embedding_model ? `<span class="ok" dir="ltr">${escapeHtml(info.embedding_model)}</span>` : onOff(false)}</dd>
      <dt>${escapeHtml(t("ocr_engine"))}</dt><dd>${info.ocr_engine === "tesseract" ? `<span class="ok">${escapeHtml(t("ocr_tesseract"))}</span>`
        : info.ocr_engine === "vision" ? `<span class="ok">${escapeHtml(t("ocr_vision", { model: info.ocr_model || "" }))}</span>` : onOff(false)}</dd>`;
  }
  loaders.system = loadSystem;

  document.getElementById("check-connection").addEventListener("click", async () => {
    const out = document.getElementById("check-result");
    out.className = "small muted";
    out.textContent = t("loading");
    try {
      const res = await api("POST", "/api/system/check");
      out.className = "small " + (res.ok ? "ok" : "fail");
      out.textContent = res.ok ? t("connection_ok") : t("connection_failed", { detail: res.detail || "" });
    } catch (err) {
      out.className = "small fail";
      out.textContent = err.message;
    }
  });

  const initial = (location.hash || "").replace("#", "");
  showTab(["documents", "users", "activity", "system"].includes(initial) ? initial : "documents");
})();
