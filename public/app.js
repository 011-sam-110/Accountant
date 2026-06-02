/* MTD app — small client helpers. Core interactions are server-rendered via
   HTMX; this file only covers what must run on the device:
   image compression, the direct-upload->scan flow, the amount number pad,
   undo toasts, and the iOS add-to-home-screen check. No framework required. */
(function () {
  "use strict";
  const MTD = (window.MTD = window.MTD || {});

  MTD.csrf = function () {
    const m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.getAttribute("content") : "";
  };

  /* Every HTMX request carries the CSRF token (double-submit cookie). */
  document.body && document.addEventListener("htmx:configRequest", function (e) {
    e.detail.headers["X-CSRF-Token"] = MTD.csrf();
  });

  /* ---- client-side downscale/compress: ~1500px longest edge, JPEG ~0.7 ---- */
  MTD.compressImage = function (file, maxEdge, quality) {
    maxEdge = maxEdge || 1500;
    quality = quality || 0.7;
    return new Promise(function (resolve, reject) {
      const img = new Image();
      const url = URL.createObjectURL(file);
      img.onload = function () {
        URL.revokeObjectURL(url);
        let { width, height } = img;
        const scale = Math.min(1, maxEdge / Math.max(width, height));
        width = Math.round(width * scale);
        height = Math.round(height * scale);
        const c = document.createElement("canvas");
        c.width = width; c.height = height;
        c.getContext("2d").drawImage(img, 0, 0, width, height);
        c.toBlob(function (blob) {
          resolve(blob || file);
        }, "image/jpeg", quality);
      };
      img.onerror = function () { URL.revokeObjectURL(url); resolve(file); };
      img.src = url;
    });
  };

  /* ---- upload flow: sign -> PUT bytes -> resolve storage key ---- */
  MTD.uploadReceipt = async function (file) {
    const blob = await MTD.compressImage(file);
    const signRes = await fetch("/api/capture/sign", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": MTD.csrf() },
      body: JSON.stringify({ content_type: "image/jpeg" }),
    });
    if (!signRes.ok) throw new Error("sign failed");
    const sign = await signRes.json();
    const headers = Object.assign({ "Content-Type": "image/jpeg" }, sign.headers || {});
    // Same-origin (local storage) PUT is CSRF-protected; R2 presigned PUT is not.
    if ((sign.url || "").startsWith("/")) headers["X-CSRF-Token"] = MTD.csrf();
    const put = await fetch(sign.url, {
      method: sign.method || "PUT",
      headers: headers,
      body: blob,
    });
    if (!put.ok) throw new Error("upload failed");
    return sign.key;
  };

  /* Wired from the Scan button: upload, then HTMX-swap the confirm screen. */
  MTD.scan = function (input) {
    const file = input.files && input.files[0];
    if (!file) return;
    const ind = document.getElementById("scanning");
    if (ind) ind.style.display = "flex";
    MTD.uploadReceipt(file)
      .then(function (key) {
        window.htmx && htmx.ajax("POST", "/scan", {
          target: "#screen", swap: "innerHTML", values: { key: key },
        });
      })
      .catch(function () {
        /* Gentle failure: still reach the confirm screen, just empty. */
        window.htmx && htmx.ajax("POST", "/scan", {
          target: "#screen", swap: "innerHTML", values: { key: "", failed: "1" },
        });
      })
      .finally(function () { if (ind) ind.style.display = "none"; });
  };

  MTD.isStandalone = function () {
    return window.navigator.standalone === true ||
      window.matchMedia("(display-mode: standalone)").matches;
  };

  /* ---- amount number pad: works on any [data-numpad] container ----
     Buttons inside with data-key="0..9|.|back" update the target display
     ([data-amount-display]) and a hidden input ([data-amount-input]). */
  MTD.numpadPress = function (btn) {
    const pad = btn.closest("[data-numpad]");
    if (!pad) return;
    const hidden = pad.querySelector("[data-amount-input]");
    const disp = pad.querySelector("[data-amount-display]");
    let v = hidden.value || "";
    const key = btn.getAttribute("data-key");
    if (key === "back") v = v.slice(0, -1);
    else if (key === ".") { if (!v.includes(".")) v = (v || "0") + "."; }
    else {
      if (v.includes(".") && v.split(".")[1].length >= 2) return; // 2 dp max
      v = (v === "0" ? "" : v) + key;
    }
    hidden.value = v;
    disp.textContent = "£" + (v === "" ? "0" : v);
  };

  /* ---- undo / flash toasts auto-dismiss ---- */
  MTD.initToasts = function (root) {
    (root || document).querySelectorAll(".toast[data-autohide]").forEach(function (t) {
      setTimeout(function () { t.style.display = "none"; }, 7000);
    });
  };
  document.addEventListener("htmx:afterSwap", function (e) { MTD.initToasts(e.target); });
  document.addEventListener("DOMContentLoaded", function () { MTD.initToasts(); });
})();
