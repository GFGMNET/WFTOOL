/* WFTOOL form designer and workflow builder.
 *
 * The page carries the draft and the master data as JSON (#builder-data). This
 * script keeps the draft in `def`, draws the designer from it, mirrors it into
 * the form's JSON box (#definition) on every change and asks the server to
 * check it (no save) a moment after each change. Saving is a normal form post.
 * No libraries: drag and drop uses HTML5 drag events, and every move can also
 * be done with the up/down buttons from the keyboard.
 */
(function () {
  "use strict";

  const dataEl = document.getElementById("builder-data");
  const root = document.getElementById("builder");
  if (!dataEl || !root) return;
  const D = JSON.parse(dataEl.textContent);
  const LANG = D.lang === "en" ? "en" : "de";
  const T = (s) => (D.strings && D.strings[s]) || s;
  const fmt = (tpl, args) => tpl.replace(/\{(\w+)\}/g, (m, k) => (k in args ? String(args[k]) : m));

  const form = document.getElementById("builder-form");
  const jsonBox = document.getElementById("definition");
  const tabInput = document.getElementById("builder-tab");
  const unsavedStamp = document.getElementById("unsaved");
  const checkStamp = document.getElementById("check-stamp");
  const heading = document.getElementById("builder-heading");
  const csrf = form.querySelector("input[name=_csrf]").value;

  // ------------------------------------------------------------- vocabulary

  const FIELD_TYPES = ["text", "longtext", "number", "date", "checkbox", "dropdown", "multichoice", "email",
    "person", "secret", "signature", "heading", "section"];
  const TYPE_NAME = {
    text: "Text", longtext: "Long text", number: "Number", date: "Date", checkbox: "Checkbox",
    dropdown: "Dropdown", multichoice: "Multiple choice", email: "Email", person: "Person", secret: "Secret",
    signature: "Signature", heading: "Heading", section: "Section",
  };
  const TYPE_ICON = {
    text: "Aa", longtext: "¶", number: "12", date: "31", checkbox: "✓", dropdown: "▾", multichoice: "☰",
    email: "@", person: "P", secret: "•••", signature: "✍", heading: "H", section: "§",
  };
  const NEW_LABEL = {
    text: ["Text", "Text"], longtext: ["Bemerkungen", "Notes"], number: ["Anzahl", "Quantity"],
    date: ["Datum", "Date"], checkbox: ["Ja/Nein", "Yes/no"], dropdown: ["Auswahl", "Choice"],
    multichoice: ["Mehrfachauswahl", "Choices"], email: ["E-Mail", "Email"], person: ["Person", "Person"],
    secret: ["PIN", "PIN"], signature: ["Unterschrift", "Signature"], heading: ["Überschrift", "Heading"],
    section: ["Abschnitt", "Section"],
  };
  const VALUE_TYPES = FIELD_TYPES.filter((t) => t !== "heading" && t !== "section");
  const LIST_TYPES = ["dropdown", "multichoice"];

  const STEP_TYPES = ["fill", "approve", "fill_approve", "approve_per_item", "sign", "confirm", "notify"];
  const STEP_NAME = {
    fill: ["Formular ausfüllen", "Fill form"], approve: ["Genehmigen", "Approve"],
    fill_approve: ["Ausfüllen und genehmigen", "Fill and approve"],
    approve_per_item: ["Freigabe je Eintrag", "Approve per item"], sign: ["Unterschreiben", "Sign"],
    confirm: ["Bestätigen", "Confirm"], notify: ["Benachrichtigen", "Notify"],
  };
  const STEP_HINT = {
    fill: "One person fills in the fields of this step.",
    approve: "One person approves, or rejects with a reason.",
    fill_approve: "One person fills in fields and approves on the same page: one email instead of two.",
    approve_per_item: "One approval for each chosen list entry that needs approval, from that entry's approver.",
    sign: "One person reviews the document and signs on screen, or on paper if allowed.",
    confirm: "A person or group presses one button to continue; for a group, the first one counts.",
    notify: "Sends an email, optionally with the PDF; the request continues at once.",
  };
  const FILL_STEPS = ["fill", "fill_approve"];
  const REJECT_STEPS = ["approve", "fill_approve", "approve_per_item", "sign"];
  const WHO_FOR = {
    fill: ["person", "field", "starter", "manager_of"],
    approve: ["person", "field", "manager_of"],
    fill_approve: ["person", "field", "manager_of"],
    approve_per_item: ["item_approver"],
    sign: ["field", "person", "starter", "manager_of"],
    confirm: ["group", "person", "field", "starter", "manager_of"],
    notify: ["group", "person", "field", "starter", "manager_of"],
  };
  const WHO_NAME = {
    person: "A fixed person", group: "A group from AD", field: "A person or email field of the form",
    starter: "The person who started the request", manager_of: "The manager of …",
    item_approver: "The approver of each item",
  };
  const OP_NAME = {
    equals: "is", not_equals: "is not", contains: "contains", not_contains: "does not contain",
    is_set: "is filled in", is_empty: "is empty", any_needs_approval: "contains an item that needs approval",
  };
  const VALUE_OPS = ["equals", "not_equals", "contains", "not_contains"];

  // ------------------------------------------------------------------ state

  let def = D.definition;
  const ui = { tab: "form", open: new Set(), stage: "start", pvLang: LANG, dirty: false, focusEmail: null };
  const pv = { values: {}, rows: {} }; // preview inputs, kept across redraws
  let problems = { wf: [], byObj: new Map() };
  const uids = new WeakMap();
  const autoId = new WeakSet(); // fields/steps whose id still follows their label
  const prevId = new WeakMap(); // id of a field when its id box got the focus
  let uidCounter = 0;
  const uid = (o) => { if (!uids.has(o)) uids.set(o, "u" + (++uidCounter)); return uids.get(o); };

  function lt(label, lang) {
    if (label && typeof label === "object") return label[lang || LANG] || label.de || label.en || "";
    return label || "";
  }

  function slug(text, taken, fallback) {
    let s = String(text || "").toLowerCase()
      .replace(/ä/g, "ae").replace(/ö/g, "oe").replace(/ü/g, "ue").replace(/ß/g, "ss")
      .normalize("NFKD").replace(/[̀-ͯ]/g, "")
      .replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
    if (!/^[a-z]/.test(s)) s = (fallback || "f") + (s ? "_" + s : "");
    s = s.slice(0, 36);
    let out = s, n = 2;
    while (taken.has(out)) out = s + "_" + n++;
    return out;
  }

  const fieldIds = (except) => new Set(def.fields.filter((f) => f !== except).map((f) => f.id));
  const stepIds = (except) => new Set(def.steps.filter((s) => s !== except).map((s) => s.id));
  const byId = (id) => def.fields.find((f) => f.id === id);
  const sectionOf = (f) => (f.section ? def.fields.find((s) => s.type === "section" && s.id === f.section) : null);
  const isRepeatMember = (f) => { const s = sectionOf(f); return !!(s && s.repeat); };
  const members = (sec) => def.fields.filter((f) => f.section === sec.id);
  const whoList = (step) => (step.who == null ? [] : Array.isArray(step.who) ? step.who : [step.who]);

  function shape() {
    if (!def || typeof def !== "object") def = {};
    if (!Array.isArray(def.fields)) def.fields = [];
    if (!Array.isArray(def.steps)) def.steps = [];
    def.fields = def.fields.filter((f) => f && typeof f === "object");
    def.steps = def.steps.filter((s) => s && typeof s === "object");
    normalizeOrder();
  }

  // --------------------------------------------------- field tree and order

  function tree() {
    const tops = [], kids = new Map();
    def.fields.forEach((f) => { if (f.type === "section") kids.set(f.id, []); });
    def.fields.forEach((f) => {
      if (f.type !== "section" && f.section && kids.has(f.section)) kids.get(f.section).push(f);
      else tops.push(f);
    });
    return { tops, kids };
  }

  function flatten(t) {
    const out = [];
    t.tops.forEach((f) => { out.push(f); if (f.type === "section") (t.kids.get(f.id) || []).forEach((k) => out.push(k)); });
    def.fields = out;
  }

  function normalizeOrder() { flatten(tree()); }

  // Which step fills a field: null = at the start.
  function ownerOf(f) {
    if (f.type === "section") {
      const m = members(f).find((x) => x.type !== "heading");
      return m ? ownerOf(m) : null;
    }
    const i = def.steps.findIndex((s) => (s.fields || []).includes(f.id));
    return i < 0 ? null : i;
  }

  function setOwner(f, index) {
    const targets = f.type === "section" ? members(f).filter((m) => m.type !== "heading") : [f];
    targets.forEach((t) => {
      def.steps.forEach((s) => {
        if (s.fields) { s.fields = s.fields.filter((id) => id !== t.id); if (!s.fields.length) delete s.fields; }
      });
      if (index != null && def.steps[index]) (def.steps[index].fields = def.steps[index].fields || []).push(t.id);
    });
  }

  function moveField(f, container, index) {
    if (container && (f.type === "section" || f.type === "signature")) return false;
    const secOwner = container && byId(container) ? ownerOf(byId(container)) : null;
    const t = tree();
    const from = f.section && t.kids.has(f.section) ? t.kids.get(f.section) : t.tops;
    from.splice(from.indexOf(f), 1);
    if (container) {
      const list = t.kids.get(container);
      if (!list) return false;
      list.splice(Math.max(0, Math.min(index, list.length)), 0, f);
      f.section = container;
      if (f.type !== "heading" && f.section !== undefined) setOwner(f, secOwner);
    } else {
      delete f.section;
      t.tops.splice(Math.max(0, Math.min(index, t.tops.length)), 0, f);
    }
    flatten(t);
    return true;
  }

  function nudgeField(f, dir) {
    const t = tree();
    if (f.section && t.kids.has(f.section)) {
      const list = t.kids.get(f.section), i = list.indexOf(f), j = i + dir;
      if (j >= 0 && j < list.length) { [list[i], list[j]] = [list[j], list[i]]; flatten(t); return; }
      const sec = byId(f.section), at = t.tops.indexOf(sec);
      moveField(f, "", dir < 0 ? at : at + 1);
      return;
    }
    const i = t.tops.indexOf(f), j = i + dir;
    if (j < 0 || j >= t.tops.length) return;
    const n = t.tops[j];
    if (n.type === "section" && f.type !== "section" && f.type !== "signature") {
      moveField(f, n.id, dir > 0 ? 0 : (t.kids.get(n.id) || []).length);
      return;
    }
    [t.tops[i], t.tops[j]] = [t.tops[j], t.tops[i]];
    flatten(t);
  }

  function renameField(oldId, newId) {
    if (!oldId || oldId === newId) return;
    const ph = new RegExp("\\{" + oldId + "\\}", "g");
    def.fields.forEach((f) => {
      if (f.section === oldId) f.section = newId;
      if (f.show_when && f.show_when.field === oldId) f.show_when.field = newId;
    });
    def.steps.forEach((s) => {
      if (s.fields) s.fields = s.fields.map((id) => (id === oldId ? newId : id));
      if (s.items === oldId) s.items = newId;
      if (s.when && s.when.field === oldId) s.when.field = newId;
      whoList(s).forEach((w) => { if (w && w.field === oldId) w.field = newId; });
      if (s.email) ["subject", "body"].forEach((part) => {
        const v = s.email[part];
        if (typeof v === "string") s.email[part] = v.replace(ph, "{" + newId + "}");
        else if (v) Object.keys(v).forEach((l) => { if (typeof v[l] === "string") v[l] = v[l].replace(ph, "{" + newId + "}"); });
      });
    });
  }

  function addField(type, container) {
    const [de, en] = NEW_LABEL[type];
    const f = { id: slug(LANG === "de" ? de : en, fieldIds(), "field"), type, label: { de, en } };
    const secOwner = container && byId(container) ? ownerOf(byId(container)) : null;
    autoId.add(f);
    if (container) f.section = container;
    def.fields.push(f);
    normalizeOrder();
    if (container && type !== "heading") setOwner(f, secOwner);
    if (type === "signature") {
      const sign = def.steps.findIndex((s) => s.type === "sign" && !(s.fields || []).length);
      if (sign >= 0) setOwner(f, sign);
    }
    ui.open.add(f);
    return f;
  }

  function removeField(f) {
    if (f.type === "section") members(f).forEach((m) => delete m.section);
    def.steps.forEach((s) => {
      if (s.fields) { s.fields = s.fields.filter((id) => id !== f.id); if (!s.fields.length) delete s.fields; }
    });
    def.fields.splice(def.fields.indexOf(f), 1);
    normalizeOrder();
  }

  function addStep(type) {
    const [de, en] = STEP_NAME[type];
    const s = { id: slug(en, stepIds(), "step"), type, label: { de, en } };
    autoId.add(s);
    if (type === "approve_per_item") {
      s.who = { kind: "item_approver" };
      const lf = def.fields.find((f) => LIST_TYPES.includes(f.type) && f.list && !isRepeatMember(f));
      if (lf) s.items = lf.id;
    } else if (type === "notify") {
      s.who = [];
      s.attach_pdf = true;
    }
    if (type === "sign") s.allow_paper = true;
    def.steps.push(s);
    if (type === "sign") {
      let sig = def.fields.find((f) => f.type === "signature" && ownerOf(f) == null);
      if (!sig) {
        sig = { id: slug("signature", fieldIds(), "field"), type: "signature", label: { de: "Unterschrift", en: "Signature" } };
        def.fields.push(sig);
        normalizeOrder();
      }
      setOwner(sig, def.steps.length - 1);
    }
    ui.open.add(s);
    return s;
  }

  function changeStepType(s, type) {
    const old = s.type;
    s.type = type;
    if (type === "notify" && !Array.isArray(s.who)) s.who = s.who ? [s.who] : [];
    if (type !== "notify" && Array.isArray(s.who)) s.who = s.who[0];
    if (type === "approve_per_item") s.who = { kind: "item_approver" };
    else if (s.who && !Array.isArray(s.who) && !WHO_FOR[type].includes(s.who.kind)) delete s.who;
    else if (Array.isArray(s.who)) s.who = s.who.filter((w) => w && WHO_FOR[type].includes(w.kind));
    if (type !== "approve_per_item") delete s.items;
    if (!FILL_STEPS.includes(type) && type !== "sign") delete s.fields;
    if (type !== "sign") delete s.allow_paper; else if (!("allow_paper" in s)) s.allow_paper = true;
    if (type !== "notify") delete s.attach_pdf;
    if (!REJECT_STEPS.includes(type)) { delete s.on_reject; delete s.on_timeout; }
    if (autoId.has(s) || old === type) s.label = { de: STEP_NAME[type][0], en: STEP_NAME[type][1] };
  }

  // ------------------------------------------------------------- DOM helpers

  function h(tag, props, ...kids) {
    const el = document.createElement(tag);
    let value;
    if (props) for (const [k, v] of Object.entries(props)) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k === "value") value = v;
      else if (k === "checked") el.checked = true;
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
      else if (k === "dataset") { for (const [dk, dv] of Object.entries(v)) if (dv != null) el.dataset[dk] = dv; }
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat(Infinity)) {
      if (kid == null || kid === false) continue;
      el.append(kid.nodeType ? kid : String(kid));
    }
    if (value !== undefined) el.value = value;
    return el;
  }

  const opt = (value, text, selected) => h("option", { value, selected: selected ? "selected" : null }, text);

  function field(labelText, control, hint, cls) {
    const single = control && /^(INPUT|SELECT|TEXTAREA)$/.test(control.tagName);
    return h(single ? "label" : "div", { class: "prop" + (cls ? " " + cls : ""), role: single ? null : "group", "aria-label": single ? null : labelText },
      h("span", { class: "prop-label" }, labelText), control, hint ? h("span", { class: "hint" }, hint) : null);
  }

  // Text input bound to obj[key] (string). `then` runs after each change.
  function textInput(obj, key, opts) {
    opts = opts || {};
    const el = h(opts.multiline ? "textarea" : "input", {
      type: opts.multiline ? null : (opts.type || "text"), value: obj[key] == null ? "" : String(obj[key]),
      placeholder: opts.placeholder, maxlength: opts.maxlength, rows: opts.rows, list: opts.list,
      "aria-label": opts.aria, dataset: { key: opts.key }, spellcheck: opts.spellcheck,
    });
    el.addEventListener(opts.lazy ? "change" : "input", () => {
      const v = el.value;
      if (opts.number) {
        const n = v.trim() === "" ? null : Number(v.replace(",", "."));
        if (n == null || Number.isNaN(n)) delete obj[key]; else obj[key] = n;
      } else if (v === "" && !opts.keepEmpty) delete obj[key];
      else obj[key] = v;
      if (opts.then) opts.then(v);
      changed(opts.redraw);
    });
    if (opts.onfocus) el.addEventListener("focus", opts.onfocus);
    return el;
  }

  // Two inputs, German and English, for a label-like property {de, en}.
  function labelPair(obj, key, title, opts) {
    opts = opts || {};
    const holder = obj[key] && typeof obj[key] === "object" ? obj[key] : { de: obj[key] || "", en: obj[key] || "" };
    const pair = (lang, langName) => {
      const el = h(opts.multiline ? "textarea" : "input", {
        value: holder[lang] || "", rows: opts.rows, placeholder: opts.placeholder ? opts.placeholder[lang] : null,
        dataset: { key: opts.key + ":" + lang }, "aria-label": title + " (" + langName + ")",
      });
      el.addEventListener("input", () => {
        holder[lang] = el.value;
        if (!holder.de && !holder.en) delete obj[key]; else obj[key] = holder;
        if (opts.then) opts.then(lang, el.value);
        changed(false);
      });
      if (opts.onfocus) el.addEventListener("focus", () => opts.onfocus(el));
      return field(title + " · " + langName, el);
    };
    return h("div", { class: "pair" }, pair("de", "DE"), pair("en", "EN"));
  }

  function checkbox(obj, key, text, opts) {
    opts = opts || {};
    const on = opts.defaultOn ? obj[key] !== false : !!obj[key];
    const el = h("input", { type: "checkbox", checked: on, dataset: { key: opts.key } });
    el.addEventListener("change", () => {
      if (opts.defaultOn) { if (el.checked) obj[key] = true; else obj[key] = false; }
      else if (el.checked) obj[key] = true; else delete obj[key];
      if (opts.then) opts.then(el.checked);
      changed(true);
    });
    return h("label", { class: "check" }, el, " ", text);
  }

  function select(options, current, onChange, opts) {
    opts = opts || {};
    const el = h("select", { dataset: { key: opts.key }, "aria-label": opts.aria, disabled: opts.disabled },
      options.map(([v, text]) => opt(v, text, String(v) === String(current ?? ""))));
    el.value = current == null ? "" : String(current);
    el.addEventListener("change", () => { onChange(el.value); changed(true); });
    return el;
  }

  // ----------------------------------------------------------------- changes

  let checkTimer = null;
  function changed(redraw) {
    ui.dirty = true;
    syncJson();
    if (redraw) render(); else { refreshHeads(); renderPreview(); }
    clearTimeout(checkTimer);
    checkTimer = setTimeout(runCheck, 600);
  }

  function syncJson() {
    jsonBox.value = JSON.stringify(def, null, 2);
    if (unsavedStamp) unsavedStamp.hidden = !ui.dirty;
    if (heading) heading.textContent = lt(def.label) || def.name || "";
  }

  let checkSeq = 0;
  function runCheck() {
    const seq = ++checkSeq;
    const fields = def.fields.slice(), steps = def.steps.slice();
    const body = new FormData();
    body.append("_csrf", csrf);
    body.append("definition", JSON.stringify(def));
    fetch(D.checkUrl, { method: "POST", body, credentials: "same-origin", headers: { Accept: "application/json" } })
      .then((r) => r.json())
      .then((res) => { if (seq === checkSeq && res && Array.isArray(res.problems)) setProblems(res.problems, fields, steps); })
      .catch(() => { /* offline or signed out: Save shows the problems */ });
  }

  function setProblems(list, fields, steps) {
    const byObj = new Map(), wf = [];
    list.forEach((p) => {
      const target = p.kind === "field" ? fields[p.index] : p.kind === "step" ? steps[p.index] : null;
      if (!target) { wf.push(p); return; }
      if (!byObj.has(target)) byObj.set(target, []);
      byObj.get(target).push(p);
    });
    problems = { wf, byObj, count: list.length };
    renderProblems();
  }

  // --------------------------------------------------------------- render

  function render() {
    const active = document.activeElement;
    const key = active && root.contains(active) && active.dataset ? active.dataset.key : null;
    const sel = key && "selectionStart" in active ? [active.selectionStart, active.selectionEnd] : null;
    const scrollY = window.scrollY;

    root.replaceChildren(
      renderGeneral(),
      renderTabs(),
      h("div", { class: "tab-panel", id: "panel-form", role: "tabpanel", hidden: ui.tab !== "form" }, renderDesigner()),
      h("div", { class: "tab-panel", id: "panel-steps", role: "tabpanel", hidden: ui.tab !== "steps" }, renderSteps()),
      h("div", { class: "tab-panel", id: "panel-check", role: "tabpanel", hidden: ui.tab !== "check" }, h("div", { id: "check-list" })),
      h("datalist", { id: "ad-users" }, D.users.map((u) => opt(u.email, u.name))),
    );
    renderPreview();
    renderProblems();

    if (key) {
      const again = root.querySelector('[data-key="' + CSS.escape(key) + '"]');
      if (again) {
        again.focus({ preventScroll: true });
        if (sel && "setSelectionRange" in again) { try { again.setSelectionRange(sel[0], sel[1]); } catch (e) { /* not a text input */ } }
      }
    }
    window.scrollTo(0, scrollY);
  }

  function renderGeneral() {
    if (!def.label || typeof def.label !== "object") def.label = { de: lt(def.label) || def.name || "", en: lt(def.label) || def.name || "" };
    return h("details", { class: "general", open: !def.name ? "open" : null },
      h("summary", null, T("Name and title of the workflow")),
      h("div", { class: "props" },
        labelPair(def, "label", T("Title"), { key: "wf:label" }),
        field(T("Internal name"), textInput(def, "name", { key: "wf:name", maxlength: 80, keepEmpty: true }),
          T("Unique; used in exports and reports. Renaming keeps the published versions."))));
  }

  function renderTabs() {
    const tab = (name, text, count) => h("button", {
      type: "button", role: "tab", id: "tab-" + name, class: "tab", "aria-selected": ui.tab === name ? "true" : "false",
      "aria-controls": "panel-" + name, onclick: () => showTab(name),
    }, text, count != null ? h("span", { class: "tab-count", dataset: { count: name } }, String(count)) : null);
    return h("div", { class: "tabs", role: "tablist" },
      tab("form", T("Form"), def.fields.length), tab("steps", T("Steps"), def.steps.length),
      tab("check", T("Check"), problemCount()));
  }

  function showTab(name) {
    ui.tab = name;
    tabInput.value = name;
    if (location.hash !== "#" + name) history.replaceState(null, "", "#" + name);
    render();
  }

  const problemCount = () => (problems.count != null ? problems.count : D.problems.length);

  // --------------------------------------------------------------- designer

  function renderDesigner() {
    const t = tree();
    const palette = h("div", { class: "palette", role: "group", "aria-label": T("Add a field") },
      h("span", { class: "palette-label" }, T("Add a field")),
      FIELD_TYPES.map((type) => h("button", {
        type: "button", class: "chip", dataset: { type },
        title: T(TYPE_NAME[type]), onclick: () => { const f = addField(type, ""); changed(true); scrollToCard(f); },
      }, h("span", { class: "type-icon", "aria-hidden": "true" }, TYPE_ICON[type]), " ", T(TYPE_NAME[type]))));

    const list = h("ol", { class: "cards field-list", dataset: { container: "" }, "aria-label": T("Fields of the form") },
      t.tops.map((f) => fieldCard(f, t)));
    sortable(list, "field");
    const empty = !def.fields.length ? h("p", { class: "empty" },
      T("The form is empty. Add fields with the buttons above; a section groups fields and can be switched on by a checkbox.")) : null;

    return h("div", { class: "designer" },
      h("div", { class: "designer-main" }, palette,
        h("a", { class: "jump-preview", href: "#preview", onclick: (e) => { e.preventDefault(); document.getElementById("preview").scrollIntoView(); } }, T("Jump to the preview ↓")),
        empty, list),
      h("aside", { class: "preview-pane", "aria-label": T("Preview") }, h("div", { id: "preview" })));
  }

  function badges(f) {
    const out = [];
    if (f.required) out.push([T("required"), ""]);
    const o = ownerOf(f);
    if (o != null && f.type !== "heading") out.push([fmt(T("Step {n}"), { n: o + 1 }), "badge-step"]);
    if (f.type === "secret") out.push([T("encrypted"), ""]);
    if (f.list) out.push([f.list, "badge-list"]);
    if (f.type === "section" && f.show_when && f.show_when.field) {
      const c = byId(f.show_when.field);
      out.push([fmt(T("only if {field}"), { field: c ? lt(c.label) : f.show_when.field }), "badge-cond"]);
    }
    if (f.type === "section" && f.repeat) {
      out.push([fmt(T("repeats {min}–{max}"), { min: f.repeat.min ?? 0, max: f.repeat.max ?? 50 }), "badge-cond"]);
    }
    return out.map(([text, cls]) => h("span", { class: "badge " + cls }, text));
  }

  function cardHead(obj, kind, title, sub, extra, onUp, onDown, upOk, downOk) {
    const u = uid(obj);
    const open = ui.open.has(obj);
    return h("div", { class: "card-head" },
      h("span", { class: "drag", title: T("Drag to move"), "aria-hidden": "true", dataset: { drag: u } }, "⠿"),
      extra,
      h("button", {
        type: "button", class: "card-title", "aria-expanded": open ? "true" : "false", dataset: { key: u + ":title" },
        onclick: () => { if (ui.open.has(obj)) ui.open.delete(obj); else ui.open.add(obj); render(); },
      }, h("span", { class: "title-text", dataset: { title: u } }, title || h("em", null, T("(no label)"))),
        h("span", { class: "id", dataset: { sub: u } }, sub)),
      h("span", { class: "badges", dataset: { badges: u } }, kind === "field" ? badges(obj) : stepBadges(obj)),
      h("span", { class: "problem-badge", dataset: { pbadge: u }, hidden: true }),
      h("span", { class: "move" },
        h("button", { type: "button", class: "icon", "aria-label": T("Move up"), title: T("Move up"), disabled: !upOk, dataset: { key: u + ":up" }, onclick: onUp }, "↑"),
        h("button", { type: "button", class: "icon", "aria-label": T("Move down"), title: T("Move down"), disabled: !downOk, dataset: { key: u + ":down" }, onclick: onDown }, "↓")));
  }

  function fieldCard(f, t) {
    const u = uid(f);
    const list = f.section && t.kids.has(f.section) ? t.kids.get(f.section) : t.tops;
    const i = list.indexOf(f);
    const canUp = i > 0 || !!f.section;
    const canDown = i < list.length - 1 || !!f.section;
    const card = h("li", { class: "card field-card type-" + f.type + (ui.open.has(f) ? " open" : ""), dataset: { uid: u, id: f.id } },
      cardHead(f, "field", lt(f.label), f.id, h("span", { class: "type-tag", title: T(TYPE_NAME[f.type] || f.type) }, TYPE_ICON[f.type] || "?"),
        () => { nudgeField(f, -1); changed(true); }, () => { nudgeField(f, 1); changed(true); }, canUp, canDown),
      h("ul", { class: "problems", dataset: { problems: u } }),
      ui.open.has(f) ? fieldBody(f) : null);
    if (f.type === "section") {
      const kids = t.kids.get(f.id) || [];
      const inner = h("ol", { class: "cards section-list", dataset: { container: f.id }, "aria-label": fmt(T("Fields in section {name}"), { name: lt(f.label) }) },
        kids.map((k) => fieldCard(k, t)));
      sortable(inner, "field");
      card.append(h("div", { class: "section-body" }, inner,
        !kids.length ? h("p", { class: "hint drop-hint" }, T("Drag fields here, or add one:")) : null,
        h("div", { class: "row-form add-in-section" },
          select([["", T("Add a field to this section …")]].concat(VALUE_TYPES.filter((x) => x !== "signature").concat(["heading"]).map((x) => [x, T(TYPE_NAME[x])])), "",
            (v) => { if (v) addField(v, f.id); }, { key: u + ":addin", aria: T("Add a field to this section …") }))));
    }
    return card;
  }

  function fieldBody(f) {
    const u = uid(f);
    const rows = [];
    const sec = sectionOf(f);
    rows.push(labelPair(f, "label", T("Label"), {
      key: u + ":label",
      then: (lang, value) => {
        if (autoId.has(f) && lang === LANG && value.trim()) { const old = f.id; f.id = slug(value, fieldIds(f), "field"); renameField(old, f.id); }
      },
    }));
    rows.push(field(T("Field id"), textInput(f, "id", {
      key: u + ":id", lazy: true, keepEmpty: true, spellcheck: "false", redraw: true,
      then: (v) => { autoId.delete(f); const nv = v.trim(); renameField(prevId.get(f), nv); f.id = nv; prevId.set(f, nv); },
      onfocus: () => { prevId.set(f, f.id); },
    }), T("Used in conditions, placeholders and exports: a-z, 0-9 and _.")));
    if (f.type !== "section" && f.type !== "heading") {
      rows.push(labelPair(f, "help", T("Help text"), { key: u + ":help" }));
    }

    const type = f.type;
    const opts = [];
    if (VALUE_TYPES.includes(type) && type !== "signature") opts.push(checkbox(f, "required", type === "checkbox" ? T("Required (must be ticked)") : T("Required"), { key: u + ":req" }));
    if (opts.length) rows.push(h("div", { class: "checks" }, opts));

    if (["text", "longtext", "secret"].includes(type)) {
      rows.push(field(T("Maximum length"), textInput(f, "max_length", { key: u + ":max", type: "number", number: true, lazy: false }), T("Characters; empty = no limit.")));
    }
    if (type === "number") {
      rows.push(h("div", { class: "pair" },
        field(T("Minimum"), textInput(f, "min", { key: u + ":min", type: "number", number: true })),
        field(T("Maximum"), textInput(f, "max", { key: u + ":maxn", type: "number", number: true }))));
    }
    if (LIST_TYPES.includes(type)) rows.push(choiceEditor(f));
    if (["text", "longtext", "number", "date", "email"].includes(type)) {
      rows.push(field(T("Default value"), textInput(f, "default", { key: u + ":def", type: type === "number" ? "number" : type === "date" ? "date" : "text", number: type === "number" })));
    }
    if (type === "checkbox") rows.push(h("div", { class: "checks" }, checkbox(f, "default", T("Ticked by default"), { key: u + ":defc" })));
    if (type === "dropdown") {
      const choices = choicesOf(f);
      rows.push(field(T("Default value"), select([["", T("none")]].concat(choices.map((c) => [c.value, c.label])), f.default,
        (v) => { if (v) f.default = v; else delete f.default; }, { key: u + ":defd" })));
    }
    if (type === "section") rows.push(sectionEditor(f));

    // Who fills the field
    if (type !== "heading") {
      if (sec) {
        rows.push(h("p", { class: "hint" }, T("Filled by:") + " " + ownerText(ownerOf(sec)) + " – " + T("set on the section.")));
      } else {
        const allowed = type === "signature" ? ["sign"] : FILL_STEPS;
        const choices = (type === "signature" ? [] : [["", T("At the start (the person who starts the request)")]])
          .concat(def.steps.map((s, i) => [String(i), fmt(T("Step {n}: {label}"), { n: i + 1, label: lt(s.label) || s.id })])
            .filter(([i]) => allowed.includes(def.steps[+i].type)));
        if (type === "signature" && !choices.length) choices.push(["", T("— add a Sign step first —")]);
        const cur = ownerOf(f);
        rows.push(field(T("Filled by"), select(choices, cur == null ? "" : String(cur), (v) => setOwner(f, v === "" ? null : +v), { key: u + ":owner" }),
          type === "section" ? T("All fields of the section are filled by this step.")
            : type === "signature" ? T("A signature belongs to a Sign step.")
              : T("Later steps can only fill fields of Fill form or Fill and approve steps.")));
      }
    }
    // Section membership, the keyboard way to move in and out
    if (type !== "section" && type !== "signature") {
      const secs = def.fields.filter((x) => x.type === "section");
      if (secs.length) {
        rows.push(field(T("In section"), select([["", T("none")]].concat(secs.map((s) => [s.id, lt(s.label) || s.id])), f.section || "",
          (v) => { const t = tree(); moveField(f, v, v ? (t.kids.get(v) || []).length : t.tops.length); }, { key: u + ":sec" })));
      }
    }
    rows.push(h("div", { class: "card-actions" },
      h("button", { type: "button", class: "danger small", onclick: () => {
        const msg = f.type === "section" ? T("Delete this section? Its fields stay in the form, outside the section.") : fmt(T("Delete the field \"{label}\"?"), { label: lt(f.label) || f.id });
        if (window.confirm(msg)) { ui.open.delete(f); removeField(f); changed(true); }
      } }, f.type === "section" ? T("Delete section") : T("Delete field"))));
    return h("div", { class: "card-body props" }, rows);
  }

  function ownerText(o) {
    if (o == null) return T("at the start");
    const s = def.steps[o];
    return fmt(T("Step {n}: {label}"), { n: o + 1, label: s ? lt(s.label) || s.id : "?" });
  }

  function choicesOf(f) {
    if (f.list) {
      const lst = D.lists.find((l) => l.name === f.list);
      return lst ? lst.items.map((i) => ({ value: i.value, label: i.label })) : [];
    }
    return (f.options || []).map((o) => ({ value: String(o.value), label: lt(o.label, ui.pvLang) || String(o.value) }));
  }

  function choiceEditor(f) {
    const u = uid(f);
    const fromList = f.options == null || !!f.list;
    const src = h("div", { class: "checks", role: "radiogroup", "aria-label": T("Choices") },
      h("label", { class: "check" }, h("input", { type: "radio", name: u + "-src", checked: fromList, dataset: { key: u + ":srcl" },
        onchange: () => { delete f.options; delete f.default; changed(true); } }), " ", T("From a list")),
      h("label", { class: "check" }, h("input", { type: "radio", name: u + "-src", checked: !fromList, dataset: { key: u + ":srco" },
        onchange: () => { delete f.list; delete f.default; f.options = f.options || []; changed(true); } }), " ", T("Fixed options")));
    const out = [src];
    if (fromList) {
      const choices = [["", T("— choose a list —")]].concat(D.lists.map((l) => [l.name, l.label + " (" + l.name + ", " + l.items.length + ")"]));
      if (f.list && !D.lists.some((l) => l.name === f.list)) choices.push([f.list, f.list + " – " + T("missing")]);
      out.push(field(T("List"), select(choices, f.list || "", (v) => { if (v) f.list = v; else delete f.list; delete f.default; }, { key: u + ":list" }),
        T("Lists are kept under Lists; software comes from the CSV import.")));
    } else {
      const table = h("div", { class: "options" },
        (f.options || []).map((o, i) => {
          if (o.label == null || typeof o.label !== "object") o.label = { de: o.label || "", en: o.label || "" };
          return h("div", { class: "option-row" },
            textInput(o, "value", { key: u + ":ov" + i, aria: T("Value"), placeholder: T("Value"), keepEmpty: true }),
            textInput(o.label, "de", { key: u + ":ode" + i, aria: T("Label") + " DE", placeholder: "DE" }),
            textInput(o.label, "en", { key: u + ":oen" + i, aria: T("Label") + " EN", placeholder: "EN" }),
            h("button", { type: "button", class: "icon", "aria-label": T("Remove"), title: T("Remove"),
              onclick: () => { f.options.splice(i, 1); changed(true); } }, "×"));
        }),
        h("button", { type: "button", class: "small", dataset: { key: u + ":oadd" }, onclick: () => {
          f.options = f.options || [];
          f.options.push({ value: "", label: { de: "", en: "" } });
          changed(true);
          const rowsEls = root.querySelectorAll('[data-key^="' + u + ':ov"]');
          if (rowsEls.length) rowsEls[rowsEls.length - 1].focus();
        } }, T("+ Add option")));
      out.push(field(T("Options"), table, T("The value is stored; the label is shown.")));
    }
    return h("div", { class: "group" }, out);
  }

  // Fields a condition may use, filled at or before `at` (null = start), never in repeatable sections.
  function conditionFields(at, inclusive, outsideSections) {
    return def.fields.filter((f) => {
      if (!VALUE_TYPES.includes(f.type) || f.type === "signature") return false;
      if (outsideSections && f.section) return false;
      if (isRepeatMember(f)) return false;
      const o = ownerOf(f);
      if (o == null) return true;
      if (at == null) return false;
      return inclusive ? o <= at : o < at;
    });
  }

  function conditionEditor(holder, key, candidates, u) {
    const c = holder[key];
    const fieldSel = select([["", T("— choose a field —")]].concat(candidates.map((f) => [f.id, lt(f.label) + " (" + f.id + ")"]))
      .concat(c.field && !candidates.some((f) => f.id === c.field) ? [[c.field, c.field + " – " + T("not available here")]] : []),
      c.field || "", (v) => {
        c.field = v;
        const f = byId(v);
        if (f && f.type === "checkbox") { c.op = "equals"; c.value = true; }
        else if (c.op === "any_needs_approval" && !(f && f.list)) c.op = "equals";
      }, { key: u + ":cf", aria: T("Field") });
    const f = byId(c.field);
    let ops = ["equals", "not_equals", "contains", "not_contains", "is_set", "is_empty"];
    if (f && f.type === "checkbox") ops = ["equals", "not_equals"];
    if (f && LIST_TYPES.includes(f.type) && f.list) ops = ops.concat(["any_needs_approval"]);
    const opSel = select(ops.map((o) => [o, T(OP_NAME[o])]), c.op || "equals", (v) => {
      c.op = v;
      if (!VALUE_OPS.includes(v)) delete c.value; else if (!("value" in c)) c.value = "";
    }, { key: u + ":co", aria: T("Condition") });
    let valueEl = null;
    if (VALUE_OPS.includes(c.op || "equals")) {
      if (!c.op) c.op = "equals";
      if (!("value" in c)) c.value = "";
      if (f && f.type === "checkbox") {
        valueEl = select([["true", T("ticked")], ["false", T("not ticked")]], c.value === false || c.value === "false" ? "false" : "true",
          (v) => { c.value = v === "true"; }, { key: u + ":cv", aria: T("Value") });
      } else if (f && LIST_TYPES.includes(f.type)) {
        const ch = choicesOf(f);
        valueEl = select([["", T("— choose —")]].concat(ch.map((x) => [x.value, x.label])), c.value, (v) => { c.value = v; }, { key: u + ":cv", aria: T("Value") });
      } else {
        valueEl = textInput(c, "value", { key: u + ":cv", aria: T("Value"), keepEmpty: true, placeholder: T("Value") });
      }
    }
    return h("div", { class: "condition" }, fieldSel, opSel, valueEl);
  }

  function sectionEditor(f) {
    const u = uid(f);
    const shown = !!f.show_when;
    const at = ownerOf(f);
    const out = [];
    out.push(field(T("Show the section"), select([["always", T("Always")], ["when", T("Only when …")]], shown ? "when" : "always", (v) => {
      if (v === "when") {
        const cb = conditionFields(at, true, true).find((x) => x.type === "checkbox");
        f.show_when = cb ? { field: cb.id, op: "equals", value: true } : { field: "", op: "equals", value: "" };
      } else delete f.show_when;
    }, { key: u + ":show" }), T("Hidden sections are not required and not stored.")));
    if (shown) out.push(conditionEditor(f, "show_when", conditionFields(at, true, true), u + ":sw"));
    const rep = h("div", { class: "checks" }, checkbox(f, "repeat", T("Can be added several times (repeatable)"), {
      key: u + ":rep",
      then: (on) => {
        if (on) f.repeat = { min: 1, max: 6, add_label: { de: "Weiteren " + (lt(f.label, "de") || "Eintrag") + " hinzufügen", en: "Add another " + (lt(f.label, "en") || "entry").toLowerCase() } };
        else delete f.repeat;
      },
    }));
    out.push(rep);
    if (f.repeat) {
      if (typeof f.repeat !== "object") f.repeat = { min: 1, max: 6 };
      out.push(h("div", { class: "pair" },
        field(T("At least"), textInput(f.repeat, "min", { key: u + ":rmin", type: "number", number: true })),
        field(T("At most"), textInput(f.repeat, "max", { key: u + ":rmax", type: "number", number: true }))));
      out.push(labelPair(f.repeat, "add_label", T("Button text"), { key: u + ":radd", placeholder: { de: "Weiteren Monitor hinzufügen", en: "Add another monitor" } }));
    }
    return h("div", { class: "group" }, out);
  }

  // ----------------------------------------------------------------- steps

  function stepBadges(s) {
    const out = [];
    const who = whoList(s).map(whoText).filter(Boolean);
    if (who.length) out.push(h("span", { class: "badge" }, "→ " + who.join(", ")));
    if (s.when && s.when.field) {
      const f = byId(s.when.field);
      out.push(h("span", { class: "badge badge-cond" }, fmt(T("only if {field}"), { field: f ? lt(f.label) : s.when.field })));
    }
    if (s.type === "notify" && s.attach_pdf) out.push(h("span", { class: "badge" }, T("with PDF")));
    if (s.on_timeout === "reject") out.push(h("span", { class: "badge" }, T("auto-reject")));
    return out;
  }

  function whoText(w) {
    if (!w || !w.kind) return "";
    if (w.kind === "person") return w.email || T("a person");
    if (w.kind === "group") return w.group ? T("group") + " " + w.group : T("a group");
    if (w.kind === "field") { const f = byId(w.field); return f ? lt(f.label) : (w.field || T("a field")); }
    if (w.kind === "starter") return T("the starter");
    if (w.kind === "manager_of") { const f = byId(w.field); return fmt(T("manager of {who}"), { who: f ? lt(f.label) : T("the starter") }); }
    if (w.kind === "item_approver") return T("approver of each item");
    return w.kind;
  }

  function renderSteps() {
    const startFields = def.fields.filter((f) => f.type !== "heading" && f.type !== "section" && !f.section && ownerOf(f) == null);
    const startCard = h("div", { class: "card start-card" },
      h("div", { class: "card-head" }, h("span", { class: "step-no" }, "0"), h("span", { class: "type-tag" }, "▶"),
        h("strong", null, T("Start")), h("span", { class: "hint" }, T("The person who starts the request fills the fields marked 'at the start'."))),
      h("p", { class: "start-fields" }, startFields.length ? startFields.map((f) => h("span", { class: "badge" }, lt(f.label) || f.id))
        : h("span", { class: "hint" }, T("No fields yet."))),
      h("p", { class: "hint" }, h("a", { href: form.querySelector("a[href*='/admin/start']").getAttribute("href") }, T("Who may start this workflow")), " – ", T("set on its own page after publishing.")));
    const list = h("ol", { class: "cards step-list", dataset: { container: "steps" }, "aria-label": T("Steps") },
      def.steps.map((s, i) => stepCard(s, i)));
    sortable(list, "step");
    const typeSel = h("select", { id: "new-step-type", "aria-label": T("Step type"), dataset: { key: "newstep" } },
      STEP_TYPES.map((t) => opt(t, T(STEP_NAME[t][1]))));
    typeSel.value = ui.newStep || "fill";
    typeSel.addEventListener("change", () => { ui.newStep = typeSel.value; hint.textContent = T(STEP_HINT[typeSel.value]); });
    const hint = h("span", { class: "hint" }, T(STEP_HINT[typeSel.value]));
    return h("div", { class: "steps" },
      h("p", { class: "lead" }, T("Steps run in this order, one after the other. A step whose condition is not met is skipped.")),
      startCard, list,
      !def.steps.length ? h("p", { class: "empty" }, T("No steps yet. Add the first one below.")) : null,
      h("div", { class: "add-step" }, h("label", { class: "prop" }, h("span", { class: "prop-label" }, T("Add a step")),
        h("span", { class: "row-form" }, typeSel, h("button", { type: "button", class: "primary", onclick: () => {
          const s = addStep(typeSel.value); changed(true); scrollToCard(s);
        } }, T("Add step")))), hint));
  }

  function stepCard(s, i) {
    const u = uid(s);
    return h("li", { class: "card step-card" + (ui.open.has(s) ? " open" : ""), dataset: { uid: u, id: s.id } },
      cardHead(s, "step", lt(s.label), (STEP_NAME[s.type] ? T(STEP_NAME[s.type][1]) : s.type) + " · " + s.id,
        [h("span", { class: "step-no" }, String(i + 1)), h("span", { class: "type-tag", title: T(STEP_NAME[s.type] ? STEP_NAME[s.type][1] : s.type) }, stepIcon(s.type))],
        () => { moveStep(s, i - 1); changed(true); }, () => { moveStep(s, i + 1); changed(true); }, i > 0, i < def.steps.length - 1),
      h("ul", { class: "problems", dataset: { problems: u } }),
      ui.open.has(s) ? stepBody(s, i) : null);
  }

  const stepIcon = (t) => ({ fill: "✎", approve: "✓", fill_approve: "✎✓", approve_per_item: "✓✓", sign: "✍", confirm: "⏎", notify: "✉" })[t] || "?";

  function moveStep(s, to) {
    const from = def.steps.indexOf(s);
    if (to < 0 || to >= def.steps.length || to === from) return;
    def.steps.splice(from, 1);
    def.steps.splice(to, 0, s);
  }

  function stepBody(s, i) {
    const u = uid(s);
    const rows = [];
    rows.push(h("div", { class: "pair" },
      field(T("Step type"), select(STEP_TYPES.map((t) => [t, T(STEP_NAME[t][1])]), s.type, (v) => changeStepType(s, v), { key: u + ":type" }), T(STEP_HINT[s.type] || "")),
      field(T("Step id"), textInput(s, "id", { key: u + ":id", lazy: true, keepEmpty: true, spellcheck: "false", redraw: true, then: () => autoId.delete(s) }))));
    rows.push(labelPair(s, "label", T("Label"), {
      key: u + ":label",
      then: (lang, value) => { if (autoId.has(s) && lang === LANG && value.trim()) s.id = slug(value, stepIds(s), "step"); },
    }));

    if (FILL_STEPS.includes(s.type) || s.type === "sign") {
      const own = (s.fields || []).map(byId).filter(Boolean);
      rows.push(h("div", { class: "group" }, h("span", { class: "prop-label" }, s.type === "sign" ? T("Signature field") : T("Fields filled in this step")),
        h("p", { class: "start-fields" }, own.length ? own.map((f) => h("span", { class: "badge" }, lt(f.label) || f.id))
          : h("span", { class: "hint" }, T("None yet."))),
        h("p", { class: "hint" }, s.type === "sign" ? T("Add a Signature field in the form and set 'Filled by' to this step.")
          : T("In the form, set 'Filled by' of a field or section to this step."))));
    }

    if (s.type === "approve_per_item") {
      const lf = def.fields.filter((f) => LIST_TYPES.includes(f.type) && f.list && !isRepeatMember(f));
      rows.push(field(T("Items of the field"), select([["", T("— choose a field —")]].concat(lf.map((f) => [f.id, lt(f.label) + " (" + f.list + ")"])), s.items || "",
        (v) => { if (v) s.items = v; else delete s.items; }, { key: u + ":items" }), T("Each chosen entry that needs approval goes to its approver.")));
    }

    rows.push(whoEditor(s, i));

    // Condition
    const cands = conditionFields(i, false, false);
    rows.push(field(T("When"), select([["always", T("Always")], ["when", T("Only when …")]], s.when ? "when" : "always", (v) => {
      if (v === "when") {
        const nf = s.type === "approve_per_item" && s.items ? byId(s.items) : null;
        s.when = nf ? { field: nf.id, op: "any_needs_approval" } : { field: cands[0] ? cands[0].id : "", op: "equals", value: "" };
        const cf = byId(s.when.field);
        if (cf && cf.type === "checkbox") s.when.value = true;
      } else delete s.when;
    }, { key: u + ":when" })));
    if (s.when) rows.push(conditionEditor(s, "when", cands, u + ":w"));

    if (REJECT_STEPS.includes(s.type)) {
      rows.push(h("div", { class: "pair" },
        field(T("On reject"), select([["starter", T("Back to the person who started the request")], ["previous", T("Back one step")]], s.on_reject || "starter",
          (v) => { if (v === "starter") delete s.on_reject; else s.on_reject = v; }, { key: u + ":rej" })),
        field(T("On no response"), select([["wait", T("Wait; tell the starter and the admin")], ["reject", T("Reject automatically")]], s.on_timeout || "wait",
          (v) => { if (v === "wait") delete s.on_timeout; else s.on_timeout = v; }, { key: u + ":tmo" }), T("When the link expires (14 days by default)."))));
    }
    const checks = [];
    if (s.type === "sign") checks.push(checkbox(s, "allow_paper", T("Allow signature on paper (scan uploaded by the starter)"), { key: u + ":paper", defaultOn: true }));
    if (s.type === "notify") checks.push(checkbox(s, "attach_pdf", T("Attach the PDF of the request"), { key: u + ":pdf" }));
    if (checks.length) rows.push(h("div", { class: "checks" }, checks));

    rows.push(emailEditor(s));
    rows.push(h("div", { class: "card-actions" },
      h("button", { type: "button", class: "danger small", onclick: () => {
        if (window.confirm(fmt(T("Delete the step \"{label}\"?"), { label: lt(s.label) || s.id }))) {
          ui.open.delete(s);
          def.steps.splice(def.steps.indexOf(s), 1);
          changed(true);
        }
      } }, T("Delete step"))));
    return h("div", { class: "card-body props" }, rows);
  }

  function whoEditor(s, i) {
    const u = uid(s);
    const kinds = WHO_FOR[s.type] || [];
    const one = (w, onKind, key) => {
      const parts = [select([["", T("— choose —")]].concat(kinds.map((k) => [k, T(WHO_NAME[k])])), w ? w.kind : "", onKind, { key: key + ":k", aria: T("Who") })];
      if (w && w.kind === "person") {
        parts.push(textInput(w, "email", { key: key + ":e", type: "email", list: "ad-users", placeholder: "name@company.de", aria: T("Email"), keepEmpty: true }));
      } else if (w && w.kind === "group") {
        const groups = D.groups.slice();
        if (w.group && !groups.includes(w.group)) groups.push(w.group);
        parts.push(select([["", T("— choose a group —")]].concat(groups.map((g) => [g, g])), w.group || "", (v) => { w.group = v; }, { key: key + ":g", aria: T("Group") }));
      } else if (w && (w.kind === "field" || w.kind === "manager_of")) {
        const types = w.kind === "field" ? ["person", "email"] : ["person"];
        const cand = def.fields.filter((f) => types.includes(f.type) && !isRepeatMember(f) && (ownerOf(f) == null || ownerOf(f) < i));
        const first = w.kind === "manager_of" ? [["", T("the person who started the request")]] : [["", T("— choose a field —")]];
        const extra = w.field && !cand.some((f) => f.id === w.field) ? [[w.field, w.field + " – " + T("not available here")]] : [];
        parts.push(select(first.concat(cand.map((f) => [f.id, lt(f.label) + " (" + f.id + ")"])).concat(extra), w.field || "",
          (v) => { if (v) w.field = v; else delete w.field; }, { key: key + ":f", aria: T("Field") }));
      }
      return parts;
    };
    if (s.type === "notify") {
      if (!Array.isArray(s.who)) s.who = s.who ? [s.who] : [];
      const rowsEls = s.who.map((w, n) => h("div", { class: "who-row" },
        one(w, (v) => { s.who[n] = v ? { kind: v } : {}; }, u + ":who" + n),
        h("button", { type: "button", class: "icon", "aria-label": T("Remove"), title: T("Remove"), onclick: () => { s.who.splice(n, 1); changed(true); } }, "×")));
      return field(T("Who receives the email"), h("div", { class: "who" }, rowsEls,
        h("button", { type: "button", class: "small", dataset: { key: u + ":whoadd" }, onclick: () => { s.who.push({ kind: "group" }); changed(true); } }, T("+ Add recipient"))),
      T("People, AD groups or an email field. Several recipients are allowed."));
    }
    const w = Array.isArray(s.who) ? s.who[0] : s.who;
    const hint = s.type === "confirm" ? T("For a group, whoever confirms first confirms for the group.")
      : s.type === "approve_per_item" ? T("Set the approver of each entry in the software list (CSV).")
        : REJECT_STEPS.includes(s.type) ? T("Approvals and signatures go to one person, never a group. The starter never approves their own request; such a task goes to the admin.")
          : null;
    return field(T("Who"), h("div", { class: "who-row" }, one(w, (v) => { s.who = v ? { kind: v } : undefined; if (!v) delete s.who; }, u + ":who")), hint);
  }

  function emailEditor(s) {
    const u = uid(s);
    const defaults = D.emailDefaults[s.type] || D.emailDefaults.notify;
    const em = s.email && typeof s.email === "object" ? s.email : {};
    const focus = (el) => { ui.focusEmail = el; };
    const tidy = () => {
      ["subject", "body"].forEach((p) => {
        const v = em[p];
        if (v && typeof v === "object" && !v.de && !v.en) delete em[p];
      });
      if (em.subject || em.body) s.email = em; else delete s.email;
    };
    const holder = h("details", { class: "email", open: ui.openEmail && ui.openEmail.has(s) ? "open" : null },
      h("summary", null, s.type === "notify" ? T("Email text") : T("Email to the recipient"),
        " ", h("span", { class: "hint" }, em.subject || em.body ? T("(own text)") : T("(default text)"))));
    holder.addEventListener("toggle", () => {
      ui.openEmail = ui.openEmail || new WeakSet();
      if (holder.open) ui.openEmail.add(s); else ui.openEmail.delete(s);
    });
    const allowed = Object.keys(D.placeholders).filter((p) => !(p === "link" && s.type === "notify"));
    const fieldPh = def.fields.filter((f) => VALUE_TYPES.includes(f.type) && f.type !== "secret" && f.type !== "signature" && !isRepeatMember(f));
    const chip = (name, title) => h("button", { type: "button", class: "chip small", title, onclick: () => insertPlaceholder("{" + name + "}") }, "{" + name + "}");
    holder.append(
      h("p", { class: "hint" }, T("Empty fields use the default text shown in grey. Emails to approvers and signers are sent in German.")),
      labelPair(em, "subject", T("Subject"), { key: u + ":subj", placeholder: defaults.subject, onfocus: focus, then: tidy }),
      labelPair(em, "body", T("Text"), { key: u + ":body", multiline: true, rows: 7, placeholder: defaults.body, onfocus: focus, then: tidy }),
      h("div", { class: "placeholders" }, h("span", { class: "prop-label" }, T("Insert placeholder")),
        allowed.map((p) => chip(p, D.placeholders[p])), fieldPh.map((f) => chip(f.id, lt(f.label)))),
      h("button", { type: "button", class: "small", onclick: () => { delete s.email; changed(true); } }, T("Use the default text")));
    return holder;
  }

  function insertPlaceholder(text) {
    const el = ui.focusEmail;
    if (!el || !document.body.contains(el)) { window.alert(T("Click into a subject or text box first.")); return; }
    const a = el.selectionStart ?? el.value.length, b = el.selectionEnd ?? el.value.length;
    el.value = el.value.slice(0, a) + text + el.value.slice(b);
    el.setSelectionRange(a + text.length, a + text.length);
    el.dispatchEvent(new Event("input"));
    el.focus();
  }

  // ------------------------------------------------------------- problems

  function renderProblems() {
    const list = (obj) => problems.byObj.get(obj) || [];
    root.querySelectorAll("[data-problems]").forEach((ul) => ul.replaceChildren());
    root.querySelectorAll("[data-pbadge]").forEach((b) => { b.hidden = true; });
    const fill = (obj) => {
      const u = uid(obj), ps = list(obj);
      const ul = root.querySelector('[data-problems="' + u + '"]');
      const badge = root.querySelector('[data-pbadge="' + u + '"]');
      if (ul) ps.forEach((p) => ul.append(h("li", null, p.message)));
      if (badge && ps.length) { badge.hidden = false; badge.textContent = "! " + ps.length; badge.title = ps.map((p) => p.message).join("\n"); }
    };
    def.fields.forEach(fill);
    def.steps.forEach(fill);

    const n = problemCount();
    root.querySelectorAll('[data-count="check"]').forEach((el) => { el.textContent = String(n); el.classList.toggle("has-problems", n > 0); });
    if (checkStamp) {
      checkStamp.textContent = n ? n + " " + T("problem(s)") : T("Ready to publish");
      checkStamp.classList.toggle("stamp-red", n > 0);
    }
    const holder = root.querySelector("#check-list");
    if (!holder) return;
    holder.replaceChildren();
    if (!n) {
      holder.append(h("p", { class: "ok-box" }, h("span", { class: "stamp" }, T("Ready to publish")), " ",
        T("No problems found. Publish creates a new version; running requests keep theirs.")));
      return;
    }
    holder.append(h("p", { class: "lead" }, T("Fix these before publishing. Click a line to go to the field or step. The check runs as you edit.")));
    const section = (title, items) => items.length ? h("section", { class: "check-group" }, h("h2", null, title), h("ul", { class: "check-items" }, items)) : null;
    const wf = problems.wf.map((p) => h("li", null, p.message));
    const fieldsItems = [], stepItems = [];
    problems.byObj.forEach((ps, obj) => {
      const isStep = def.steps.includes(obj);
      if (!isStep && !def.fields.includes(obj)) return;
      const name = isStep ? fmt(T("Step {n}: {label}"), { n: def.steps.indexOf(obj) + 1, label: lt(obj.label) || obj.id }) : (lt(obj.label) || obj.id || "?");
      const li = h("li", null, h("button", { type: "button", class: "link", onclick: () => goTo(obj, isStep ? "steps" : "form") }, name),
        h("ul", null, ps.map((p) => h("li", null, p.message))));
      (isStep ? stepItems : fieldsItems).push([isStep ? def.steps.indexOf(obj) : def.fields.indexOf(obj), li]);
    });
    const sorted = (arr) => arr.sort((a, b) => a[0] - b[0]).map((x) => x[1]);
    holder.append(...[section(T("Workflow"), wf), section(T("Form"), sorted(fieldsItems)), section(T("Steps"), sorted(stepItems))].filter(Boolean));
  }

  function goTo(obj, tab) {
    ui.open.add(obj);
    showTab(tab);
    scrollToCard(obj);
  }

  function scrollToCard(obj) {
    const el = root.querySelector('[data-uid="' + uid(obj) + '"]');
    if (!el) return;
    el.scrollIntoView({ block: "center", behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    const title = el.querySelector(".card-title");
    if (title) title.focus({ preventScroll: true });
  }

  // Refresh what a typed character can change without rebuilding inputs.
  function refreshHeads() {
    const each = (obj, kind) => {
      const u = uid(obj);
      const t = root.querySelector('[data-title="' + u + '"]');
      if (t) t.textContent = lt(obj.label) || T("(no label)");
      const sub = root.querySelector('[data-sub="' + u + '"]');
      if (sub) sub.textContent = kind === "field" ? obj.id : (STEP_NAME[obj.type] ? T(STEP_NAME[obj.type][1]) : obj.type) + " · " + obj.id;
      const idBox = root.querySelector('[data-key="' + u + ':id"]');
      if (idBox && document.activeElement !== idBox && idBox.value !== obj.id) idBox.value = obj.id;
      const card = root.querySelector('[data-uid="' + u + '"]');
      if (card) card.dataset.id = obj.id;
      const b = root.querySelector('[data-badges="' + u + '"]');
      if (b) b.replaceChildren(...(kind === "field" ? badges(obj) : stepBadges(obj)));
    };
    def.fields.forEach((f) => each(f, "field"));
    def.steps.forEach((s) => each(s, "step"));
  }

  // -------------------------------------------------------- drag and drop

  let dragging = null; // {kind, obj}

  function sortable(listEl, kind) {
    listEl.addEventListener("mousedown", (e) => {
      const handle = e.target.closest("[data-drag]");
      if (!handle) return;
      const li = handle.closest("li.card");
      if (li && li.parentElement === listEl) li.draggable = true;
    });
    listEl.addEventListener("dragstart", (e) => {
      const li = e.target.closest("li.card");
      if (!li || li.parentElement !== listEl || dragging) return;
      const obj = objByUid(li.dataset.uid);
      if (!obj) return;
      dragging = { kind, obj };
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", li.dataset.id || "");
      li.classList.add("dragging");
      e.stopPropagation();
    });
    listEl.addEventListener("dragend", (e) => {
      const li = e.target.closest("li.card");
      if (li) { li.draggable = false; li.classList.remove("dragging"); }
      clearMarks();
      dragging = null;
    });
    listEl.addEventListener("dragover", (e) => {
      if (!dragging || dragging.kind !== kind) return;
      const container = listEl.dataset.container;
      if (kind === "field" && container && (dragging.obj.type === "section" || dragging.obj.type === "signature")) return;
      e.preventDefault();
      e.stopPropagation();
      e.dataTransfer.dropEffect = "move";
      clearMarks();
      const spot = dropSpot(listEl, e.clientY);
      if (spot.li) spot.li.classList.add(spot.after ? "drop-after" : "drop-before");
      else listEl.classList.add("drop-into");
    });
    listEl.addEventListener("drop", (e) => {
      if (!dragging || dragging.kind !== kind) return;
      e.preventDefault();
      e.stopPropagation();
      const spot = dropSpot(listEl, e.clientY);
      const container = listEl.dataset.container;
      if (kind === "step") {
        const from = def.steps.indexOf(dragging.obj);
        let to = spot.index;
        if (from < to) to -= 1;
        moveStep(dragging.obj, to);
      } else {
        const t = tree();
        const current = dragging.obj.section && t.kids.has(dragging.obj.section) ? t.kids.get(dragging.obj.section) : t.tops;
        const sameList = current === (container ? t.kids.get(container) : t.tops);
        let to = spot.index;
        if (sameList && current.indexOf(dragging.obj) < to) to -= 1;
        moveField(dragging.obj, container, to);
      }
      clearMarks();
      dragging = null;
      changed(true);
    });
  }

  function dropSpot(listEl, y) {
    const items = Array.from(listEl.children).filter((c) => c.matches("li.card"));
    for (let n = 0; n < items.length; n++) {
      const r = items[n].querySelector(".card-head").getBoundingClientRect();
      if (y < r.top + r.height / 2) return { li: items[n], after: false, index: n };
      const whole = items[n].getBoundingClientRect();
      if (y < whole.bottom) return { li: items[n], after: true, index: n + 1 };
    }
    return items.length ? { li: items[items.length - 1], after: true, index: items.length } : { li: null, index: 0 };
  }

  function clearMarks() {
    root.querySelectorAll(".drop-before,.drop-after,.drop-into").forEach((el) => el.classList.remove("drop-before", "drop-after", "drop-into"));
  }

  function objByUid(u) {
    return def.fields.find((f) => uid(f) === u) || def.steps.find((s) => uid(s) === u);
  }

  // --------------------------------------------------------------- preview

  function renderPreview() {
    const box = document.getElementById("preview");
    if (!box) return;
    const L = ui.pvLang;
    const stages = [["start", T("At the start")]].concat(def.steps.map((s, i) => [String(i), s]).filter(([, s]) => FILL_STEPS.includes(s.type) || s.type === "sign")
      .map(([i, s]) => [i, fmt(T("Step {n}: {label}"), { n: +i + 1, label: lt(s.label) || s.id })]));
    if (!stages.some(([v]) => v === ui.stage)) ui.stage = "start";
    const stageSel = h("select", { "aria-label": T("Preview of"), dataset: { key: "pv:stage" } }, stages.map(([v, t]) => opt(v, t, v === ui.stage)));
    stageSel.value = ui.stage;
    stageSel.addEventListener("change", () => { ui.stage = stageSel.value; renderPreview(); });
    const langs = h("span", { class: "pv-lang", role: "group", "aria-label": T("Language of the preview") },
      ["de", "en"].map((l) => h("button", { type: "button", class: "small" + (l === L ? " on" : ""), "aria-pressed": l === L ? "true" : "false",
        onclick: () => { ui.pvLang = l; renderPreview(); } }, l.toUpperCase())));

    const at = ui.stage === "start" ? null : +ui.stage;
    const t = tree();
    const inStage = (f) => {
      if (f.type === "heading") return true;
      const o = ownerOf(f);
      return at == null ? o == null && f.type !== "signature" : o === at;
    };
    const items = [];
    t.tops.forEach((f) => {
      if (f.type === "section") {
        const kids = (t.kids.get(f.id) || []).filter((k) => k.type !== "heading");
        if (!kids.length || !inStage(f)) return;
        items.push(pvSection(f, t.kids.get(f.id) || [], L));
      } else if (f.type === "heading") {
        items.push(h("h3", { class: "pv-heading" }, lt(f.label, L)));
      } else if (inStage(f)) items.push(pvField(f, L, pv.values, f.id));
    });
    const title = lt(def.label, L) || def.name || "";
    box.replaceChildren(
      h("div", { class: "pv-bar" }, h("strong", null, T("Preview")), stageSel, langs),
      h("div", { class: "pv-sheet", lang: L },
        h("p", { class: "pv-title" }, title),
        items.length ? items : h("p", { class: "hint" }, T("Nothing to fill in at this point.")),
        h("div", { class: "pv-actions" }, h("button", { type: "button", class: "primary", disabled: true }, pvButton(at, L)))),
      h("p", { class: "hint" }, T("The preview works like the real form: tick a box to show its section. Nothing is sent.")));
  }

  function pvButton(at, L) {
    const de = L === "de";
    if (at == null) return de ? "Antrag senden" : "Send request";
    const tpe = def.steps[at] ? def.steps[at].type : "fill";
    if (tpe === "sign") return de ? "Unterschreiben" : "Sign";
    if (tpe === "fill_approve") return de ? "Genehmigen" : "Approve";
    return de ? "Absenden" : "Submit";
  }

  function pvVisible(sec) {
    const c = sec.show_when;
    if (!c || !c.field) return true;
    const f = byId(c.field);
    let v = pv.values[c.field];
    if (v === undefined && f) v = f.type === "checkbox" ? !!f.default : f.default;
    const empty = v == null || v === "" || v === false || (Array.isArray(v) && !v.length);
    const target = c.value;
    switch (c.op) {
      case "is_set": return !empty;
      case "is_empty": return empty;
      case "any_needs_approval": {
        const lst = f && D.lists.find((l) => l.name === f.list);
        const vals = Array.isArray(v) ? v : v ? [v] : [];
        return !!lst && lst.items.some((i) => i.needs_approval && vals.includes(i.value));
      }
      case "contains": case "not_contains": {
        const has = Array.isArray(v) ? v.map(String).includes(String(target)) : String(v || "").toLowerCase().includes(String(target).toLowerCase());
        return c.op === "contains" ? has : !has;
      }
      default: {
        let eq;
        if (typeof v === "boolean" || (f && f.type === "checkbox")) eq = !!v === (target === true || ["1", "true", "yes", "on", "ja"].includes(String(target).toLowerCase()));
        else eq = String(v ?? "").trim().toLowerCase() === String(target ?? "").trim().toLowerCase();
        return c.op === "not_equals" ? !eq : eq;
      }
    }
  }

  function pvSection(sec, kids, L) {
    const legend = h("legend", null, lt(sec.label, L));
    if (!pvVisible(sec)) {
      return h("div", { class: "pv-hidden" }, "§ " + lt(sec.label, L) + " – " + T("hidden until its condition is met"));
    }
    if (!sec.repeat) {
      return h("fieldset", { class: "pv-section" }, legend, kids.map((k) => k.type === "heading" ? h("h3", { class: "pv-heading" }, lt(k.label, L)) : pvField(k, L, pv.values, k.id)));
    }
    const low = Math.max(+sec.repeat.min || 0, 0), high = Math.max(+sec.repeat.max || 50, 1);
    if (pv.rows[sec.id] == null) pv.rows[sec.id] = Math.max(low, 1);
    const count = Math.min(Math.max(pv.rows[sec.id], low), high);
    const rows = [];
    for (let n = 0; n < count; n++) {
      pv.values[sec.id] = pv.values[sec.id] || [];
      const rowVals = pv.values[sec.id][n] = pv.values[sec.id][n] || {};
      rows.push(h("div", { class: "pv-row" },
        h("div", { class: "pv-row-head" }, h("strong", null, lt(sec.label, L) + " " + (n + 1)),
          count > low ? h("button", { type: "button", class: "link", onclick: () => { pv.values[sec.id].splice(n, 1); pv.rows[sec.id] = count - 1; renderPreview(); } }, L === "de" ? "Entfernen" : "Remove") : null),
        kids.map((k) => k.type === "heading" ? h("h3", { class: "pv-heading" }, lt(k.label, L)) : pvField(k, L, rowVals, sec.id + "." + n + "." + k.id))));
    }
    const addText = lt(sec.repeat.add_label, L) || (L === "de" ? "Weiteren Eintrag hinzufügen" : "Add another");
    return h("fieldset", { class: "pv-section pv-repeat" }, legend, rows,
      count < high ? h("button", { type: "button", class: "small", onclick: () => { pv.rows[sec.id] = count + 1; renderPreview(); } }, "+ " + addText) : null,
      h("p", { class: "hint" }, fmt(T("{min} to {max} times"), { min: low, max: high })));
  }

  function pvField(f, L, store, key) {
    const id = "pv-" + key.replace(/[^a-z0-9_]/gi, "-");
    const label = lt(f.label, L) || f.id;
    const req = f.required ? h("span", { class: "pv-req", "aria-hidden": "true" }, " *") : null;
    const help = lt(f.help, L) ? h("span", { class: "hint" }, lt(f.help, L)) : null;
    const set = (v, redraw) => { store[f.id] = v; if (redraw) renderPreview(); };
    const val = store[f.id] !== undefined ? store[f.id] : f.default;
    const dk = { key: "pv:" + key };
    let control;
    switch (f.type) {
      case "longtext":
        control = h("textarea", { id, rows: 3, maxlength: f.max_length, dataset: dk, value: val || "", oninput: (e) => set(e.target.value) });
        break;
      case "number":
        control = h("input", { id, type: "number", min: f.min, max: f.max, dataset: dk, value: val ?? "", oninput: (e) => set(e.target.value) });
        break;
      case "date":
        control = h("input", { id, type: "date", dataset: dk, value: val || "", oninput: (e) => set(e.target.value) });
        break;
      case "checkbox": {
        const cb = h("input", { id, type: "checkbox", checked: !!val, dataset: dk, onchange: (e) => set(e.target.checked, true) });
        return h("div", { class: "pv-field pv-check" }, h("label", { class: "check", for: id }, cb, " ", label, req), help);
      }
      case "dropdown":
        control = h("select", { id, dataset: dk, onchange: (e) => set(e.target.value, true) },
          opt("", L === "de" ? "– bitte wählen –" : "– please choose –"), choicesFor(f, L).map((c) => opt(c.value, c.label, c.value === val)));
        if (val) control.value = val;
        break;
      case "multichoice": {
        const chosen = Array.isArray(val) ? val : [];
        const ch = choicesFor(f, L);
        return h("fieldset", { class: "pv-field pv-multi" }, h("legend", null, label, req),
          ch.length ? ch.map((c) => h("label", { class: "check" }, h("input", { type: "checkbox", checked: chosen.includes(c.value),
            onchange: (e) => set(e.target.checked ? chosen.concat([c.value]) : chosen.filter((x) => x !== c.value), true) }), " ", c.label))
            : h("span", { class: "hint" }, T("No entries in the list yet.")), help);
      }
      case "email":
        control = h("input", { id, type: "email", dataset: dk, value: val || "", placeholder: "name@company.de", oninput: (e) => set(e.target.value) });
        break;
      case "person":
        control = h("input", { id, type: "text", list: "ad-users", dataset: dk, value: val || "", placeholder: L === "de" ? "Name oder E-Mail" : "Name or email", oninput: (e) => set(e.target.value) });
        break;
      case "secret":
        control = h("input", { id, type: "password", autocomplete: "off", maxlength: f.max_length, dataset: dk, value: val || "", oninput: (e) => set(e.target.value) });
        break;
      case "signature":
        control = h("div", { id, class: "pv-signature", role: "img", "aria-label": label }, L === "de" ? "Hier mit Maus, Finger oder Stift unterschreiben" : "Sign here with mouse, finger or pen");
        break;
      default:
        control = h("input", { id, type: "text", maxlength: f.max_length, dataset: dk, value: val || "", oninput: (e) => set(e.target.value) });
    }
    return h("div", { class: "pv-field" }, h("label", { for: id }, label, req), control, help);
  }

  function choicesFor(f, L) {
    if (f.list) { const l = D.lists.find((x) => x.name === f.list); return l ? l.items.map((i) => ({ value: i.value, label: i.label })) : []; }
    return (f.options || []).filter((o) => String(o.value || "").trim()).map((o) => ({ value: String(o.value), label: lt(o.label, L) || String(o.value) }));
  }

  // ------------------------------------------------------------ page glue

  jsonBox.addEventListener("change", () => {
    let parsed;
    try { parsed = JSON.parse(jsonBox.value); } catch (e) { jsonBox.setCustomValidity(T("The JSON is not valid:") + " " + e.message); jsonBox.reportValidity(); return; }
    jsonBox.setCustomValidity("");
    def = parsed;
    shape();
    ui.open.clear();
    changed(true);
  });

  form.addEventListener("submit", (e) => {
    if (e.submitter && e.submitter.dataset.publish && problemCount() > 0 &&
        !window.confirm(T("There are still problems. Save the draft and try to publish anyway? Publishing will be refused until they are fixed."))) {
      e.preventDefault();
      return;
    }
    syncJson();
    tabInput.value = ui.tab;
    ui.dirty = false;
  });

  document.addEventListener("click", (e) => {
    const c = e.target.closest("[data-confirm]");
    if (c && !window.confirm(c.dataset.confirm)) e.preventDefault();
    const link = e.target.closest("[data-tab-link]");
    if (link) { e.preventDefault(); showTab(link.dataset.tabLink); }
    const exp = e.target.closest("[data-export]");
    if (exp && ui.dirty && !window.confirm(T("Export takes the saved draft. Your latest changes are not saved yet. Export anyway?"))) e.preventDefault();
  });

  window.addEventListener("beforeunload", (e) => { if (ui.dirty) { e.preventDefault(); e.returnValue = ""; } });
  window.addEventListener("hashchange", () => { const t = location.hash.slice(1); if (["form", "steps", "check"].includes(t) && t !== ui.tab) showTab(t); });

  shape();
  const startTab = location.hash.slice(1);
  if (["form", "steps", "check"].includes(startTab)) ui.tab = startTab;
  tabInput.value = ui.tab;
  setProblems(D.problems, def.fields.slice(), def.steps.slice());
  problems.count = D.problems.length;
  render();
  syncJson();
  if (unsavedStamp) unsavedStamp.hidden = true;
})();
