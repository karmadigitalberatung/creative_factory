// Laufende Jobs abfragen und die Seite neu laden, sobald sie fertig sind.
document.querySelectorAll("[data-poll]").forEach((el) => {
  const url = el.dataset.poll;
  const tick = async () => {
    try {
      const r = await fetch(url, { credentials: "same-origin" });
      const s = await r.json();
      if (!s.laeuft) return location.reload();
    } catch (e) { /* weiter versuchen */ }
    setTimeout(tick, 1500);
  };
  setTimeout(tick, 1500);
});

// Arbeitet im Hintergrund: Inhalt unscharf, darüber „Wird generiert“ mit dem, was gerade passiert.
function arbeitet(el, text) {
  if (!el || el.classList.contains("arbeitet")) return;
  el.classList.add("arbeitet");
  const o = document.createElement("div");
  o.className = "arbeitet-overlay";
  o.innerHTML = '<div class="ao"><span class="ao-punkte"><i></i><i></i><i></i></span><strong>Wird generiert</strong><span class="ao-text"></span></div>';
  o.querySelector(".ao-text").textContent = text || "";
  el.appendChild(o);
}
document.querySelectorAll("[data-arbeitet]").forEach((el) => arbeitet(el, el.dataset.arbeitet));

// Formulare, die lange dauern: Bereich als „Wird generiert“ markieren und Knöpfe sperren.
document.querySelectorAll("form[data-busy]").forEach((f) => {
  f.addEventListener("submit", () => {
    arbeitet(f.closest(".card") || f, f.dataset.busy);
    f.querySelectorAll("button").forEach((b) => (b.disabled = true));
  });
});

// Chat: Nachricht senden, Antwort anzeigen, bei Änderungen neu laden.
document.querySelectorAll(".chat").forEach((box) => {
  const form = box.querySelector(".chatform");
  if (!form) return;
  const list = box.querySelector(".messages");
  const add = (role, text) => {
    const d = document.createElement("div");
    d.className = "msg " + role;
    d.textContent = text;
    list.appendChild(d);
    list.scrollTop = list.scrollHeight;
    return d;
  };
  list.scrollTop = list.scrollHeight;
  const ta = form.querySelector("textarea");
  const datei = form.querySelector('input[type="file"]');
  const anhang = form.querySelector(".anhang");
  const zeigen = () => {
    if (!anhang) return;
    anhang.innerHTML = "";
    [...(datei.files || [])].forEach((f) => { const i = document.createElement("img"); i.src = URL.createObjectURL(f); anhang.appendChild(i); });
  };
  if (datei) datei.addEventListener("change", zeigen);
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) form.requestSubmit();
  });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = ta.value.trim();
    const files = datei ? [...datei.files] : [];
    if (!text && !files.length) return;
    const msg = add("du", text);
    if (files.length) {
      const span = document.createElement("span"); span.className = "msg-bilder";
      files.forEach((f) => { const i = document.createElement("img"); i.src = URL.createObjectURL(f); span.appendChild(i); });
      msg.prepend(span);
    }
    ta.value = "";
    const btn = form.querySelector("button");
    btn.disabled = true;
    const wait = add("claude", "…");
    wait.classList.add("busy");
    const body = new FormData();
    body.append("nachricht", text);
    if (box.dataset.post) body.append("post_id", box.dataset.post);
    body.append("bereich", box.dataset.bereich || "post");
    files.forEach((f) => body.append("bilder", f));
    if (datei) { datei.value = ""; zeigen(); }
    if (files.length) wait.textContent = "Schaut sich die Bilder an …";
    try {
      const r = await fetch("/chat", { method: "POST", body, credentials: "same-origin" });
      const s = await r.json();
      wait.classList.remove("busy");
      wait.textContent = s.antwort || "(keine Antwort)";
      if (s.geaendert) setTimeout(() => location.reload(), 900);
    } catch (err) {
      wait.classList.remove("busy");
      wait.textContent = "Fehler beim Senden. Bitte noch einmal versuchen.";
    }
    btn.disabled = false;
  });
});

// Slide-Editor: Feldbeschriftungen je nach Slide-Art, neue Slides hinzufügen.
const LABELS = { zeile1: "Zeile 1", zeile2: "Zeile 2", subline: "Subline (| = Umbruch)", lead: "Leitsatz", body: "Fließtext",
  text: "Text", titel: "Titel", punkte: "Punkte (einer pro Zeile)", quelle: "Quelle", zahl: "Zahl", nachsatz: "Nachsatz oder Frage",
  farben: "Farben (je Zeile: Name #RRGGBB)" };
function setupSlide(el) {
  const sel = el.querySelector("select.art");
  const apply = () => {
    const f = (window.FELDER || {})[sel.value] || [];
    ["a", "b", "c", "liste"].forEach((k, i) => {
      const lab = el.querySelector(`[data-f="${k}"]`);
      if (!lab) return;
      lab.style.display = f[i] ? "" : "none";
      lab.querySelector("span").textContent = f[i] ? LABELS[f[i]] || f[i] : "";
    });
  };
  sel.addEventListener("change", apply);
  apply();
}
document.querySelectorAll(".slide-edit").forEach(setupSlide);
const add = document.getElementById("addslide");
if (add) add.addEventListener("click", () => {
  const box = document.getElementById("slides");
  const i = box.querySelectorAll(".slide-edit").length;
  const html = document.getElementById("slidetpl").innerHTML.replaceAll("__i__", i);
  box.insertAdjacentHTML("beforeend", html);
  setupSlide(box.lastElementChild);
});

// Drag and Drop: Upload in der Mediathek (sofort hochladen) und „Person merken“ (Vorschau, dann absenden).
document.querySelectorAll("[data-drop]").forEach((form) => {
  const input = form.querySelector('input[type="file"]');
  const zone = form.classList.contains("drop") ? form : form.querySelector(".drop");
  const vorschau = form.querySelector(".drop-vorschau");
  const sofort = form.dataset.drop === "upload";
  form.querySelectorAll("[data-waehlen]").forEach((b) => b.addEventListener("click", () => input.click()));
  const hochladen = async () => {
    if (!input.files.length) return;
    zone.classList.add("ueber");
    zone.querySelector(".drop-text strong").textContent = `${input.files.length} Bild${input.files.length > 1 ? "er" : ""} werden hochgeladen …`;
    const body = new FormData(form);
    await fetch(form.action, { method: "POST", body, credentials: "same-origin", headers: { "x-requested-with": "fetch" } });
    location.reload();
  };
  const zeigen = () => {
    if (!vorschau) return;
    vorschau.innerHTML = "";
    [...input.files].slice(0, 3).forEach((f) => { const i = document.createElement("img"); i.src = URL.createObjectURL(f); vorschau.appendChild(i); });
  };
  input.addEventListener("change", () => (sofort ? hochladen() : zeigen()));
  ["dragenter", "dragover"].forEach((t) => zone.addEventListener(t, (e) => { e.preventDefault(); zone.classList.add("ueber"); }));
  ["dragleave", "drop"].forEach((t) => zone.addEventListener(t, (e) => { e.preventDefault(); zone.classList.remove("ueber"); }));
  zone.addEventListener("drop", (e) => {
    const dt = new DataTransfer();
    [...e.dataTransfer.files].filter((f) => f.type.startsWith("image/")).forEach((f) => dt.items.add(f));
    if (!dt.files.length) return;
    input.files = dt.files;
    sofort ? hochladen() : zeigen();
  });
  if (!sofort) form.addEventListener("submit", (e) => {
    if (input.files.length < 3) { e.preventDefault(); alert("Bitte genau 3 Fotos der Person hinzufügen."); }
  });
});
