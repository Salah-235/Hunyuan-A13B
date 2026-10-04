/* Shared helpers: translations, API calls with CSRF, toasts, dialogs, PWA. */
(function () {
  "use strict";

  const I18N = JSON.parse(document.getElementById("i18n").textContent || "{}");
  const CSRF = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
  const LANG = document.documentElement.lang || "ar";

  function t(key, vars) {
    let text = I18N[key] || key;
    if (vars) {
      Object.keys(vars).forEach((k) => { text = text.split("{" + k + "}").join(vars[k]); });
    }
    return text;
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  async function api(method, url, body, options) {
    const opts = Object.assign({ method, headers: { "X-CSRF-Token": CSRF }, credentials: "same-origin" }, options || {});
    if (body instanceof FormData) {
      opts.body = body;
    } else if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    let response;
    try {
      response = await fetch(url, opts);
    } catch (err) {
      if (err && err.name === "AbortError") throw err;
      throw new ApiError(t("error_network"), "network");
    }
    if (response.status === 401) {
      window.location.href = "/login?next=" + encodeURIComponent(window.location.pathname);
      throw new ApiError(t("session_expired"), "auth");
    }
    if (opts.raw) {
      if (!response.ok) throw await toError(response);
      return response;
    }
    if (!response.ok) throw await toError(response);
    return response.json();
  }

  async function toError(response) {
    let data = {};
    try { data = await response.json(); } catch (e) { /* not json */ }
    return new ApiError(data.message || t(data.error || "error_generic"), data.error || "http_" + response.status);
  }

  class ApiError extends Error {
    constructor(message, code) { super(message); this.code = code; }
  }

  let toastTimer;
  function toast(message, isError) {
    let el = document.querySelector(".toast");
    if (!el) {
      el = document.createElement("div");
      el.className = "toast";
      el.setAttribute("role", "status");
      document.body.appendChild(el);
    }
    el.textContent = message;
    el.classList.toggle("error", !!isError);
    requestAnimationFrame(() => el.classList.add("show"));
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("show"), isError ? 5000 : 2600);
  }

  function formatDate(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    const locale = LANG === "ar" ? "ar-DZ-u-nu-latn" : "fr-FR";
    try {
      return d.toLocaleString(locale, { dateStyle: "medium", timeStyle: "short" });
    } catch (e) {
      return d.toLocaleString();
    }
  }

  function showMsg(el, text, ok) {
    if (!el) return;
    el.textContent = text;
    el.className = "alert " + (ok ? "alert-success" : "alert-error");
  }

  // ---- dialogs
  document.addEventListener("click", (event) => {
    const opener = event.target.closest("[data-open-dialog]");
    if (opener) {
      const dialog = document.getElementById(opener.dataset.openDialog);
      if (dialog) {
        const menu = opener.closest("details");
        if (menu) menu.open = false;
        const msg = dialog.querySelector('[data-role="msg"]');
        if (msg) msg.className = "alert hidden";
        dialog.querySelector("form") && dialog.querySelector("form").reset();
        dialog.showModal();
      }
    }
    if (event.target.closest("[data-close-dialog]")) {
      event.target.closest("dialog").close();
    }
    // close the user menu when clicking elsewhere
    document.querySelectorAll("details.user-menu[open]").forEach((d) => {
      if (!d.contains(event.target)) d.open = false;
    });
  });

  // ---- change own password
  const pwForm = document.getElementById("pw-form");
  if (pwForm) {
    pwForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const data = Object.fromEntries(new FormData(pwForm));
      const msg = pwForm.querySelector('[data-role="msg"]');
      if ((data.new || "").length < 8) return showMsg(msg, t("password_min"));
      if (data.new !== data.confirm) return showMsg(msg, t("passwords_mismatch"));
      try {
        await api("POST", "/api/account/password", data);
        pwForm.closest("dialog").close();
        toast(t("password_changed"));
      } catch (err) {
        showMsg(msg, err.message);
      }
    });
  }

  // ---- PWA: service worker + install button
  if ("serviceWorker" in navigator && window.isSecureContext) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
    });
  }
  let installPrompt = null;
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    installPrompt = event;
    const btn = document.getElementById("install-app");
    if (btn) btn.classList.remove("hidden");
  });
  const installBtn = document.getElementById("install-app");
  if (installBtn) {
    installBtn.addEventListener("click", async () => {
      if (!installPrompt) return;
      installPrompt.prompt();
      await installPrompt.userChoice;
      installPrompt = null;
      installBtn.classList.add("hidden");
    });
  }

  window.App = { t, api, ApiError, escapeHtml, toast, formatDate, showMsg, LANG, CSRF };
})();
