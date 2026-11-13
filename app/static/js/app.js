/* Vehicle Transport Manager — small, dependency-free helpers. */
(function () {
  "use strict";
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  /* ---------- confirm before destructive actions ---------- */
  document.addEventListener("submit", (e) => {
    const f = e.target;
    if (f.dataset.confirm && !window.confirm(f.dataset.confirm)) { e.preventDefault(); return; }
    if (!f.dataset.upload) {
      // prevent double taps creating duplicates
      $$("button[type=submit]", f).forEach((b) => setTimeout(() => (b.disabled = true), 0));
      setTimeout(() => $$("button[type=submit]", f).forEach((b) => (b.disabled = false)), 8000);
    }
  }, true);

  /* ---------- filter forms: apply as soon as a dropdown or date changes ---------- */
  $$("form[data-autosubmit]").forEach((form) => {
    form.addEventListener("change", (e) => {
      if (e.target.matches("select, input[type=date]")) form.requestSubmit ? form.requestSubmit() : form.submit();
    });
  });

  /* ---------- GPS: fill hidden lat/lng on forms with data-geo ---------- */
  $$("form[data-geo]").forEach((form) => {
    const status = $("[data-geo-status]", form);
    if (!("geolocation" in navigator)) { if (status) status.textContent = "Location not available on this device."; return; }
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        $("input[name=lat]", form).value = pos.coords.latitude.toFixed(6);
        $("input[name=lng]", form).value = pos.coords.longitude.toFixed(6);
        if (status) status.innerHTML = '<i class="bi bi-geo-alt-fill text-success"></i> Location captured';
      },
      () => { if (status) status.innerHTML = '<i class="bi bi-geo-alt text-muted"></i> Location not shared'; },
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 }
    );
  });

  /* ---------- signature pad ---------- */
  $$("[data-sigpad]").forEach((wrap) => {
    const canvas = $("canvas", wrap);
    const input = $("input[type=hidden]", wrap.parentElement);
    const hint = $(".hint", wrap);
    const ctx = canvas.getContext("2d");
    let drawing = false, dirty = false, last = null;
    function size() {
      const r = canvas.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
      const data = dirty ? canvas.toDataURL() : null;
      canvas.width = r.width * dpr; canvas.height = r.height * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.lineWidth = 2.4; ctx.lineCap = "round"; ctx.lineJoin = "round"; ctx.strokeStyle = "#0b1f3a";
      if (data) { const img = new Image(); img.onload = () => ctx.drawImage(img, 0, 0, r.width, r.height); img.src = data; }
    }
    function pt(e) { const r = canvas.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; }
    canvas.addEventListener("pointerdown", (e) => { drawing = true; last = pt(e); canvas.setPointerCapture(e.pointerId); if (hint) hint.style.display = "none"; });
    canvas.addEventListener("pointermove", (e) => {
      if (!drawing) return;
      const p = pt(e); ctx.beginPath(); ctx.moveTo(last.x, last.y); ctx.lineTo(p.x, p.y); ctx.stroke(); last = p; dirty = true;
    });
    const end = () => { if (drawing && dirty) input.value = canvas.toDataURL("image/png"); drawing = false; };
    canvas.addEventListener("pointerup", end); canvas.addEventListener("pointercancel", end);
    const clear = $("[data-sig-clear]", wrap.parentElement);
    if (clear) clear.addEventListener("click", () => { ctx.clearRect(0, 0, canvas.width, canvas.height); input.value = ""; dirty = false; if (hint) hint.style.display = ""; });
    window.addEventListener("resize", size); size();
  });

  /* ---------- photo picker with previews + client-side resize ---------- */
  const MAX_PX = 2000;
  function resize(file) {
    return new Promise((resolve) => {
      if (!file.type.startsWith("image/") || file.type === "image/gif") return resolve(file);
      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onload = () => {
        let { width: w, height: h } = img;
        const scale = Math.min(1, MAX_PX / Math.max(w, h));
        w = Math.round(w * scale); h = Math.round(h * scale);
        const c = document.createElement("canvas"); c.width = w; c.height = h;
        c.getContext("2d").drawImage(img, 0, 0, w, h);
        URL.revokeObjectURL(url);
        c.toBlob((b) => resolve(b ? new File([b], (file.name || "photo").replace(/\.\w+$/, "") + ".jpg", { type: "image/jpeg" }) : file), "image/jpeg", 0.85);
      };
      img.onerror = () => { URL.revokeObjectURL(url); resolve(file); };
      img.src = url;
    });
  }

  $$("form[data-upload]").forEach((form) => {
    const picked = []; // File[]
    const grid = $("[data-photo-grid]", form);
    const counter = $("[data-photo-count]", form);
    const progress = $("[data-progress]", form);
    const fieldName = form.dataset.upload; // name the server expects, e.g. "photos"

    function render() {
      if (!grid) return;
      grid.innerHTML = "";
      picked.forEach((f, i) => {
        const div = document.createElement("div"); div.className = "item";
        const img = document.createElement("img"); img.alt = "Photo " + (i + 1);
        img.src = URL.createObjectURL(f); img.onload = () => URL.revokeObjectURL(img.src);
        const btn = document.createElement("button"); btn.type = "button"; btn.innerHTML = "&times;"; btn.title = "Remove";
        btn.onclick = () => { picked.splice(i, 1); render(); };
        div.append(img, btn); grid.append(div);
      });
      if (counter) counter.textContent = picked.length ? picked.length + " photo" + (picked.length > 1 ? "s" : "") : "No photos yet";
    }
    $$("input[type=file][data-photos]", form).forEach((inp) => {
      inp.addEventListener("change", () => { Array.from(inp.files).forEach((f) => picked.push(f)); inp.value = ""; render(); });
    });
    render();

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const btns = $$("button[type=submit]", form); btns.forEach((b) => (b.disabled = true));
      const fd = new FormData(form);
      $$("input[type=file][data-photos]", form).forEach((inp) => fd.delete(inp.name));
      if (progress) { progress.classList.remove("d-none"); progress.querySelector(".label").textContent = "Preparing photos…"; }
      for (const f of picked) fd.append(fieldName, await resize(f));
      const xhr = new XMLHttpRequest();
      xhr.open("POST", form.action || window.location.href);
      xhr.setRequestHeader("X-Requested-With", "XMLHttpRequest");
      xhr.upload.onprogress = (ev) => {
        if (!progress || !ev.lengthComputable) return;
        const pct = Math.round((ev.loaded / ev.total) * 100);
        progress.querySelector(".progress-bar").style.width = pct + "%";
        progress.querySelector(".label").textContent = "Uploading… " + pct + "%";
      };
      xhr.onload = () => {
        if (xhr.status < 400) {
          let target = xhr.responseURL || window.location.href;
          try { const j = JSON.parse(xhr.responseText); if (j.redirect) target = j.redirect; } catch (_) { /* HTML response */ }
          window.location.href = target; return;
        }
        btns.forEach((b) => (b.disabled = false));
        alert(xhr.status === 413 ? "Too much data at once — try fewer photos." : "Upload failed (" + xhr.status + "). Please try again.");
        if (progress) progress.classList.add("d-none");
      };
      xhr.onerror = () => {
        btns.forEach((b) => (b.disabled = false));
        if (progress) progress.classList.add("d-none");
        alert("No connection. Your photos are still here — tap save again when you have signal.");
      };
      xhr.send(fd);
    });
  });

  /* ---------- load form: vehicle rows + VIN decode ---------- */
  const vlist = $("[data-vehicles]");
  if (vlist) {
    const tpl = $("#vehicle-row-template");
    const renumber = () => $$(".vehicle-row", vlist).forEach((r, i) => ($(".vnum", r).textContent = i + 1));
    $("[data-add-vehicle]").addEventListener("click", () => {
      vlist.append(tpl.content.cloneNode(true)); renumber();
      const rows = $$(".vehicle-row", vlist); $("input[name=v_vin]", rows[rows.length - 1]).focus();
    });
    vlist.addEventListener("click", async (e) => {
      const row = e.target.closest(".vehicle-row");
      if (e.target.closest("[data-remove-vehicle]")) { row.remove(); renumber(); return; }
      const dec = e.target.closest("[data-decode-vin]");
      if (dec) {
        const vin = $("input[name=v_vin]", row).value.trim().toUpperCase();
        if (vin.length !== 17) { alert("Enter the full 17-character VIN first."); return; }
        dec.disabled = true; const old = dec.innerHTML; dec.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
        try {
          const r = await fetch("https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/" + encodeURIComponent(vin) + "?format=json");
          const d = (await r.json()).Results[0];
          if (d.ModelYear) $("input[name=v_year]", row).value = d.ModelYear;
          if (d.Make) $("input[name=v_make]", row).value = d.Make.charAt(0) + d.Make.slice(1).toLowerCase();
          if (d.Model) $("input[name=v_model]", row).value = d.Model;
          if (!d.Make) alert("Couldn't decode that VIN — double-check it.");
        } catch (err) { alert("VIN lookup failed (no connection?). Fill the vehicle in by hand."); }
        dec.disabled = false; dec.innerHTML = old;
      }
    });
  }

  /* ---------- new customer toggle on load form ---------- */
  const custSel = $("select[name=customer_id]");
  const newCust = $("[data-new-customer]");
  if (custSel && newCust) {
    const sync = () => newCust.classList.toggle("d-none", custSel.value !== "__new");
    custSel.addEventListener("change", sync); sync();
    custSel.form.addEventListener("submit", () => { if (custSel.value === "__new") custSel.value = ""; });
  }

  /* ---------- copy temp password ---------- */
  $$("[data-copy]").forEach((b) => b.addEventListener("click", () => {
    navigator.clipboard && navigator.clipboard.writeText(b.dataset.copy).then(() => { b.textContent = "Copied"; });
  }));
})();
