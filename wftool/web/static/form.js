/* WFTOOL request forms: sections shown on a condition, repeatable rows, showing a secret.
 * Hidden sections are disabled, so their fields are neither checked nor sent (the server
 * ignores them too). Works without this script as well: then every section is visible. */
(function () {
  "use strict";
  var form = document.querySelector("form.request-form, form.task-form");
  if (!form) return;

  function valueOf(id) {
    var els = form.querySelectorAll('[name="f.' + id + '"]');
    if (!els.length) return form.dataset["known_" + id];
    var first = els[0];
    if (first.type === "checkbox" && els.length === 1 && first.value === "on") return first.checked;
    if (first.type === "checkbox") {
      return Array.prototype.filter.call(els, function (x) { return x.checked; }).map(function (x) { return x.value; });
    }
    return first.value;
  }

  function truthy(target) {
    return target === true || ["1", "true", "yes", "on", "ja"].indexOf(String(target).toLowerCase()) >= 0;
  }

  function holds(cond) {
    if (!cond || !cond.field) return true;
    var v = valueOf(cond.field);
    var empty = v === undefined || v === null || v === "" || v === false || (Array.isArray(v) && !v.length);
    switch (cond.op) {
      case "is_set": return !empty;
      case "is_empty": return empty;
      case "contains": case "not_contains": {
        var has = Array.isArray(v) ? v.map(String).indexOf(String(cond.value)) >= 0
          : String(v || "").toLowerCase().indexOf(String(cond.value).toLowerCase()) >= 0;
        return cond.op === "contains" ? has : !has;
      }
      case "any_needs_approval": return true; // decided on the server
      default: {
        var eq = typeof v === "boolean" ? v === truthy(cond.value)
          : String(v == null ? "" : v).trim().toLowerCase() === String(cond.value == null ? "" : cond.value).trim().toLowerCase();
        return cond.op === "not_equals" ? !eq : eq;
      }
    }
  }

  function update() {
    form.querySelectorAll("fieldset[data-show-when]").forEach(function (fs) {
      var cond = null;
      try { cond = JSON.parse(fs.getAttribute("data-show-when")); } catch (e) { cond = null; }
      var show = holds(cond);
      fs.hidden = !show;
      fs.disabled = !show;
    });
    form.querySelectorAll("[data-rows]").forEach(syncRows);
  }

  function syncRows(box) {
    var rows = box.querySelectorAll(":scope > [data-row]");
    var min = parseInt(box.dataset.min || "0", 10), max = parseInt(box.dataset.max || "50", 10);
    rows.forEach(function (row, i) {
      var no = row.querySelector("[data-row-no]");
      if (no) no.textContent = String(i + 1);
      var rm = row.querySelector("[data-remove-row]");
      if (rm) rm.hidden = rows.length <= Math.max(min, 1);
    });
    var add = form.querySelector('[data-add-row="' + box.dataset.rows + '"]');
    if (add) add.hidden = rows.length >= max;
  }

  var counter = 1000;
  form.addEventListener("click", function (e) {
    var add = e.target.closest("[data-add-row]");
    if (add) {
      var sid = add.getAttribute("data-add-row");
      var tpl = form.querySelector('template[data-row-template="' + sid + '"]');
      var box = form.querySelector('[data-rows="' + sid + '"]');
      var n = String(counter++);
      var html = tpl.innerHTML.split("__N__").join(n).split("__N1__").join("");
      var holder = document.createElement("div");
      holder.innerHTML = html;
      var row = holder.firstElementChild;
      box.appendChild(row);
      syncRows(box);
      var first = row.querySelector("input, select, textarea");
      if (first) first.focus();
      return;
    }
    var rm = e.target.closest("[data-remove-row]");
    if (rm) {
      var r = rm.closest("[data-row]"), parent = r.parentElement;
      r.remove();
      syncRows(parent);
      return;
    }
    var reveal = e.target.closest("[data-reveal]");
    if (reveal) {
      var input = document.getElementById(reveal.getAttribute("data-reveal"));
      if (input) {
        var show = input.type === "password";
        input.type = show ? "text" : "password";
        reveal.setAttribute("aria-pressed", show ? "true" : "false");
      }
    }
  });
  form.addEventListener("change", update);
  form.addEventListener("input", function (e) { if (e.target.type !== "password") update(); });
  update();
})();
