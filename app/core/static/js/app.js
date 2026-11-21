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
})();
