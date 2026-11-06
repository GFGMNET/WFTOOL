/* WFTOOL signature pad: draw with mouse, finger or pen; sent as a PNG with the Sign button. */
(function () {
  "use strict";
  var canvas = document.getElementById("pad");
  var form = document.getElementById("task-form");
  if (!canvas || !form) return;
  var input = document.getElementById("signature");
  var error = document.getElementById("pad-error");
  var ctx = canvas.getContext("2d");
  var drawing = false, strokes = 0, last = null;

  function setup() {
    var ratio = Math.max(window.devicePixelRatio || 1, 1);
    var width = canvas.clientWidth || 600, height = canvas.clientHeight || 200;
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.lineWidth = 2.2;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.strokeStyle = "#1D2633";
    strokes = 0;
  }

  function point(e) {
    var r = canvas.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }

  canvas.addEventListener("pointerdown", function (e) {
    e.preventDefault();
    canvas.setPointerCapture(e.pointerId);
    drawing = true;
    last = point(e);
    ctx.beginPath();
    ctx.arc(last.x, last.y, 1, 0, Math.PI * 2);
    ctx.fillStyle = "#1D2633";
    ctx.fill();
  });
  canvas.addEventListener("pointermove", function (e) {
    if (!drawing) return;
    var p = point(e);
    ctx.beginPath();
    ctx.moveTo(last.x, last.y);
    ctx.lineTo(p.x, p.y);
    ctx.stroke();
    last = p;
  });
  function end() { if (drawing) { drawing = false; strokes += 1; if (error) error.hidden = true; } }
  canvas.addEventListener("pointerup", end);
  canvas.addEventListener("pointercancel", end);
  canvas.addEventListener("pointerleave", end);

  document.getElementById("pad-clear").addEventListener("click", function () {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    strokes = 0;
    input.value = "";
  });

  form.addEventListener("submit", function (e) {
    var btn = e.submitter;
    if (!btn || !btn.hasAttribute("data-sign")) return;
    if (!strokes) {
      e.preventDefault();
      if (error) error.hidden = false;
      canvas.focus();
      return;
    }
    // white background, so the signature is visible in every PDF viewer
    var out = document.createElement("canvas");
    out.width = canvas.width; out.height = canvas.height;
    var o = out.getContext("2d");
    o.fillStyle = "#FFFFFF"; o.fillRect(0, 0, out.width, out.height);
    o.drawImage(canvas, 0, 0);
    input.value = out.toDataURL("image/png");
  });

  setup();
  window.addEventListener("resize", function () { if (!strokes) setup(); });
})();
