/* WFTOOL: small helpers for every page (no inline scripts, CSP default-src 'self'). */
(function () {
  "use strict";
  // Buttons and links with data-confirm ask first.
  document.addEventListener("click", function (e) {
    var el = e.target.closest ? e.target.closest("[data-confirm]") : null;
    if (el && !window.confirm(el.getAttribute("data-confirm"))) { e.preventDefault(); e.stopImmediatePropagation(); }
  }, true);
})();
