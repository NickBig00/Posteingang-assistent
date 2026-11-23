// Small progressive enhancements; every page also works without JavaScript.
(function () {
  "use strict";

  // Sidebar toggle on small screens.
  document.addEventListener("click", function (event) {
    const toggle = event.target.closest("[data-nav-toggle]");
    if (!toggle) return;
    const nav = document.getElementById(toggle.getAttribute("aria-controls"));
    const open = toggle.getAttribute("aria-expanded") !== "true";
    toggle.setAttribute("aria-expanded", String(open));
    nav.classList.toggle("hidden", !open);
  });

  // Copy buttons: <button data-copy="text"> or data-copy-target="#element-id".
  document.addEventListener("click", function (event) {
    const button = event.target.closest("[data-copy], [data-copy-target]");
    if (!button || !navigator.clipboard) return;
    const target = button.dataset.copyTarget && document.querySelector(button.dataset.copyTarget);
    const text = target ? target.innerText : button.dataset.copy;
    navigator.clipboard.writeText(text).then(function () {
      const label = button.querySelector("[data-copy-label]") || button;
      const original = label.textContent;
      label.textContent = "Kopiert ✓";
      setTimeout(function () { label.textContent = original; }, 2000);
    });
  });

  // Long-running form posts (AI steps): disable the button and show a loading state.
  document.addEventListener("submit", function (event) {
    const form = event.target;
    if (!form.matches("form[data-loading]") || event.defaultPrevented) return;
    const button = event.submitter || form.querySelector("button[type=submit], button:not([type])");
    form.setAttribute("aria-busy", "true");
    // Placeholder exactly where the result will appear (e.g. three tiles for AI images).
    if (form.id) {
      const skeleton = document.querySelector('[data-skeleton-for="' + form.id + '"]');
      if (skeleton) {
        skeleton.hidden = false;
        const old = skeleton.parentElement.querySelector("[data-skeleton-hide]");
        if (old) old.hidden = true;
      }
    }
    if (button) {
      // Keep the clicked button's name/value in the request before disabling it.
      if (button.name) {
        const hidden = document.createElement("input");
        hidden.type = "hidden";
        hidden.name = button.name;
        hidden.value = button.value;
        form.appendChild(hidden);
      }
      setTimeout(function () {
        button.disabled = true;
        button.innerHTML = '<span class="spinner" aria-hidden="true"></span>' + form.dataset.loading;
      }, 0);
    }
  });

  // Dismissible toasts.
  document.addEventListener("click", function (event) {
    const close = event.target.closest("[data-dismiss]");
    if (close) close.closest(".toast, .alert").remove();
  });

  // A link to a collapsed section (e.g. "Foto mit KI bearbeiten") opens it.
  function openTarget() {
    if (!location.hash) return;
    const target = document.getElementById(decodeURIComponent(location.hash.slice(1)));
    if (target && target.tagName === "DETAILS") target.open = true;
  }
  window.addEventListener("hashchange", openTarget);
  openTarget();

  // --- Flow page (U7): tabs per channel, choice cards, popovers, chips, uploads ---

  // Tabs: elements with data-tab="<id>" belong to the tab <id>; without JavaScript all show.
  function tabsOf(root) {
    return Array.from(root.querySelectorAll("[data-tab-target]"));
  }
  function activateTab(id, focus) {
    const tabs = document.querySelector("[data-tabs]");
    if (!tabs) return;
    const all = tabsOf(tabs);
    if (!all.some(function (t) { return t.dataset.tabTarget === id; })) id = all[0].dataset.tabTarget;
    all.forEach(function (tab) {
      const on = tab.dataset.tabTarget === id;
      tab.setAttribute("aria-selected", String(on));
      tab.tabIndex = on ? 0 : -1;
      if (on && focus) tab.focus();
    });
    document.querySelectorAll("[data-tab]").forEach(function (el) {
      el.hidden = el.dataset.tab !== id;
    });
    document.querySelectorAll("[data-hide-for]").forEach(function (el) {
      el.hidden = el.dataset.hideFor.split(" ").indexOf(id) >= 0;
    });
    document.documentElement.dataset.activeTab = id;
  }
  function tabFromHash() {
    const hash = decodeURIComponent(location.hash.slice(1));
    if (!hash) return null;
    if (document.querySelector('[data-tab-target="' + hash + '"]')) return hash;
    const target = document.getElementById(hash);
    const owner = target && target.closest("[data-tab]");
    return owner ? owner.dataset.tab : null;
  }
  function initTabs() {
    const tabs = document.querySelector("[data-tabs]");
    if (!tabs) return;
    const selected = tabs.querySelector('[aria-selected="true"]') || tabsOf(tabs)[0];
    activateTab(tabFromHash() || selected.dataset.tabTarget, false);
    const hash = decodeURIComponent(location.hash.slice(1));
    const target = hash && document.getElementById(hash);
    if (target && !target.matches("[data-tab-target]")) {
      target.scrollIntoView({ block: "center" });
      target.classList.add("flash");
      // Focus moves on to where the result is (e.g. the first new image after generating).
      const next = Array.from(
        target.querySelectorAll("button:not([disabled]), a[href], textarea, input:not([type=hidden])"),
      ).find(function (el) { return el.offsetParent !== null; });
      if (next) next.focus({ preventScroll: true });
      else if (target.tabIndex >= 0) target.focus({ preventScroll: true });
    }
    tabs.addEventListener("click", function (event) {
      const tab = event.target.closest("[data-tab-target]");
      if (!tab) return;
      event.preventDefault();
      activateTab(tab.dataset.tabTarget, false);
      history.replaceState(null, "", "#" + tab.dataset.tabTarget);
    });
    tabs.addEventListener("keydown", function (event) {
      const all = tabsOf(tabs);
      const index = all.indexOf(document.activeElement);
      if (index < 0) return;
      let next = null;
      if (event.key === "ArrowRight") next = all[(index + 1) % all.length];
      if (event.key === "ArrowLeft") next = all[(index - 1 + all.length) % all.length];
      if (event.key === "Home") next = all[0];
      if (event.key === "End") next = all[all.length - 1];
      if (!next) return;
      event.preventDefault();
      activateTab(next.dataset.tabTarget, true);
      history.replaceState(null, "", "#" + next.dataset.tabTarget);
    });
    window.addEventListener("hashchange", function () {
      const id = tabFromHash();
      if (id) activateTab(id, false);
    });
  }

  // Checklist links: jump to the place and highlight it briefly.
  document.addEventListener("click", function (event) {
    const link = event.target.closest("[data-checklist-link]");
    if (!link) return;
    const target = document.getElementById(link.getAttribute("href").slice(1));
    if (!target) return;
    event.preventDefault();
    const owner = target.closest("[data-tab]");
    if (owner) activateTab(owner.dataset.tab, false);
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    target.classList.remove("flash");
    void target.offsetWidth;
    target.classList.add("flash");
    const focusable = target.matches("a, button, input, textarea, select") ? target : target.querySelector("button, a, textarea, input");
    if (focusable) focusable.focus({ preventScroll: true });
  });

  // Choice cards show the panel of the chosen value (all panels show without JavaScript).
  function syncChoice(group) {
    const checked = group.querySelector("[data-choice]:checked");
    const scope = group.closest("[data-choice-scope]") || document;
    scope.querySelectorAll("[data-choice-panel]").forEach(function (panel) {
      panel.hidden = !checked || panel.dataset.choicePanel !== checked.value;
    });
  }
  document.addEventListener("change", function (event) {
    const group = event.target.closest("[data-choice-cards]");
    if (group) syncChoice(group);
  });

  // Popovers: close on Escape and when clicking elsewhere.
  document.addEventListener("click", function (event) {
    document.querySelectorAll("details[data-popover][open]").forEach(function (popover) {
      if (!popover.contains(event.target)) popover.open = false;
    });
  });
  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    const open = document.querySelector("details[data-popover][open]");
    if (open) {
      open.open = false;
      open.querySelector("summary").focus();
    }
  });

  // Hashtag chips: × removes the tag from the text field.
  document.addEventListener("click", function (event) {
    const remove = event.target.closest("[data-chip-remove]");
    if (!remove) return;
    const chips = remove.closest("[data-chips]");
    const value = chips.querySelector("[data-chips-value]");
    const tag = remove.dataset.chipRemove;
    value.value = value.value.split(/[\s,]+/).filter(function (t) { return t && t !== tag; }).join(" ");
    remove.closest("[data-chip]").remove();
    value.dispatchEvent(new Event("input", { bubbles: true }));
    chips.querySelector("[data-chips-input]").focus();
  });

  // "Speichern" is only active when something changed.
  function initDirtyForms() {
    document.querySelectorAll("form[data-dirty]").forEach(function (form) {
      const button = form.querySelector("[data-dirty-submit]");
      if (!button) return;
      const initial = new URLSearchParams(new FormData(form)).toString();
      button.disabled = true;
      form.addEventListener("input", function () {
        button.disabled = new URLSearchParams(new FormData(form)).toString() === initial;
      });
    });
  }

  // Uploads: submit as soon as a file is chosen or dropped.
  function initDropzones() {
    document.querySelectorAll("[data-dropzone]").forEach(function (form) {
      const input = form.querySelector("[data-dropzone-input]");
      const submit = form.querySelector("[data-dropzone-submit]");
      if (submit) submit.classList.add("sr-only");
      input.addEventListener("change", function () {
        if (input.files.length) form.requestSubmit();
      });
      ["dragenter", "dragover"].forEach(function (type) {
        form.addEventListener(type, function (event) {
          event.preventDefault();
          form.classList.add("dropzone-active");
        });
      });
      ["dragleave", "drop"].forEach(function (type) {
        form.addEventListener(type, function () { form.classList.remove("dropzone-active"); });
      });
      form.addEventListener("drop", function (event) {
        event.preventDefault();
        if (!event.dataTransfer.files.length) return;
        input.files = event.dataTransfer.files;
        form.requestSubmit();
      });
    });
  }

  // Toasts close by themselves after a few seconds (the close button stays).
  function initToasts() {
    document.querySelectorAll("[data-toast]").forEach(function (toast) {
      setTimeout(function () { toast.remove(); }, 5000);
    });
    // The toast parameter is a one-off: keep it out of reloads and bookmarks.
    if (location.search.indexOf("toast=") >= 0) {
      const url = new URL(location.href);
      url.searchParams.delete("toast");
      history.replaceState(null, "", url.pathname + url.search + url.hash);
    }
  }

  // Live preview: dim the preview while a new image is rendered.
  document.addEventListener("htmx:beforeRequest", function (event) {
    if (event.target.matches("[data-live-form]")) document.body.classList.add("is-updating");
  });
  document.addEventListener("htmx:afterSettle", function () {
    document.querySelectorAll("img[data-post-image]").forEach(function (img) {
      if (img.complete) document.body.classList.remove("is-updating");
      else img.addEventListener("load", function () { document.body.classList.remove("is-updating"); }, { once: true });
    });
  });

  // A link into a hidden choice panel (e.g. #ki-bild after "Vorschlag von der KI") selects it.
  function openChoiceOfHash() {
    const hash = decodeURIComponent(location.hash.slice(1));
    const target = hash && document.getElementById(hash);
    const panel = target && target.closest("[data-choice-panel]");
    if (!panel) return;
    const scope = panel.closest("[data-choice-scope]") || document;
    const radio = scope.querySelector('[data-choice][value="' + panel.dataset.choicePanel + '"]');
    if (radio) radio.checked = true;
  }

  function initFlow() {
    initToasts();
    openChoiceOfHash();
    document.querySelectorAll("[data-choice-cards]").forEach(syncChoice);
    initTabs();
    initDirtyForms();
    initDropzones();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initFlow);
  else initFlow();
})();
