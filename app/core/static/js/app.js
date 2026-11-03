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

  // Dismissible toasts.
  document.addEventListener("click", function (event) {
    const close = event.target.closest("[data-dismiss]");
    if (close) close.closest(".toast, .alert").remove();
  });
})();
