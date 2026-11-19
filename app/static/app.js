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
    arbeitet((f.dataset.busyZiel && document.querySelector(f.dataset.busyZiel)) || f.closest(".card") || f, f.dataset.busy);
    // auch Knöpfe außerhalb des Formulars, die per form="…" dazugehören
    setTimeout(() => [...f.elements].forEach((b) => { if (b.tagName === "BUTTON") b.disabled = true; }), 0);
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
    if (box.dataset.seite) body.append("seite", box.dataset.seite);   // wo die Person gerade ist
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
  // „Person merken“: Fotos sammeln sich über mehrere Auswahlen/Drops (höchstens 3), statt sich zu ersetzen.
  let gesammelt = [];
  const hinweis = form.querySelector(".drop-text strong");
  const zeigen = (neu) => {
    if (neu) {
      gesammelt = [...gesammelt, ...neu].slice(-3);
      const dt = new DataTransfer();
      gesammelt.forEach((f) => dt.items.add(f));
      input.files = dt.files;
    }
    zone.classList.remove("fehlt");
    if (hinweis) hinweis.textContent = gesammelt.length >= 3 ? "3 Fotos bereit" : `${gesammelt.length} von 3 Fotos – noch ${3 - gesammelt.length} hinzufügen`;
    if (!vorschau) return;
    vorschau.innerHTML = "";
    gesammelt.forEach((f) => { const i = document.createElement("img"); i.src = URL.createObjectURL(f); vorschau.appendChild(i); });
  };
  input.addEventListener("change", () => (sofort ? hochladen() : zeigen([...input.files])));
  ["dragenter", "dragover"].forEach((t) => zone.addEventListener(t, (e) => { e.preventDefault(); zone.classList.add("ueber"); }));
  ["dragleave", "drop"].forEach((t) => zone.addEventListener(t, (e) => { e.preventDefault(); zone.classList.remove("ueber"); }));
  zone.addEventListener("drop", (e) => {
    const dt = new DataTransfer();
    [...e.dataTransfer.files].filter((f) => f.type.startsWith("image/")).forEach((f) => dt.items.add(f));
    if (!dt.files.length) return;
    if (sofort) { input.files = dt.files; hochladen(); } else zeigen([...dt.files]);
  });
  if (!sofort) form.addEventListener("submit", (e) => {
    if (input.files.length < 3) {
      e.preventDefault();
      zone.classList.add("fehlt");
      if (hinweis) hinweis.textContent = `Bitte 3 Fotos hinzufügen (jetzt ${input.files.length})`;
      zone.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  });
});

// Planen: nach Format filtern und nach Titel suchen (nur Anzeige, ohne Neuladen).
document.querySelectorAll("[data-filter]").forEach((leiste) => {
  let format = "", status = leiste.dataset.statusStart || "", suche = "";
  const zahl = leiste.querySelector(".filter-zahl");
  const anwenden = () => {
    document.querySelectorAll(".board .ticket, .tabelle tbody tr[data-format], .cal-post").forEach((t) => {
      t.hidden = (format && t.dataset.format !== format) || (status && t.dataset.status !== status) || (suche && !t.dataset.titel.includes(suche));
    });
    const n = [format, status, suche].filter(Boolean).length;
    if (zahl) { zahl.hidden = !n; zahl.textContent = n; }
    leiste.classList.toggle("aktiv", !!n);
  };
  const knoepfe = (attr, wert) => leiste.querySelectorAll(`[data-${attr}]`).forEach((x) => x.classList.toggle("on", x.dataset[attr] === wert));
  leiste.querySelectorAll("[data-format]").forEach((k) => k.addEventListener("click", () => { format = k.dataset.format; knoepfe("format", format); anwenden(); }));
  leiste.querySelectorAll("[data-status]").forEach((k) => k.addEventListener("click", () => { status = k.dataset.status; knoepfe("status", status); anwenden(); }));
  const feld = leiste.querySelector("[data-suche]");
  if (feld) feld.addEventListener("input", () => { suche = feld.value.trim().toLowerCase(); anwenden(); });
  leiste.querySelector("[data-filter-weg]")?.addEventListener("click", () => {
    format = status = suche = ""; if (feld) feld.value = ""; knoepfe("format", ""); knoepfe("status", ""); anwenden();
  });
  if (status) { knoepfe("status", status); anwenden(); }
});

// Aufklapp-Knöpfe (Filter, Redaktion): Klick daneben oder Escape schließt sie
document.addEventListener("click", (e) => {
  document.querySelectorAll("details.aufklapp[open]").forEach((d) => { if (!d.contains(e.target)) d.open = false; });
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") document.querySelectorAll("details.aufklapp[open]").forEach((d) => { d.open = false; });
});

// Bild-Prompt: Personen mit @Name erwähnen (Vorschlagsliste beim Tippen, erkannte Personen darunter, höchstens 3).
document.querySelectorAll("textarea[data-erwaehnen]").forEach((feld) => {
  const form = feld.closest("[data-erwaehnen-wurzel]") || feld.closest("form");
  const personen = [...(form?.querySelectorAll(".erwaehnen-personen li") || [])].map((li) => ({ name: li.dataset.name, bild: li.dataset.bild }));
  if (!personen.length) return;
  const MAX = 3;
  const huelle = document.createElement("div");
  huelle.className = "erwaehnen";
  feld.replaceWith(huelle);
  huelle.appendChild(feld);
  const menue = document.createElement("div");
  menue.className = "erwaehnen-menue"; menue.hidden = true;
  const chips = document.createElement("div");
  chips.className = "erwaehnen-chips";
  huelle.append(menue, chips);
  const nachLaenge = [...personen].sort((a, b) => b.name.length - a.name.length);
  const bildTag = (p) => (p.bild && !p.bild.endsWith("/media/") ? `<img src="${p.bild}" alt="">` : `<span class="ohne">${p.name[0] || "?"}</span>`);
  const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  // gleiche Regel wie auf dem Server: längster passender Name nach @
  const erkannt = () => {
    const t = feld.value, out = [];
    for (let i = t.indexOf("@"); i !== -1; i = t.indexOf("@", i + 1)) {
      const rest = t.slice(i + 1);
      const p = nachLaenge.find((p) => rest.slice(0, p.name.length).toLowerCase() === p.name.toLowerCase() && !/[\p{L}\p{N}_]/u.test(rest[p.name.length] || ""));
      if (p && !out.includes(p)) out.push(p);
    }
    return out;
  };
  const chipsZeigen = () => {
    const e = erkannt();
    chips.innerHTML = e.map((p, i) => `<span class="chip ${i >= MAX ? "zuviel" : ""}">${bildTag(p)}${esc(p.name)}</span>`).join("") +
      (e.length > MAX ? `<span class="hint warn">Höchstens ${MAX} Personen – ${e.slice(MAX).map((p) => esc(p.name)).join(", ")} bleibt außen vor.</span>` : "");
  };

  let treffer = [], aktiv = 0, start = -1;
  const anfrage = () => {   // „@abc“ direkt vor dem Cursor
    const vor = feld.value.slice(0, feld.selectionStart);
    const m = vor.match(/(^|[\s(„"'])@([^@\n]{0,30})$/);
    return m ? { start: vor.length - m[2].length - 1, text: m[2] } : null;
  };
  const schliessen = () => { menue.hidden = true; treffer = []; };
  const menueZeigen = () => {
    const a = anfrage();
    if (!a) return schliessen();
    treffer = personen.filter((p) => p.name.toLowerCase().startsWith(a.text.toLowerCase()));
    if (!treffer.length) return schliessen();
    start = a.start; aktiv = Math.min(aktiv, treffer.length - 1);
    menue.innerHTML = treffer.map((p, i) => `<button type="button" class="${i === aktiv ? "on" : ""}" data-i="${i}">${bildTag(p)}${esc(p.name)}</button>`).join("");
    menue.hidden = false;
  };
  const einsetzen = (p) => {
    const ende = feld.selectionStart, t = feld.value;
    const danach = t.slice(ende).startsWith(" ") ? "" : " ";
    feld.value = t.slice(0, start) + "@" + p.name + danach + t.slice(ende);
    const pos = start + 1 + p.name.length + danach.length;
    feld.setSelectionRange(pos, pos);
    feld.focus(); schliessen(); chipsZeigen();
  };
  feld.addEventListener("input", () => { aktiv = 0; menueZeigen(); chipsZeigen(); });
  feld.addEventListener("click", menueZeigen);
  feld.addEventListener("keydown", (e) => {
    if (menue.hidden) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); aktiv = (aktiv + (e.key === "ArrowDown" ? 1 : -1) + treffer.length) % treffer.length; menueZeigen(); }
    else if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); einsetzen(treffer[aktiv]); }
    else if (e.key === "Escape") { e.preventDefault(); schliessen(); }
  });
  menue.addEventListener("mousedown", (e) => { const b = e.target.closest("button"); if (b) { e.preventDefault(); einsetzen(treffer[+b.dataset.i]); } });
  feld.addEventListener("blur", () => setTimeout(schliessen, 100));
  chipsZeigen();
});

// Post-Detail: Reiter für die Captions, Zeichenzähler, Chat als Seitenfenster, Warnung vor „Neu gestalten“.
document.querySelectorAll(".reiter").forEach((leiste) => {
  const box = leiste.parentElement;
  leiste.querySelectorAll("[data-reiter]").forEach((k) => k.addEventListener("click", () => {
    leiste.querySelectorAll("[data-reiter]").forEach((x) => x.classList.toggle("on", x === k));
    box.querySelectorAll(":scope > [data-reiter-inhalt]").forEach((x) => (x.hidden = x.dataset.reiterInhalt !== k.dataset.reiter));
  }));
});
document.querySelectorAll("textarea[data-max]").forEach((t) => {
  const z = t.parentElement.querySelector(".zaehler");
  if (!z) return;
  const max = +t.dataset.max;
  const zeigen = () => { z.textContent = `${t.value.length} / ${max} Zeichen`; z.classList.toggle("zuviel", t.value.length > max); };
  t.addEventListener("input", zeigen); zeigen();
});
(() => {
  const schublade = document.querySelector(".chat-schublade");
  if (!schublade) return;
  const key = "chat-offen";   // offen bleibt offen, auch beim Wechsel der Seite (der Inhalt passt sich der Seite an)
  const merken = (v) => { try { v ? sessionStorage.setItem(key, "1") : sessionStorage.removeItem(key); } catch (e) {} };
  const auf = () => { schublade.hidden = false; merken(true); if (document.querySelector(".chat-fab")) document.querySelector(".chat-fab").classList.add("offen"); const t = schublade.querySelector("textarea"); if (t) t.focus(); const m = schublade.querySelector(".messages"); if (m) m.scrollTop = m.scrollHeight; };
  const fab = document.querySelector(".chat-fab");
  const zu = () => { schublade.hidden = true; merken(false); if (fab) fab.classList.remove("offen"); };
  document.querySelectorAll("[data-chat-auf]").forEach((b) => b.addEventListener("click", () => (schublade.hidden ? auf() : zu())));
  schublade.querySelector("[data-chat-zu]").addEventListener("click", zu);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !schublade.hidden && !document.querySelector(".le:not([hidden])")) zu(); });
  try { if (sessionStorage.getItem(key)) auf(); } catch (e) {}
})();
document.querySelectorAll("button[data-warnen]").forEach((b) => b.addEventListener("click", (e) => { if (!confirm(b.dataset.warnen)) e.preventDefault(); }));

// Layout-Editor: eine Slide groß, Elemente verschieben, Breite ziehen, Größe, Ausrichtung, Text, Ebenen.
// Jede Änderung wird sofort echt gerendert (Server, ca. 50 ms), gespeichert wird erst mit „Speichern“.
(() => {
  const knoepfe = document.querySelectorAll("[data-layout]");
  if (!knoepfe.length) return;
  const PID = window.POST_ID;
  const BASIS = window.LAYOUT_BASIS || `/planen/${PID}/layout`, BILD = window.BILD_BASIS || `/planen/${PID}/bild`;
  const TYP = { text: "Text", stapel: "Textblock", liste: "Liste", foto: "Foto", form: "Form", logo: "Logo", farbfelder: "Farbfelder", verlauf: "Verlauf" };
  const hatHoehe = (typ) => ["foto", "form", "verlauf"].includes(typ);
  const RAND = 96, FANG = 10;
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const kopie = (o) => JSON.parse(JSON.stringify(o));
  const r1 = (v) => Math.round(v * 10) / 10;

  const le = document.createElement("div");
  le.className = "le"; le.hidden = true;
  le.innerHTML = `<div class="le-fenster" role="dialog" aria-modal="true" aria-label="Layout anpassen">
    <div class="le-kopf"><h2></h2><span class="le-status"></span>
      <span class="le-blaettern"><button type="button" class="small quadrat" data-folie="-1" aria-label="Vorherige Folie">‹</button><button type="button" class="small quadrat" data-folie="1" aria-label="Nächste Folie">›</button></span>
      <button type="button" class="small" data-undo title="Rückgängig (⌘Z)">↶</button>
      <button type="button" class="small" data-redo title="Wiederholen (⇧⌘Z)">↷</button>
      <button type="button" class="small" data-entwurf>Auf Entwurf zurücksetzen</button>
      <button type="button" class="small" data-abbrechen>Abbrechen</button>
      <button type="button" class="small primary" data-speichern>Speichern</button></div>
    <div class="le-buehne"><div class="le-flaeche"><img alt="Slide"></div></div>
    <div class="le-seite"></div></div>`;
  document.body.appendChild(le);
  const flaeche = le.querySelector(".le-flaeche"), bild = flaeche.querySelector("img"), seite = le.querySelector(".le-seite");
  const status = le.querySelector(".le-status");

  let S = null;   // {nr, gesamt, szene, elemente, W, H, manuell, aktiv, zurueck: [], vor: [], geaendert}
  let ziehen = null;

  const skala = () => flaeche.clientWidth / S.W;
  const el = (i) => S.szene.elemente[i];
  const boxVon = (i) => (S.elemente.find((x) => x.i === i) || {}).box;
  const markieren = (txt) => { status.textContent = txt; };

  function merken() {   // Zustand vor einer Änderung sichern
    S.zurueck.push(kopie(S.szene)); if (S.zurueck.length > 80) S.zurueck.shift();
    S.vor = []; S.geaendert = true; markieren("Nicht gespeichert");
  }

  let unterwegs = false, nochmal = false;
  async function vorschau() {
    if (unterwegs) { nochmal = true; return; }
    unterwegs = true;
    do {
      nochmal = false;
      try {
        const r = await fetch(`${BASIS}/${S.nr}/vorschau`, { method: "POST", credentials: "same-origin",
          headers: { "Content-Type": "application/json" }, body: JSON.stringify({ szene: S.szene }) });
        const d = await r.json();
        if (d.fehler) { markieren(d.fehler); break; }
        bild.src = d.bild; S.elemente = d.elemente; S.hg_ueber = d.hg_ueber;
        if (!ziehen) boxenZeichnen();
      } catch (e) { markieren("Vorschau fehlgeschlagen"); }
    } while (nochmal);
    unterwegs = false;
  }

  function boxenZeichnen() {
    flaeche.querySelectorAll(".le-box, .le-linie, .le-zuschnitt-leiste").forEach((x) => x.remove());
    const k = skala(), zs = S.zuschnitt ?? null;
    flaeche.classList.toggle("zuschnitt-hg", zs === "hg");
    flaeche.classList.toggle("im-zuschnitt", zs !== null);
    if (zs !== null) {
      const l = document.createElement("div");
      l.className = "le-zuschnitt-leiste";
      l.innerHTML = `<span>${zs === "hg" ? "Hintergrundbild" : "Foto"}: ziehen verschiebt das Bild, Mausrad zoomt</span><button type="button" class="small primary" data-zuschnitt-fertig>Fertig</button>`;
      flaeche.appendChild(l);
    }
    S.elemente.forEach(({ i, typ, box }) => {
      const d = document.createElement("div");
      d.className = "le-box" + (i === S.aktiv ? " aktiv" : "") + (i === zs ? " zuschnitt" : "");
      d.dataset.i = i;
      Object.assign(d.style, { left: box[0] * k + "px", top: box[1] * k + "px", width: Math.max(8, (box[2] - box[0]) * k) + "px", height: Math.max(8, (box[3] - box[1]) * k) + "px" });
      d.innerHTML = `<span class="typ">${TYP[typ] || typ}</span><span class="griff l" data-griff="l"></span><span class="griff r" data-griff="r"></span>` +
        (hatHoehe(typ) ? ["lo", "ro", "lu", "ru"].map((g) => `<span class="griff ${g}" data-griff="${g}"></span>`).join("") : "");
      flaeche.appendChild(d);
    });
  }

  function waehlen(i) { if (S.zuschnitt !== undefined && S.zuschnitt !== i) S.zuschnitt = null; S.aktiv = i; boxenZeichnen(); seiteZeichnen(); }

  // ---------- Zuschnitt: Bild im Rahmen (oder Hintergrundbild) verschieben und zoomen
  const zuschnittZiel = () => S.zuschnitt === "hg" ? S.szene.hintergrund : S.zuschnitt !== null && S.zuschnitt !== undefined ? el(S.zuschnitt) : null;
  const zuschnittUeber = (z) => (z === "hg" ? S.hg_ueber : (S.elemente.find((x) => x.i === z) || {}).ueber) || [0, 0];
  function zuschnitt(z) {
    inlineZu();
    if (z !== null && z !== "hg" && S.aktiv !== z) S.aktiv = z;
    if (z === "hg") S.aktiv = null;
    S.zuschnitt = z; boxenZeichnen(); seiteZeichnen();
  }
  const zoomGrenze = (v) => Math.max(1, Math.min(4, Math.round(v * 100) / 100));
  let radMerken = null;
  flaeche.addEventListener("wheel", (ev) => {
    const o = S && zuschnittZiel();
    if (!o) return;
    ev.preventDefault();
    if (!radMerken) merken();
    clearTimeout(radMerken); radMerken = setTimeout(() => (radMerken = null), 600);
    o.zoom = zoomGrenze((o.zoom || 1) * Math.exp(-ev.deltaY * 0.0015));
    const r = seite.querySelector("[data-zoom]"); if (r) { r.value = Math.round(o.zoom * 100); seite.querySelector("[data-zoomwert]").textContent = r.value + " %"; }
    vorschau();
  }, { passive: false });
  flaeche.addEventListener("click", (ev) => { if (ev.target.closest("[data-zuschnitt-fertig]")) zuschnitt(null); });

  // ---------- Ziehen: verschieben oder Breite ändern, mit Hilfslinien
  function fangen(b, dx, dy, ohne) {
    const linien = [];
    if (ohne) return { dx, dy, linien };
    const xs = [RAND, S.W / 2, S.W - RAND], ys = [RAND, S.H / 2, S.H - RAND];
    S.elemente.forEach((o) => { if (o.i !== ziehen.i) { xs.push(o.box[0], o.box[2]); ys.push(o.box[1], o.box[3]); } });
    const pruefen = (werte, kanten, d) => {
      let best = null;
      kanten.forEach((kante) => werte.forEach((w) => { const diff = w - (kante + d); if (Math.abs(diff) < FANG && (!best || Math.abs(diff) < Math.abs(best.diff))) best = { diff, w }; }));
      return best;
    };
    const bx = pruefen(xs, [b[0], (b[0] + b[2]) / 2, b[2]], dx);
    const by = ziehen.griff ? null : pruefen(ys, [b[1], (b[1] + b[3]) / 2, b[3]], dy);
    if (bx) { dx += bx.diff; linien.push(["v", bx.w]); }
    if (by) { dy += by.diff; linien.push(["h", by.w]); }
    return { dx, dy, linien };
  }
  function linienZeigen(linien) {
    flaeche.querySelectorAll(".le-linie").forEach((x) => x.remove());
    const k = skala();
    linien.forEach(([art, w]) => { const l = document.createElement("div"); l.className = "le-linie " + art; l.style[art === "v" ? "left" : "top"] = w * k + "px"; flaeche.appendChild(l); });
  }

  // ---------- Text direkt auf der Slide ändern (Doppelklick oder „Text ändern“)
  let inline = null;
  function inlineZu() {
    if (!inline) return;
    const unveraendert = JSON.stringify(S.szene) === inline.vorher;
    inline.div.remove(); inline = null;
    if (unveraendert) { S.zurueck.pop(); if (!S.zurueck.length) { S.geaendert = false; markieren(""); } }
    seiteZeichnen();
  }
  function textBearbeiten(i) {
    inlineZu();
    const info = S.elemente.find((x) => x.i === i);
    const texte = ((info && info.texte) || []);
    if (!texte.length) return;
    if (S.aktiv !== i) waehlen(i);
    const e = el(i), k = skala(), b = info.box;
    const d = document.createElement("div");
    d.className = "le-inline";
    const breite = Math.min(flaeche.clientWidth - 16, Math.max(280, (b[2] - b[0]) * k));
    Object.assign(d.style, { width: breite + "px", left: Math.max(8, Math.min(b[0] * k, flaeche.clientWidth - breite - 8)) + "px" });
    d.innerHTML = texte.map((t) => `<textarea rows="${Math.min(5, t.wert.split("\n").length + 1)}" data-text="${t.pfad}">${esc(textVon(e, t.pfad))}</textarea>`).join("") +
      `<div class="le-inline-fuss"><span class="hint">Enter = neue Zeile${e.typ === "liste" ? " (bei Listen: neuer Punkt)" : ""} · ⌘Enter = fertig</span><button type="button" class="small primary" data-ok>Fertig</button></div>`;
    flaeche.appendChild(d);
    const h = d.offsetHeight;
    let top = b[3] * k + 8;
    if (top + h > flaeche.clientHeight) top = b[1] * k - h - 8;
    d.style.top = Math.max(8, Math.min(top, flaeche.clientHeight - h - 8)) + "px";
    merken();
    inline = { div: d, vorher: JSON.stringify(S.zurueck[S.zurueck.length - 1]) };
    d.querySelectorAll("[data-text]").forEach((t) => {
      t.addEventListener("input", () => { textSetzen(e, t.dataset.text, t.value); vorschau(); });
      t.addEventListener("keydown", (ev) => { if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) { ev.preventDefault(); inlineZu(); } });
    });
    d.querySelector("[data-ok]").addEventListener("click", inlineZu);
    const erstes = d.querySelector("[data-text]");
    if (erstes) { erstes.focus(); erstes.setSelectionRange(erstes.value.length, erstes.value.length); }
  }
  flaeche.addEventListener("dblclick", (ev) => {   // Ziel über die Position bestimmen: beim Klicken hält die Fläche den Zeiger fest
    if (ev.target.closest(".le-inline")) return;
    if (ev.target.closest(".le-zuschnitt-leiste")) return;
    const box = document.elementsFromPoint(ev.clientX, ev.clientY).find((x) => x.classList && x.classList.contains("le-box"));
    if (box && el(+box.dataset.i).typ === "foto") zuschnitt(+box.dataset.i);
    else if (box) textBearbeiten(+box.dataset.i);
    else if ("foto" in (S.szene.hintergrund || {})) zuschnitt("hg");
  });

  flaeche.addEventListener("pointerdown", (ev) => {
    if (ev.target.closest(".le-inline, .le-zuschnitt-leiste")) return;
    if (S.zuschnitt !== null && S.zuschnitt !== undefined) {
      const z = S.zuschnitt;
      if (z === "hg" || ev.target.closest(`.le-box[data-i="${z}"]`)) {
        ev.preventDefault();
        const o = zuschnittZiel();
        merken();
        ziehen = { pan: true, o, ueber: zuschnittUeber(z), x0: ev.clientX, y0: ev.clientY, fx: o.fokus_x ?? 0.5, fy: o.fokus ?? 0.5, start: JSON.stringify(o) };
        flaeche.setPointerCapture(ev.pointerId);
        return;
      }
      S.zuschnitt = null;
    }
    inlineZu();
    const box = ev.target.closest(".le-box");
    if (!box) { waehlen(null); return; }
    ev.preventDefault();
    const i = +box.dataset.i;
    if (S.aktiv !== i) waehlen(i);
    const e = el(i);
    merken();
    ziehen = { i, griff: ev.target.dataset.griff || null, x0: ev.clientX, y0: ev.clientY, start: kopie(e), box: boxVon(i).slice(), div: flaeche.querySelector(`.le-box[data-i="${i}"]`) };
    flaeche.setPointerCapture(ev.pointerId);
  });
  flaeche.addEventListener("pointermove", (ev) => {
    if (!ziehen) return;
    if (ziehen.pan) {
      const k = skala(), dx = (ev.clientX - ziehen.x0) / k, dy = (ev.clientY - ziehen.y0) / k, [ux, uy] = ziehen.ueber;
      const klemmen = (v) => Math.round(Math.max(0, Math.min(1, v)) * 1000) / 1000;
      if (ux > 0.5) ziehen.o.fokus_x = klemmen(ziehen.fx - dx / ux);
      if (uy > 0.5) ziehen.o.fokus = klemmen(ziehen.fy - dy / uy);
      vorschau();
      return;
    }
    const k = skala(), e = el(ziehen.i), st = ziehen.start, b = ziehen.box;
    let dx = (ev.clientX - ziehen.x0) / k, dy = (ev.clientY - ziehen.y0) / k;
    if (ev.shiftKey && !ziehen.griff) { if (Math.abs(dx) > Math.abs(dy)) dy = 0; else dx = 0; }   // Shift: nur waagrecht oder senkrecht
    const g = ziehen.griff;
    let f;
    if (!g) {
      f = fangen(b, dx, dy, ev.altKey);
      e.x = r1(st.x + f.dx); e.y = r1(st.y + f.dy);
      Object.assign(ziehen.div.style, { left: (b[0] + f.dx) * k + "px", top: (b[1] + f.dy) * k + "px" });
    } else if (g === "r") {
      f = fangen([b[2], 0, b[2], 0], dx, 0, ev.altKey);
      e.breite = Math.max(40, r1(st.breite + f.dx));
      ziehen.div.style.width = Math.max(8, (b[2] - b[0] + (e.breite - st.breite))) * k + "px";
    } else if (g === "l") {
      f = fangen([b[0], 0, b[0], 0], dx, 0, ev.altKey);
      const d = Math.min(f.dx, st.breite - 40);
      e.x = r1(st.x + d); e.breite = r1(st.breite - d);
      Object.assign(ziehen.div.style, { left: (b[0] + d) * k + "px", width: (b[2] - b[0] - d) * k + "px" });
    } else if (["ru", "lu", "ro", "lo"].includes(g)) {   // Ecken: Rahmen größer/kleiner, Shift hält das Seitenverhältnis
      f = { linien: [] };
      const links = g[0] === "l", oben = g[1] === "o";
      e.breite = Math.max(40, r1(st.breite + (links ? -dx : dx)));
      e.hoehe = ev.shiftKey ? r1(e.breite * st.hoehe / st.breite) : Math.max(40, r1(st.hoehe + (oben ? -dy : dy)));
      e.x = links ? r1(st.x + st.breite - e.breite) : st.x;
      e.y = oben ? r1(st.y + st.hoehe - e.hoehe) : st.y;
      Object.assign(ziehen.div.style, { left: (b[0] + e.x - st.x) * k + "px", top: (b[1] + e.y - st.y) * k + "px", width: e.breite * k + "px", height: e.hoehe * k + "px" });
    }
    linienZeigen(f.linien || []);
    vorschau();
  });
  const loslassen = () => {
    if (!ziehen) return;
    if (ziehen.pan) {
      const bewegt = ziehen.start !== JSON.stringify(ziehen.o);
      ziehen = null;
      if (!bewegt) { S.zurueck.pop(); if (!S.zurueck.length) { S.geaendert = false; markieren(""); } }
      vorschau();
      return;
    }
    const bewegt = JSON.stringify(ziehen.start) !== JSON.stringify(el(ziehen.i));
    ziehen = null;
    linienZeigen([]);
    if (!bewegt) { S.zurueck.pop(); if (!S.zurueck.length) { S.geaendert = false; markieren(""); } }
    seiteZeichnen();
    vorschau();
  };
  flaeche.addEventListener("pointerup", loslassen);
  flaeche.addEventListener("pointercancel", loslassen);

  // ---------- Bild tauschen: aus der Mediathek, hochladen oder neu erzeugen. ziel = {lesen(), setzen(q), da(), format()}
  const BILDER = window.BILDER || [], POST_BILD = window.POST_BILD || {}, GEN = window.GENERATOREN || {};
  const bildGruppe = (titel) => `<div class="gruppe le-bild"><span class="label">${titel}</span>
      <div class="knoepfe"><button type="button" data-bild="mediathek">Mediathek</button><button type="button" data-bild="hochladen">Hochladen</button><button type="button" data-bild="ki">Neu erzeugen</button></div>
      <div class="le-bild-feld" hidden></div><input type="file" accept="image/*" hidden></div>`;
  const ideeZu = (q) => { const m = BILDER.find((x) => "m" + x.id === q); if (!m || !m.beschreibung) return POST_BILD.idee || "";
    const name = Object.values(GEN).find((n) => m.beschreibung.endsWith(` (${n})`));   // der Generator steht hinten in Klammern
    return name ? m.beschreibung.slice(0, -name.length - 3) : m.beschreibung; };
  function bildBinden(wurzel, ziel) {
    const feld = wurzel.querySelector(".le-bild-feld"), datei = wurzel.querySelector("input[type=file]");
    let offen = null;
    const nehmen = (m) => {   // neues Bild in die Liste und, wenn die Slide noch dieselbe ist, in das Element
      if (!BILDER.some((x) => x.id === m.id)) BILDER.unshift(m);
      if (!S || !ziel.da()) { if (S) markieren("Das Bild liegt in der Mediathek."); return; }
      merken(); ziel.setzen("m" + m.id); vorschau();
      if (wurzel.isConnected && offen === "mediathek") zeigen("mediathek", true);
    };
    function zeigen(was, bleiben) {
      if (offen === was && !bleiben) { offen = null; feld.hidden = true; return; }
      offen = was; feld.hidden = false;
      wurzel.querySelectorAll("[data-bild]").forEach((b) => b.classList.toggle("on", b.dataset.bild === was));
      if (was === "mediathek") {
        const jetzt = ziel.lesen();
        const eintraege = (POST_BILD.datei ? [{ q: "post", datei: POST_BILD.datei, titel: "Bild des Posts" }] : []).concat(BILDER.map((m) => ({ q: "m" + m.id, datei: m.datei, titel: m.titel })));
        feld.innerHTML = eintraege.length ? `<div class="pick">${eintraege.map((x) => `<button type="button" class="pick-item ${x.q === jetzt ? "on" : ""}" data-q="${x.q}" title="${esc(x.titel || "")}"><img src="/media/${esc(x.datei)}" alt="${esc(x.titel || "")}" loading="lazy"></button>`).join("")}</div>`
          : '<span class="hint">Noch keine Bilder in der Mediathek. Lade eins hoch oder erzeuge eins.</span>';
        feld.querySelectorAll("[data-q]").forEach((b) => b.addEventListener("click", () => {
          merken(); ziel.setzen(b.dataset.q); vorschau();
          feld.querySelectorAll("[data-q]").forEach((x) => x.classList.toggle("on", x === b));
        }));
      } else {
        const gens = Object.keys(GEN);
        feld.innerHTML = gens.length ? `<textarea rows="4" placeholder="Szene, Licht, Hintergrund, Pose … Personen mit @Name">${esc(ideeZu(ziel.lesen()))}</textarea>
          ${gens.length > 1 ? `<select aria-label="Generator"><option value="">Generator: automatisch</option>${gens.map((g) => `<option value="${g}">${esc(GEN[g])}</option>`).join("")}</select>` : ""}
          <button type="button" class="primary" data-erzeugen>Bild erzeugen</button><span class="hint">Dauert etwa eine halbe Minute. Das neue Bild landet auch in der Mediathek.</span>`
          : '<span class="hint">Kein Bildgenerator eingerichtet (GEMINI_API_KEY oder SEEDREAM_API_KEY in .env). Lade vorerst ein Bild hoch.</span>';
        const knopf = feld.querySelector("[data-erzeugen]");
        if (knopf) knopf.addEventListener("click", async () => {
          const text = feld.querySelector("textarea").value.trim();
          if (!text) { feld.querySelector("textarea").focus(); return; }
          const fd = new FormData();
          fd.append("beschreibung", text); fd.append("format", ziel.format()); fd.append("generator", (feld.querySelector("select") || {}).value || "");
          knopf.disabled = true; knopf.classList.add("busy"); knopf.textContent = "Erzeugt Bild …"; markieren("Erzeugt Bild …");
          await holen(`${BILD}/erzeugen`, fd);
          if (knopf.isConnected) { knopf.disabled = false; knopf.classList.remove("busy"); knopf.textContent = "Bild erzeugen"; }
        });
      }
    }
    async function holen(url, fd) {
      try {
        const r = await fetch(url, { method: "POST", credentials: "same-origin", body: fd });
        const d = await r.json().catch(() => ({}));
        if (!r.ok || d.fehler || !d.id) { if (S) markieren(d.fehler || "Das hat nicht geklappt."); return; }
        nehmen(d);
      } catch (e) { if (S) markieren("Das hat nicht geklappt."); }
    }
    wurzel.querySelectorAll("[data-bild]").forEach((b) => b.addEventListener("click", () => { if (b.dataset.bild === "hochladen") datei.click(); else zeigen(b.dataset.bild); }));
    datei.addEventListener("change", async () => {
      if (!datei.files[0]) return;
      const fd = new FormData(); fd.append("datei", datei.files[0]); datei.value = "";
      markieren("Lädt hoch …");
      await holen(`${BILD}/upload`, fd);
    });
    return zeigen;
  }
  const formatFuer = (w, h) => { const v = w / h; return v > 1.4 ? "16:9" : v > 0.9 ? "1:1" : v > 0.68 ? "4:5" : "9:16"; };

  // ---------- Seitenleiste: Eigenschaften des gewählten Elements oder Liste aller Elemente
  const textVon = (e, pfad) => pfad === "text" ? e.text : pfad === "punkte" ? (e.punkte || []).join("\n") : e.teile[+pfad.split(".")[1]].text;
  const textSetzen = (e, pfad, w) => { if (pfad === "text") e.text = w; else if (pfad === "punkte") e.punkte = w.split("\n").filter((z) => z.trim()); else e.teile[+pfad.split(".")[1]].text = w; };
  const kurz = (i) => { const e = el(i); const t = e.text || (e.teile || []).map((x) => x.text).filter(Boolean)[0] || (e.punkte || [])[0] || ""; return t ? "„" + String(t).slice(0, 28) + (String(t).length > 28 ? "…" : "") + "“" : ""; };

const fotoRahmen = (o, wer) => `<div class="gruppe"><span class="label">${wer}</span>
    <label>Größe <span class="wert" data-zoomwert>${Math.round((o.zoom || 1) * 100)} %</span><input type="range" min="100" max="400" value="${Math.round((o.zoom || 1) * 100)}" data-zoom></label>
    <button type="button" class="small ${S.zuschnitt !== null && S.zuschnitt !== undefined ? "on" : ""}" data-zuschnitt>${S.zuschnitt !== null && S.zuschnitt !== undefined ? "Ausschnitt fertig" : "Ausschnitt verschieben"}</button>
    <span class="hint">Oder Doppelklick aufs Bild: dann verschiebt Ziehen das Bild, das Mausrad zoomt.${wer === "Foto im Rahmen" ? " Den Rahmen selbst ziehst du an den Ecken größer oder kleiner." : ""}</span></div>`;
  const fotoRahmenBinden = (o, z) => {
    const r = seite.querySelector("[data-zoom]"), w = seite.querySelector("[data-zoomwert]");
    if (r) { r.addEventListener("pointerdown", merken); r.addEventListener("input", () => { o.zoom = zoomGrenze(r.value / 100); w.textContent = r.value + " %"; vorschau(); }); }
    const kn = seite.querySelector("[data-zuschnitt]");
    if (kn) kn.addEventListener("click", () => zuschnitt(S.zuschnitt === z ? null : z));
  };

  function seiteZeichnen() {
    if (S.aktiv === null || S.aktiv === undefined || !el(S.aktiv)) {
      S.aktiv = null;
      const hg = S.szene.hintergrund || {}, hgFoto = "foto" in hg;
      const fotoI = S.szene.elemente.findIndex((x) => x.typ === "foto"), fotoEl = fotoI >= 0 ? S.szene.elemente[fotoI] : null;
      seite.innerHTML = `<p class="leer">Klick einen Text oder ein Bild auf der Folie an, um es zu ändern. Doppelklick auf einen Text ändert ihn direkt.</p>
        ${!hgFoto && fotoEl ? `<div data-fotoel>${bildGruppe("Foto dieser Folie")}</div>` : ""}
        ${!hgFoto && !fotoEl ? `<div class="gruppe le-bild"><span class="label">Foto</span><div class="knoepfe"><button type="button" data-neu="foto">Foto einfügen</button></div></div>` : ""}
        ${hgFoto ? `<div data-hg>${bildGruppe("Hintergrundbild")}${fotoRahmen(hg, "Hintergrundbild zuschneiden")}</div>` : ""}
        <div class="gruppe"><span class="label">Element hinzufügen</span><div class="knoepfe le-neu">
          <button type="button" data-neu="text">Text</button><button type="button" data-neu="rechteck">Rechteck</button><button type="button" data-neu="kreis">Kreis</button>
          <button type="button" data-neu="linie">Linie</button><button type="button" data-neu="verlauf">Verlauf</button><button type="button" data-neu="foto">Foto</button>
          ${S.logo ? '<button type="button" data-neu="logo">Logo</button>' : ""}</div></div>
        <div class="gruppe"><span class="label">Elemente</span>${S.elemente.map((o) => `<button type="button" class="small" data-waehle="${o.i}" style="justify-content:flex-start">${TYP[o.typ] || o.typ} <span class="leise ellipsis">${esc(kurz(o.i))}</span></button>`).join("")}</div>
        <p class="le-tipp">Doppelklick auf einen Text ändert ihn direkt auf der Slide. Ziehen verschiebt, die Griffe links und rechts ändern die Breite. Hilfslinien rasten an Rand und Mitte ein (Alt hält sie aus, Shift zieht nur gerade). Pfeiltasten schieben um 1 px, mit Shift um 10 px.</p>`;
      seite.querySelectorAll("[data-waehle]").forEach((b) => b.addEventListener("click", () => waehlen(+b.dataset.waehle)));
      seite.querySelectorAll("[data-neu]").forEach((b) => b.addEventListener("click", () => hinzufuegen(b.dataset.neu)));
      if (fotoEl && !hgFoto) bildBinden(seite.querySelector("[data-fotoel]"), { lesen: () => fotoEl.quelle || "post", setzen: (q) => { fotoEl.quelle = q; }, da: () => S.szene.elemente.includes(fotoEl), format: () => formatFuer(fotoEl.breite, fotoEl.hoehe) });
      if (hgFoto) {
        bildBinden(seite.querySelector("[data-hg]"), { lesen: () => hg.foto, setzen: (q) => { S.szene.hintergrund.foto = q; }, da: () => S.szene.hintergrund === hg, format: () => "4:5" });
        fotoRahmenBinden(hg, "hg");
      }
      return;
    }
    const i = S.aktiv, e = el(i), info = S.elemente.find((x) => x.i === i) || { texte: [] }, typ = e.typ;
    const hatSchrift = ["text", "stapel", "liste"].includes(typ);
    const basis = typ === "stapel" ? (e.teile || []).map((t) => +t.groesse || 48) : [+e.groesse || (typ === "liste" ? 42 : 48)];
    const hatText = (info.texte || []).length > 0;
    let h = (hatText ? `<button type="button" class="primary" data-inline>Text ändern</button>` : "") + `<div class="gruppe"><span class="label">${TYP[typ] || typ}</span>
      <div class="zahlen"><label>X <input type="number" data-zahl="x" value="${Math.round(e.x)}"></label><label>Y <input type="number" data-zahl="y" value="${Math.round(e.y)}"></label>
      <label>Breite <input type="number" data-zahl="breite" value="${Math.round(e.breite ?? S.W)}"></label>${hatHoehe(typ) ? `<label>Höhe <input type="number" data-zahl="hoehe" value="${Math.round(e.hoehe ?? S.H)}"></label>` : ""}</div></div>`;
    if (hatSchrift) h += `<div class="gruppe"><label>Schriftgröße <span class="wert" data-wert>100 %</span><input type="range" min="40" max="250" value="100" data-groesse></label></div>`;
    if (["text", "stapel", "logo"].includes(typ)) {
      const al = e.ausrichtung || "links";
      h += `<div class="gruppe"><span class="label">Ausrichtung</span><div class="knoepfe">${["links", "mitte", "rechts"].map((a) => `<button type="button" data-ausr="${a}" class="${a === al ? "on" : ""}">${a[0].toUpperCase() + a.slice(1)}</button>`).join("")}</div>
        <button type="button" class="small" data-zentrieren>Waagrecht mittig auf der Slide</button></div>`;
    }
    if (typ === "foto") h += `<div data-fotobild>${bildGruppe("Bild")}</div>` + fotoRahmen(e, "Foto im Rahmen");
    const texte = info.texte || [];
    if (texte.length) {
      h += `<div class="gruppe"><span class="label">Text</span>` + texte.map((t) => `<textarea rows="${Math.min(6, t.wert.split("\n").length + 1)}" data-text="${t.pfad}">${esc(textVon(e, t.pfad))}</textarea>`).join("") +
        `<span class="hint">Enter = neue Zeile.${texte.some((t) => t.feld) ? " Verknüpfte Texte ändern auch das Feld der Slide." : ""}</span></div>`;
    }
    const FARBEN = S.farben || [];
    const palette = (attr, jetzt, mitLeer) => `<div class="le-farben">${mitLeer ? `<button type="button" class="le-farbe leer ${!jetzt ? "on" : ""}" data-${attr}="" title="durchsichtig"></button>` : ""}${FARBEN.map((f) => `<button type="button" class="le-farbe ${(jetzt || "").toLowerCase() === f.hex.toLowerCase() ? "on" : ""}" data-${attr}="${f.hex}" style="background:${f.hex}" title="${esc(f.name)}"></button>`).join("")}</div>`;
    if (["text", "stapel", "liste", "form", "verlauf"].includes(typ) && FARBEN.length) {
      const jetzt = typ === "stapel" ? ((e.teile || [])[0] || {}).farbe : e.farbe;
      h += `<div class="gruppe"><span class="label">${typ === "verlauf" ? "Farbe (volle Seite)" : "Farbe"}</span>${palette("farbe", jetzt)}</div>`;
    }
    if (typ === "form") {
      h += `<div class="gruppe"><span class="label">Form</span><div class="knoepfe">${["rechteck", "kreis", "linie"].map((f) => `<button type="button" data-formart="${f}" class="${(e.form || "rechteck") === f ? "on" : ""}">${f[0].toUpperCase() + f.slice(1)}</button>`).join("")}</div>
        <label>Ecken <input type="range" min="0" max="120" value="${+e.radius || 0}" data-radius></label>
        <label>Deckkraft <input type="range" min="5" max="100" value="${Math.round((e.deckkraft ?? 1) * 100)}" data-deck></label></div>`;
    }
    if (typ === "verlauf") {
      h += `<div class="gruppe"><span class="label">Verlauf</span>
        <label>Volle Farbe auf der Seite <select data-von>${["unten", "oben", "links", "rechts"].map((v) => `<option ${(e.winkel == null && (e.von || "unten") === v) ? "selected" : ""}>${v}</option>`).join("")}${e.winkel != null ? '<option selected value="">schräg (Winkel)</option>' : ""}</select></label>
        <label>Winkel <span class="wert" data-winkelwert>${Math.round(e.winkel ?? { oben: 0, rechts: 90, unten: 180, links: 270 }[e.von || "unten"])}°</span><input type="range" min="0" max="359" step="1" value="${Math.round(e.winkel ?? { oben: 0, rechts: 90, unten: 180, links: 270 }[e.von || "unten"])}" data-winkel></label>
        <label>Länge des Übergangs <input type="range" min="5" max="100" value="${Math.round((e.anteil ?? 0.5) * 100)}" data-anteil></label>
        <label>Voll deckender Teil <input type="range" min="0" max="90" value="${Math.round((e.voll ?? 0) * 100)}" data-voll></label>
        <label>Deckkraft <input type="range" min="5" max="100" value="${Math.round((e.deckkraft ?? 0.8) * 100)}" data-deck></label>
        <span class="label">Ausläufen in</span>${palette("bis", e.farbe_bis, true)}<span class="hint">Leer = ins Durchsichtige (Foto scheint durch), sonst Farbe A → Farbe B.</span></div>`;
    }
    h += `<div class="gruppe"><span class="label">Ebene</span><div class="knoepfe"><button type="button" data-ebene="1">Nach vorne</button><button type="button" data-ebene="-1">Nach hinten</button></div>
      <button type="button" class="small link danger" data-weg>Element entfernen</button></div>
      <button type="button" class="small" data-fertig>Auswahl aufheben</button>`;
    seite.innerHTML = h;

    const ib = seite.querySelector("[data-inline]");
    if (ib) ib.addEventListener("click", () => textBearbeiten(i));
    seite.querySelectorAll("[data-zahl]").forEach((inp) => {
      inp.addEventListener("focus", merken);
      inp.addEventListener("input", () => { const v = parseFloat(inp.value); if (!isNaN(v)) { e[inp.dataset.zahl] = inp.dataset.zahl === "breite" || inp.dataset.zahl === "hoehe" ? Math.max(20, v) : v; vorschau(); } });
    });
    const gr = seite.querySelector("[data-groesse]");
    if (gr) {
      gr.addEventListener("pointerdown", merken);
      gr.addEventListener("keydown", (ev) => { if (ev.key.startsWith("Arrow")) merken(); });
      gr.addEventListener("input", () => {
        const f = gr.value / 100; seite.querySelector("[data-wert]").textContent = gr.value + " %";
        if (typ === "stapel") e.teile.forEach((t, k) => (t.groesse = r1(basis[k] * f))); else e.groesse = r1(basis[0] * f);
        vorschau();
      });
    }
    seite.querySelectorAll("[data-ausr]").forEach((b) => b.addEventListener("click", () => { merken(); e.ausrichtung = b.dataset.ausr; if (typ === "stapel") (e.teile || []).forEach((t) => delete t.ausrichtung); seiteZeichnen(); vorschau(); }));
    const z = seite.querySelector("[data-zentrieren]");
    if (z) z.addEventListener("click", () => { merken(); e.x = r1((S.W - e.breite) / 2); e.ausrichtung = "mitte"; if (typ === "stapel") (e.teile || []).forEach((t) => delete t.ausrichtung); seiteZeichnen(); vorschau(); });
    const fb = seite.querySelector("[data-fotobild]");
    if (fb) bildBinden(fb, { lesen: () => e.quelle || "post", setzen: (q) => { e.quelle = q; }, da: () => S.szene.elemente.includes(e), format: () => formatFuer(e.breite, e.hoehe) });
    if (typ === "foto") fotoRahmenBinden(e, i);
    seite.querySelectorAll("[data-text]").forEach((t) => {
      t.addEventListener("focus", merken);
      t.addEventListener("input", () => { textSetzen(e, t.dataset.text, t.value); vorschau(); });
    });
    seite.querySelectorAll("[data-ebene]").forEach((b) => b.addEventListener("click", () => {
      const j = i + +b.dataset.ebene, arr = S.szene.elemente;
      if (j < 0 || j >= arr.length) return;
      merken(); [arr[i], arr[j]] = [arr[j], arr[i]]; S.aktiv = j; vorschau().then(seiteZeichnen);
    }));
    seite.querySelectorAll("[data-farbe]").forEach((b) => b.addEventListener("click", () => {
      merken();
      if (typ === "stapel") (e.teile || []).forEach((t) => (t.farbe = b.dataset.farbe)); else e.farbe = b.dataset.farbe;
      seiteZeichnen(); vorschau();
    }));
    seite.querySelectorAll("[data-bis]").forEach((b) => b.addEventListener("click", () => { merken(); if (b.dataset.bis) e.farbe_bis = b.dataset.bis; else delete e.farbe_bis; seiteZeichnen(); vorschau(); }));
    seite.querySelectorAll("[data-formart]").forEach((b) => b.addEventListener("click", () => { merken(); e.form = b.dataset.formart; if (e.form === "linie") e.hoehe = Math.min(e.hoehe || 4, 8); seiteZeichnen(); vorschau(); }));
    const regler = (sel, fn) => { const r = seite.querySelector(sel); if (r) { r.addEventListener("pointerdown", merken); r.addEventListener("input", () => { fn(+r.value); vorschau(); }); } };
    regler("[data-radius]", (v) => { e.radius = v; });
    regler("[data-deck]", (v) => { e.deckkraft = v / 100; });
    regler("[data-anteil]", (v) => { e.anteil = v / 100; });
    regler("[data-voll]", (v) => { e.voll = v / 100; });
    const von = seite.querySelector("[data-von]");
    if (von) von.addEventListener("change", () => { if (!von.value) return; merken(); e.von = von.value; delete e.winkel; seiteZeichnen(); vorschau(); });
    const wk = seite.querySelector("[data-winkel]");
    if (wk) {
      wk.addEventListener("pointerdown", merken);
      wk.addEventListener("input", () => {
        let v = +wk.value;
        if (!window.event || !window.event.altKey) { const n = Math.round(v / 15) * 15; if (Math.abs(n - v) <= 4) v = n % 360; }   // rastet alle 15° ein (Alt: frei)
        e.winkel = v; seite.querySelector("[data-winkelwert]").textContent = v + "°"; vorschau();
      });
      wk.addEventListener("change", () => seiteZeichnen());
    }
    seite.querySelector("[data-weg]").addEventListener("click", () => { merken(); S.szene.elemente.splice(i, 1); S.aktiv = null; vorschau().then(seiteZeichnen); });
    seite.querySelector("[data-fertig]").addEventListener("click", () => waehlen(null));
  }

  // ---------- Neue Grundelemente (in den Farben der Marke), landen obenauf und sind gleich gewählt
  function hinzufuegen(art) {
    const f = (rolle, ersatz) => ((S.farben || []).find((x) => x.rolle === rolle) || {}).hex || ersatz;
    const neu = {
      text: { typ: "text", text: "Neuer Text", rolle: "text", groesse: 48, farbe: f("text", "#111111"), x: RAND, y: S.H / 2 - 40, breite: S.W - 2 * RAND, ausrichtung: "links" },
      rechteck: { typ: "form", form: "rechteck", farbe: f("akzent", "#111111"), x: S.W / 2 - 200, y: S.H / 2 - 120, breite: 400, hoehe: 240, radius: 0 },
      kreis: { typ: "form", form: "kreis", farbe: f("akzent", "#111111"), x: S.W / 2 - 120, y: S.H / 2 - 120, breite: 240, hoehe: 240 },
      linie: { typ: "form", form: "linie", farbe: f("text", "#111111"), x: RAND, y: S.H / 2, breite: 300, hoehe: 4 },
      verlauf: { typ: "verlauf", farbe: f("dunkel", "#000000"), von: "unten", anteil: 1, voll: 0, deckkraft: 0.8, x: 0, y: S.H / 2, breite: S.W, hoehe: S.H / 2 },
      foto: { typ: "foto", quelle: "post", fokus: 0.4, x: S.W / 2 - 250, y: S.H / 2 - 300, breite: 500, hoehe: 600, radius: 0 },
      logo: { typ: "logo", x: RAND, y: RAND, breite: 220, ausrichtung: "links" },
    }[art];
    if (!neu) return;
    merken();
    S.szene.elemente.push(neu);
    const i = S.szene.elemente.length - 1;
    S.aktiv = i;
    vorschau().then(() => { boxenZeichnen(); seiteZeichnen(); if (art === "text") textBearbeiten(i); if (art === "foto") { const m = seite.querySelector('[data-bild="mediathek"]'); if (m) m.click(); } });
  }

  // ---------- Öffnen, Rückgängig, Speichern
  async function oeffnen(nr, zumBild) {
    le.hidden = false; seite.innerHTML = '<p class="leer busy">Lädt …</p>'; bild.removeAttribute("src");
    const r = await fetch(`${BASIS}/${nr}`, { credentials: "same-origin" });
    const d = await r.json();
    if (d.fehler) { seite.innerHTML = `<p class="error">${esc(d.fehler)}</p>`; return; }
    S = { ...d, aktiv: null, zurueck: [], vor: [], geaendert: false };
    le.querySelector("h2").textContent = d.titel || `Folie ${d.nr} von ${d.gesamt}`;
    le.querySelector('[data-folie="-1"]').disabled = d.nr <= 1;
    le.querySelector('[data-folie="1"]').disabled = !document.querySelector(`[data-layout="${d.nr + 1}"]`);
    le.querySelector("[data-entwurf]").hidden = !d.manuell;
    markieren(d.manuell ? "von Hand angepasst" : "");
    bild.onload = () => { if (!ziehen) boxenZeichnen(); };
    bild.src = d.bild;
    const foto = zumBild && !("foto" in (d.szene.hintergrund || {})) ? d.elemente.find((x) => x.typ === "foto") : null;
    if (foto) S.aktiv = foto.i;   // „Bild tauschen“: gleich das Foto wählen (ein Hintergrundbild steht ohne Auswahl oben)
    seiteZeichnen();
    if (zumBild) { const m = seite.querySelector('[data-bild="mediathek"]'); if (m) m.click(); }
  }
  function schliessen() {
    if (inline) { inline.div.remove(); inline = null; }
    if (S && S.geaendert && !confirm("Änderungen am Layout verwerfen?")) return;
    le.hidden = true; S = null;
  }
  function schritt(von, nach) {
    if (inline) { inline.div.remove(); inline = null; }
    if (!S || !von.length) return;
    nach.push(kopie(S.szene)); S.szene = von.pop();
    S.geaendert = true; markieren("Nicht gespeichert");
    if (S.aktiv !== null && !el(S.aktiv)) S.aktiv = null;
    vorschau().then(seiteZeichnen);
  }
  le.querySelectorAll("[data-folie]").forEach((b) => b.addEventListener("click", () => {
    if (!S) return;
    if (inline) inlineZu();
    if (S.geaendert && !confirm("Änderungen an dieser Folie verwerfen?")) return;
    oeffnen(S.nr + +b.dataset.folie);
  }));
  le.querySelector("[data-undo]").addEventListener("click", () => schritt(S.zurueck, S.vor));
  le.querySelector("[data-redo]").addEventListener("click", () => schritt(S.vor, S.zurueck));
  le.querySelector("[data-abbrechen]").addEventListener("click", schliessen);
  le.addEventListener("pointerdown", (ev) => { if (ev.target === le) schliessen(); });
  le.querySelector("[data-speichern]").addEventListener("click", async (ev) => {
    if (inline) inlineZu();
    const b = ev.currentTarget; b.disabled = true; markieren("Speichert …");
    const r = await fetch(`${BASIS}/${S.nr}`, { method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ szene: S.szene }) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok || d.fehler) { markieren(d.fehler || "Speichern fehlgeschlagen"); b.disabled = false; return; }
    S.geaendert = false; location.reload();
  });
  le.querySelector("[data-entwurf]").addEventListener("click", async () => {
    if (!confirm("Alle Handanpassungen dieser Slide verwerfen und zum ursprünglichen Entwurf zurück?")) return;
    await fetch(`${BASIS}/${S.nr}/entwurf`, { method: "POST", credentials: "same-origin" });
    S.geaendert = false; location.reload();
  });
  document.addEventListener("keydown", (ev) => {
    if (le.hidden || !S) return;
    const imFeld = /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName);
    if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === "z" && !imFeld) { ev.preventDefault(); ev.shiftKey ? schritt(S.vor, S.zurueck) : schritt(S.zurueck, S.vor); return; }
    if ((ev.key === "Escape" || ev.key === "Enter") && S.zuschnitt !== null && S.zuschnitt !== undefined && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) { ev.preventDefault(); zuschnitt(null); return; }
    if (ev.key === "Escape") { ev.preventDefault(); if (inline) inlineZu(); else if (imFeld) document.activeElement.blur(); else if (S.aktiv !== null) waehlen(null); else schliessen(); return; }
    if (imFeld || S.aktiv === null) return;
    const d = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[ev.key];
    if (d) {
      ev.preventDefault();
      const n = ev.shiftKey ? 10 : 1, e = el(S.aktiv);
      if (!ev.repeat) merken();
      e.x = r1(e.x + d[0] * n); e.y = r1(e.y + d[1] * n);
      const b = boxVon(S.aktiv); if (b) { b[0] += d[0] * n; b[2] += d[0] * n; b[1] += d[1] * n; b[3] += d[1] * n; }
      boxenZeichnen(); vorschau();
      const xi = seite.querySelector('[data-zahl="x"]'), yi = seite.querySelector('[data-zahl="y"]');
      if (xi) { xi.value = Math.round(e.x); yi.value = Math.round(e.y); }
    }
  });
  window.addEventListener("resize", () => { if (S && !le.hidden) boxenZeichnen(); });
  window.addEventListener("beforeunload", (ev) => { if (S && S.geaendert && !le.hidden) { ev.preventDefault(); ev.returnValue = ""; } });
  knoepfe.forEach((b) => {
    b.addEventListener("click", () => oeffnen(+b.dataset.layout, "layoutBild" in b.dataset));
    if (b.tagName !== "BUTTON") b.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); oeffnen(+b.dataset.layout); } });
  });
})();

// Connectors: Zähler der ausgewählten Kanäle
document.querySelectorAll("[data-zaehler]").forEach((z) => {
  const form = z.closest("form");
  const boxen = form.querySelectorAll('input[name="kanal"]');
  const zeigen = () => { z.textContent = `${[...boxen].filter((b) => b.checked).length} von ${boxen.length} ausgewählt`; };
  boxen.forEach((b) => b.addEventListener("change", zeigen));
});

// Formatwechsel zwischen Slides (Carousel, Einzelbild) und Video (Reel, Story): vorher warnen
(() => {
  const sel = document.querySelector("select[data-format-vorher]");
  if (!sel) return;
  const gruppe = (f) => (["Reel", "Story"].includes(f) ? "video" : "slides");
  sel.form.addEventListener("submit", (e) => {
    const vorher = sel.dataset.formatVorher;
    if (sel.value === vorher || gruppe(sel.value) === gruppe(vorher) || sel.dataset.hatVisuals !== "1") return;
    const msg = gruppe(sel.value) === "video"
      ? `Aus dem ${vorher} wird ein ${sel.value}. Die Slides werden zu Szenen, die bisherigen Bilder passen dann nicht mehr. Format wechseln?`
      : `Aus dem ${vorher} wird ein ${sel.value}. Das Video bleibt gespeichert, der Post bekommt aber neue Slides. Format wechseln?`;
    if (!confirm(msg)) { e.preventDefault(); sel.value = vorher; }
  });
})();

// Dashboard: Schnellfragen an die Redaktion
document.querySelectorAll("[data-frage]").forEach((b) => b.addEventListener("click", () => {
  const form = b.closest(".chat").querySelector(".chatform");
  form.querySelector("textarea").value = b.dataset.frage;
  form.requestSubmit();
}));

// Planen-Tabelle: nach Spalte sortieren (Klick auf die Überschrift, nochmal = umgekehrt)
document.querySelectorAll(".tabelle th[data-sort]").forEach((th) => th.addEventListener("click", () => {
  const tb = th.closest("table").querySelector("tbody"), k = th.dataset.sort;
  const auf = th.dataset.richtung !== "auf";
  th.closest("tr").querySelectorAll("th").forEach((x) => delete x.dataset.richtung);
  th.dataset.richtung = auf ? "auf" : "ab";
  [...tb.querySelectorAll("tr[data-format]")].sort((a, b) => (a.dataset[k] || "").localeCompare(b.dataset[k] || "", "de") * (auf ? 1 : -1))
    .forEach((tr) => tb.appendChild(tr));
}));

// Planen: Einstellungen der Redaktion nur aufklappen, wenn man gezielt hinspringt (#redaktion)
const redaktionAuf = () => { if (location.hash === "#redaktion") { const r = document.getElementById("redaktion"); if (r) { r.open = true; r.scrollIntoView(); } } };
redaktionAuf();
window.addEventListener("hashchange", redaktionAuf);

// Ideenwerkstatt: Leiste für mehrere ausgewählte Ideen
(() => {
  const form = document.getElementById("ideen-form");
  const leiste = form && form.querySelector(".auswahl-leiste");
  if (!leiste) return;
  const boxen = [...form.querySelectorAll('input[name="idee"]')];
  const zeigen = () => {
    const n = boxen.filter((b) => b.checked).length;
    leiste.hidden = n === 0;
    leiste.querySelector("[data-auswahl-zahl]").textContent = `${n} ausgewählt`;
  };
  boxen.forEach((b) => b.addEventListener("change", zeigen));
  leiste.querySelector("[data-auswahl-leeren]").addEventListener("click", () => { boxen.forEach((b) => (b.checked = false)); zeigen(); });
  zeigen();
})();

// Einstellungen: Prompt bzw. MCP-Konfiguration kopieren (jeweils das Feld im selben Block)
document.querySelectorAll("[data-kopieren-knopf]").forEach((k) => k.addEventListener("click", async () => {
  const feld = (k.closest("[data-kopier-block]") || k.closest(".mcp-neu")).querySelector("[data-kopieren]");
  try { await navigator.clipboard.writeText(feld.value); } catch (e) { feld.select(); document.execCommand("copy"); }
  k.textContent = "Kopiert";
}));

// Post: zwei Schritte, „Inhalt“ (Vorschau mit Inline-Bearbeitung) und „Planen & veröffentlichen“.
// Start im Inhalt; wer gerade etwas abgeschickt hat, landet wieder im selben Schritt.
(() => {
  const nav = document.querySelector("[data-post-ansicht]");
  if (!nav) return;
  const pid = nav.dataset.postAnsicht, key = "post-ansicht-" + pid;
  const ALLE = ["inhalt", "veroeffentlichen"];
  const zeigen = (was, merken) => {
    if (!ALLE.includes(was)) was = "inhalt";
    document.querySelectorAll("[data-ansicht]").forEach((d) => { d.hidden = d.dataset.ansicht !== was; });
    nav.querySelectorAll("[data-ansicht-wechsel]").forEach((b) => { const on = b.dataset.ansichtWechsel === was; b.classList.toggle("on", on); on ? b.setAttribute("aria-current", "page") : b.removeAttribute("aria-current"); });
    if (merken) { try { sessionStorage.setItem(key, JSON.stringify({ was, t: Date.now() })); } catch (e) {} }
  };
  document.querySelectorAll("[data-ansicht-wechsel]").forEach((b) => b.addEventListener("click", () => { zeigen(b.dataset.ansichtWechsel, true); window.scrollTo({ top: 0 }); }));
  let start = "inhalt";
  try { const m = JSON.parse(sessionStorage.getItem(key) || "null"); if (m && Date.now() - m.t < 10 * 60 * 1000) start = m.was; } catch (e) {}
  if (new URLSearchParams(location.search).get("tab")) start = "inhalt";
  if (/^#(abnehmen|planen|veroeffentlichen)/.test(location.hash)) start = "veroeffentlichen";
  zeigen(start, false);
  document.addEventListener("submit", (e) => {
    const quelle = (e.submitter && e.submitter.closest("[data-ansicht]")) || e.target.closest("[data-ansicht]");
    if (quelle) { try { sessionStorage.setItem(key, JSON.stringify({ was: quelle.dataset.ansicht, t: Date.now() })); } catch (x) {} }
  }, true);

  // Vorschau: Instagram oder LinkedIn (gemerkt)
  const pk = "post-plattform";
  const plattform = (was) => {
    document.querySelectorAll("[data-plattform]").forEach((b) => { const on = b.dataset.plattform === was; b.classList.toggle("on", on); b.setAttribute("aria-selected", on ? "true" : "false"); });
    document.querySelectorAll("[data-plattform-inhalt]").forEach((d) => { d.hidden = d.dataset.plattformInhalt !== was; });
    document.querySelectorAll("[data-plattform-inhalt] [data-ig-caption]").forEach(mehrPruefen);
  };
  document.querySelectorAll("[data-plattform]").forEach((b) => b.addEventListener("click", () => { plattform(b.dataset.plattform); try { localStorage.setItem(pk, b.dataset.plattform); } catch (e) {} }));
  try { const g = localStorage.getItem(pk); if (g && document.querySelector(`[data-plattform="${g}"]`)) plattform(g); } catch (e) {}

  // Carousel wischen: Zähler, Punkte, Pfeile – je Vorschau
  document.querySelectorAll("[data-ig-karussell]").forEach((k) => {
    const spur = k.querySelector("[data-ig-spur]");
    if (!spur) return;
    const n = spur.children.length, zaehler = k.querySelector("[data-ig-zaehler]");
    const punkte = (k.closest(".ig-telefon") || k).querySelectorAll("[data-ig-punkte] i");
    const pfeile = k.querySelectorAll("[data-ig-pfeil]");
    const aktuell = () => Math.round(spur.scrollLeft / (spur.clientWidth || 1));
    const anzeigen = () => { const i = aktuell(); if (zaehler) zaehler.textContent = `${i + 1}/${n}`; punkte.forEach((p, j) => p.classList.toggle("on", j === i));
      pfeile.forEach((p) => { p.hidden = (+p.dataset.igPfeil < 0 && i === 0) || (+p.dataset.igPfeil > 0 && i === n - 1); }); };
    spur.addEventListener("scroll", anzeigen, { passive: true });
    pfeile.forEach((p) => p.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const ziel = Math.max(0, Math.min(n - 1, aktuell() + +p.dataset.igPfeil)) * spur.clientWidth;
      spur.scrollTo({ left: ziel, behavior: "smooth" });
      setTimeout(() => { if (Math.abs(spur.scrollLeft - ziel) > 2) spur.scrollLeft = ziel; anzeigen(); }, 450);
    }));
  });
  // Caption „… mehr“ (erst prüfbar, wenn die Vorschau sichtbar ist)
  function mehrPruefen(c) {
    if (c.dataset.mehrGeprueft || !c.offsetParent) return;
    c.dataset.mehrGeprueft = "1";
    if (c.scrollHeight <= c.clientHeight + 2) { c.classList.remove("kurz"); return; }
    const mehr = document.createElement("button"); mehr.type = "button"; mehr.className = "ig-mehr"; mehr.textContent = "… mehr";
    mehr.addEventListener("click", (ev) => { ev.stopPropagation(); c.classList.remove("kurz"); mehr.remove(); });
    c.after(mehr);
  }
  document.querySelectorAll("[data-ig-caption]").forEach(mehrPruefen);

  // Inline-Bearbeitung: Caption (Instagram, LinkedIn) und Titel direkt in der Vorschau ändern, sofort gespeichert
  document.querySelectorAll("[data-inline-feld]").forEach((zone) => {
    const feld = zone.dataset.inlineFeld, max = +zone.dataset.max || 0, einzeilig = "einzeilig" in zone.dataset;
    const anzeige = zone.querySelector("[data-inline-text]");
    const formFeld = () => document.querySelector(`[data-feld-wert="${feld}"]`);
    let offen = false;
    const oeffnen = () => {
      if (offen) return;
      offen = true; zone.classList.add("bearbeitet");
      const wert = formFeld() ? formFeld().value : anzeige.textContent;
      const box = document.createElement("div");
      box.className = "pv-inline" + (einzeilig ? " einzeilig" : "");
      box.innerHTML = (einzeilig ? `<input type="text" maxlength="${max}">` : `<textarea rows="6"></textarea>`) +
        `<div class="pv-inline-fuss"><span class="hint" data-z></span><span class="grow"></span><button type="button" class="small" data-x>Abbrechen</button><button type="button" class="small primary" data-ok>Fertig</button></div>`;
      const eingabe = box.querySelector("input, textarea"), z = box.querySelector("[data-z]");
      eingabe.value = wert;
      const zaehlen = () => { if (!einzeilig) { z.textContent = max ? `${eingabe.value.length} / ${max}` : ""; z.classList.toggle("warn", max && eingabe.value.length > max); }
        if (!einzeilig) { eingabe.style.height = "auto"; eingabe.style.height = Math.min(420, eingabe.scrollHeight + 2) + "px"; } };
      zone.hidden = true; zone.after(box);
      eingabe.focus(); eingabe.setSelectionRange(eingabe.value.length, eingabe.value.length); zaehlen();
      eingabe.addEventListener("input", zaehlen);
      const zu = () => { box.remove(); zone.hidden = false; zone.classList.remove("bearbeitet"); offen = false; };
      const speichern = async () => {
        const neu = eingabe.value;
        if (neu === wert) return zu();
        const ok = box.querySelector("[data-ok]"); ok.disabled = true; ok.textContent = "Speichert …";
        try {
          const r = await fetch(`/planen/${pid}/feld`, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ feld, wert: neu }) });
          const d = await r.json().catch(() => ({}));
          if (!r.ok || d.fehler) throw new Error(d.fehler || "Speichern ging nicht.");
          if (formFeld()) formFeld().value = d.wert;
          document.querySelectorAll(`[data-inline-feld="${feld}"] [data-inline-text]`).forEach((a) => {
            a.innerHTML = d.wert ? (einzeilig ? "" : d.html) : `<span class="leise">${feld === "caption_li" ? "LinkedIn-Text schreiben …" : feld === "titel" ? "Ohne Titel" : "Caption schreiben …"}</span>`;
            if (einzeilig && d.wert) a.textContent = d.wert;
          });
          zu();
        } catch (f) { ok.disabled = false; ok.textContent = "Fertig"; z.textContent = f.message; z.classList.add("warn"); }
      };
      box.querySelector("[data-ok]").addEventListener("click", speichern);
      box.querySelector("[data-x]").addEventListener("click", zu);
      eingabe.addEventListener("keydown", (ev) => {
        if (ev.key === "Escape") { ev.preventDefault(); ev.stopPropagation(); zu(); }
        if (ev.key === "Enter" && (einzeilig || ev.metaKey || ev.ctrlKey)) { ev.preventDefault(); speichern(); }
      });
    };
    zone.addEventListener("click", (ev) => { if (!ev.target.closest("a, .ig-mehr")) oeffnen(); });
    zone.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); oeffnen(); } });
  });
  // Reel: Ton an/aus
  document.querySelectorAll("[data-ig-ton]").forEach((b) => b.addEventListener("click", () => { const v = document.querySelector("[data-ig-video]"); if (v) { v.muted = !v.muted; b.classList.toggle("an", !v.muted); } }));
})();

// Newsletter: HTML der Ausgabe in die Zwischenablage (für MailerLite-Tarife ohne eigenes HTML über die Schnittstelle)
document.querySelectorAll("[data-html-kopieren]").forEach((k) => k.addEventListener("click", async () => {
  const html = await (await fetch(k.dataset.htmlKopieren, { credentials: "same-origin" })).text();
  try { await navigator.clipboard.writeText(html); k.textContent = "Kopiert"; } catch (e) { k.textContent = "Kopieren nicht erlaubt"; }
}));

// Kalender: Post auf einen anderen Tag ziehen (nur solange er nicht an Buffer übertragen ist; nie in die Vergangenheit)
(() => {
  let ziehend = null;
  document.querySelectorAll(".cal-post[data-verschieben]").forEach((k) => {
    k.addEventListener("dragstart", (e) => { ziehend = k; k.classList.add("zieht"); e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", k.dataset.verschieben); });
    k.addEventListener("dragend", () => { k.classList.remove("zieht"); ziehend = null; document.querySelectorAll(".day.drop-ziel").forEach((d) => d.classList.remove("drop-ziel")); });
  });
  document.querySelectorAll(".cal .day[data-datum]").forEach((tag) => {
    const erlaubt = () => ziehend && !tag.classList.contains("vorbei") && ziehend.closest(".day") !== tag;
    tag.addEventListener("dragover", (e) => { if (erlaubt()) { e.preventDefault(); e.dataTransfer.dropEffect = "move"; tag.classList.add("drop-ziel"); } });
    tag.addEventListener("dragleave", (e) => { if (!tag.contains(e.relatedTarget)) tag.classList.remove("drop-ziel"); });
    tag.addEventListener("drop", async (e) => {
      e.preventDefault(); tag.classList.remove("drop-ziel");
      if (!erlaubt()) return;
      const k = ziehend, alt = k.closest(".day");
      tag.appendChild(k);   // sofort zeigen, bei Fehler zurück
      try {
        const r = await fetch(`/planen/${k.dataset.verschieben}/termin`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ datum: tag.dataset.datum }) });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(d.fehler || "Verschieben ging nicht.");
      } catch (f) { alt.appendChild(k); alert(f.message); }
    });
  });
})();

// Sidebar: Planen auf- und zuklappen; auf Planen-Seiten offen, sonst die letzte Wahl
(() => {
  document.querySelectorAll("[data-nav-gruppe]").forEach((g) => {
    const knopf = g.querySelector(".unterpunkt-kopf"), schluessel = "nav-" + g.dataset.navGruppe;
    let gemerkt = null;
    try { gemerkt = localStorage.getItem(schluessel); } catch (e) {}
    const setzen = (offen) => { g.classList.toggle("offen", offen); knopf.setAttribute("aria-expanded", offen ? "true" : "false"); };
    if (g.dataset.aktiv !== "1" && gemerkt !== null) setzen(gemerkt === "1");
    knopf.addEventListener("click", () => {
      const offen = !g.classList.contains("offen");
      setzen(offen);
      try { localStorage.setItem(schluessel, offen ? "1" : "0"); } catch (e) {}
    });
  });
})();

// Handy: Hauptmenü als Schublade (Menüknopf in der Kopfleiste, Schleier oder Escape schließt, ein Link schließt sie auch)
(() => {
  const knopf = document.querySelector("[data-menue-auf]"), menue = document.getElementById("hauptmenue");
  if (!knopf || !menue) return;
  const schmal = matchMedia("(max-width: 900px)");
  const setzen = (offen) => {
    document.body.classList.toggle("menue-offen", offen);
    knopf.setAttribute("aria-expanded", offen ? "true" : "false");
    if (offen) (menue.querySelector("a.on, a, button") || menue).focus({ preventScroll: true });
  };
  knopf.addEventListener("click", () => setzen(!document.body.classList.contains("menue-offen")));
  document.querySelectorAll("[data-menue-zu]").forEach((s) => s.addEventListener("click", () => setzen(false)));
  menue.addEventListener("click", (e) => { if (schmal.matches && e.target.closest("a")) setzen(false); });
  addEventListener("keydown", (e) => { if (e.key === "Escape" && document.body.classList.contains("menue-offen")) { setzen(false); knopf.focus(); } });
  schmal.addEventListener("change", () => setzen(false));
})();

// Carousel: Folien in der Leiste umsortieren. Maus ziehen, am Handy lange drücken und ziehen, Tastatur Alt + Pfeil.
(() => {
  const leiste = document.querySelector("[data-sortieren]");
  if (!leiste) return;
  const folien = () => [...leiste.querySelectorAll(".pv-folie")];
  const anfang = folien().map((f) => f.dataset.nr).join();
  const hinweis = leiste.closest(".pv-block")?.querySelector(".pv-block-kopf .muted");
  let zug = null, geklickt = false, timer = null;

  const nummern = () => folien().forEach((f, i) => { const s = f.querySelector(".nr"); if (s) s.textContent = i + 1; });
  const speichern = async () => {
    const reihe = folien().map((f) => +f.dataset.nr);
    if (reihe.join() === anfang) return;
    leiste.classList.add("speichert");
    if (hinweis) hinweis.textContent = "Neue Reihenfolge wird gespeichert …";
    try {
      const r = await fetch(leiste.dataset.sortieren, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reihe }) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.fehler || "Das hat nicht geklappt.");
      location.reload();
    } catch (err) {
      leiste.classList.remove("speichert");
      if (hinweis) { hinweis.textContent = err.message; hinweis.classList.add("fehler"); }
      setTimeout(() => location.reload(), 2500);
    }
  };
  const beenden = (ok) => {
    clearTimeout(timer);
    if (zug?.an) {
      zug.f.classList.remove("zieht"); leiste.classList.remove("sortiert");
      geklickt = true; setTimeout(() => (geklickt = false), 0);
      if (ok) speichern(); else location.reload();
    }
    zug = null;
  };
  const aktivieren = () => { zug.an = true; zug.f.classList.add("zieht"); leiste.classList.add("sortiert"); };

  leiste.querySelectorAll("img").forEach((i) => (i.draggable = false));
  leiste.addEventListener("pointerdown", (e) => {
    const f = e.target.closest(".pv-folie");
    if (!f || f.disabled || e.button !== 0 || leiste.classList.contains("speichert")) return;
    zug = { f, x: e.clientX, y: e.clientY, an: false, id: e.pointerId, touch: e.pointerType !== "mouse" };
    if (zug.touch) timer = setTimeout(() => zug && aktivieren(), 320);   // am Handy erst nach langem Drücken, sonst scrollt die Seite
  });
  addEventListener("pointermove", (e) => {
    if (!zug || e.pointerId !== zug.id) return;
    if (!zug.an) {
      if (Math.hypot(e.clientX - zug.x, e.clientY - zug.y) < 6) return;
      if (zug.touch) { clearTimeout(timer); zug = null; return; }
      aktivieren();
    }
    const unter = document.elementFromPoint(e.clientX, e.clientY)?.closest(".pv-folie");
    if (!unter || unter === zug.f || unter.parentElement !== leiste) return;
    const r = unter.getBoundingClientRect();
    leiste.insertBefore(zug.f, e.clientX > r.left + r.width / 2 ? unter.nextSibling : unter);
    nummern();
  });
  addEventListener("touchmove", (e) => { if (zug?.an) e.preventDefault(); }, { passive: false });
  addEventListener("pointerup", (e) => { if (zug && e.pointerId === zug.id) beenden(true); });
  addEventListener("pointercancel", (e) => { if (zug && e.pointerId === zug.id) beenden(!!zug.an); });
  leiste.addEventListener("contextmenu", (e) => { if (zug) e.preventDefault(); });
  leiste.addEventListener("click", (e) => { if (geklickt) { e.stopPropagation(); e.preventDefault(); } }, true);   // nach dem Ziehen nicht den Editor öffnen

  let spaeter = null;
  leiste.addEventListener("keydown", (e) => {
    const f = e.target.closest(".pv-folie");
    if (!f || !e.altKey || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(e.key)) return;
    e.preventDefault();
    const vor = e.key === "ArrowLeft" || e.key === "ArrowUp";
    const nachbar = vor ? f.previousElementSibling : f.nextElementSibling;
    if (!nachbar) return;
    leiste.insertBefore(f, vor ? nachbar : nachbar.nextSibling);
    f.focus(); nummern();
    clearTimeout(spaeter); spaeter = setTimeout(speichern, 900);
  });
})();

// Planen & veröffentlichen: kleiner Monatskalender mit Miniaturen aller Posts. Den eigenen Post auf einen Tag ziehen
// (Maus oder Finger, das Vorschaubild scrollt die Seite nicht) oder einen Tag anklicken, um das Datum zu setzen.
(() => {
  const wurzel = document.querySelector("[data-mini-kal]"), K = window.PLAN_KAL;
  const datum = document.querySelector("[data-mini-kal-datum]"), zeit = document.querySelector("[data-mini-kal-zeit]");
  const info = document.querySelector("[data-mini-kal-info]");
  if (!wurzel || !K || !datum) return;
  const MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"];
  const WT = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"], WT_LANG = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"];
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const ausIso = (s) => { const [j, m, t] = s.split("-").map(Number); return new Date(j, m - 1, t); };
  const wochentag = (d) => (d.getDay() + 6) % 7;
  const anfang = datum.value;
  const STATUS = { "Geplant": "geplant", "Gepostet": "gepostet", "Zur Abnahme": "abnahme", "Wartet auf Input": "abnahme", "Idee": "idee" };
  let monat = ausIso(datum.value || K.heute); monat.setDate(1);

  const mini = (p, eigen) => {
    const inhalt = p.thumb ? `<img src="${esc(p.thumb)}" alt="" draggable="false">` : `<b>${esc((p.titel || "?").slice(0, 1))}</b>`;
    return `<span class="mk-mini ${eigen ? "eigen" : STATUS[p.status] || "arbeit"}" ${eigen ? 'data-eigen aria-label="Dein Post, ziehen zum Verschieben"' : ""}>${inhalt}</span>`;
  };

  const beschreiben = (tag) => {
    if (!tag) return '<span class="muted">Noch kein Datum. Zieh deinen Post auf einen Tag oder klick einen Tag an.</span>';
    const d = ausIso(tag), andere = K.tage[tag] || [];
    let h = `<span class="mini-kal-eigen"><strong>${WT_LANG[wochentag(d)]}, ${d.getDate()}. ${MONATE[d.getMonth()]} ${d.getFullYear()}</strong>${zeit?.value ? ` · ${esc(zeit.value)} Uhr` : ""}`;
    if (tag !== anfang) h += ' <em class="mini-kal-neu">noch speichern</em>';
    h += "</span>";
    h += andere.length
      ? `<span class="mini-kal-andere">Am selben Tag: ${andere.map((p) => `<a href="/planen/${p.id}">${esc(p.titel)}</a>${p.zeit ? ` <span class="muted">${esc(p.zeit)}</span>` : ""}`).join(", ")}</span>`
      : '<span class="mini-kal-andere muted">An diesem Tag ist sonst nichts geplant.</span>';
    if (tag < K.heute) h += '<span class="mini-kal-andere muted">Der Tag liegt in der Vergangenheit.</span>';
    const gezeigt = ausIso(tag); if (gezeigt.getMonth() !== monat.getMonth() || gezeigt.getFullYear() !== monat.getFullYear())
      h += '<button type="button" class="link small" data-mk-hin>Zum Monat deines Posts</button>';
    return h;
  };

  const zeichnen = () => {
    const erster = new Date(monat), start = new Date(erster); start.setDate(1 - wochentag(erster));
    const ende = new Date(erster.getFullYear(), erster.getMonth() + 1, 0), wochen = Math.ceil((wochentag(erster) + ende.getDate()) / 7);
    let h = `<div class="mini-kal-kopf"><strong>${MONATE[erster.getMonth()]} ${erster.getFullYear()}</strong>
      <button type="button" class="quadrat small" data-mk="-1" aria-label="Vorheriger Monat">‹</button><button type="button" class="quadrat small" data-mk="1" aria-label="Nächster Monat">›</button></div>
      <div class="mini-kal-raster">${WT.map((w) => `<span class="mk-wt">${w}</span>`).join("")}`;
    for (let i = 0; i < wochen * 7; i++) {
      const d = new Date(start); d.setDate(start.getDate() + i);
      const t = iso(d), posts = K.tage[t] || [], vorbei = t < K.heute, hier = t === datum.value;
      const kl = ["mk-tag", d.getMonth() !== erster.getMonth() && "anders", vorbei && "vorbei", t === K.heute && "heute", hier && "gewaehlt",
        !vorbei && K.rhythmus.includes(wochentag(d)) && !posts.length && !hier && "rhythmus"].filter(Boolean).join(" ");
      const titel = [hier ? `Dein Post: ${K.eigen.titel}` : "", ...posts.map((p) => `${p.zeit || ""} ${p.titel} (${p.status})`.trim())].filter(Boolean).join("\n");
      const max = hier ? 2 : 3, rest = posts.length - max;
      h += `<div class="${kl}" data-tag="${t}" ${vorbei ? "" : 'role="button" tabindex="0"'} title="${esc(titel || (vorbei ? "" : "Auf diesen Tag legen"))}">
        <span class="mk-zahl">${d.getDate()}</span><span class="mk-minis">${hier ? mini(K.eigen, true) : ""}${posts.slice(0, max).map((p) => mini(p)).join("")}${rest > 0 ? `<span class="mk-mehr">+${rest}</span>` : ""}</span></div>`;
    }
    wurzel.innerHTML = h + `</div><div class="mini-kal-legende"><span><span class="mk-mini eigen"></span>dein Post</span>
      ${[["geplant", "geplant"], ["abnahme", "braucht dich"], ["idee", "Idee"], ["gepostet", "gepostet"]].map(([k, t]) => `<span><span class="mk-mini ${k}"></span>${t}</span>`).join("")}</div>`;
    if (info) info.innerHTML = beschreiben(datum.value);
  };

  const setzen = (tag) => { datum.value = tag; datum.dispatchEvent(new Event("change", { bubbles: true })); };
  const zumPost = () => { if (datum.value) { monat = ausIso(datum.value); monat.setDate(1); } };

  // ---------- Ziehen des eigenen Posts
  let zug = null, geradeGezogen = false;
  const zielUnter = (x, y) => document.elementFromPoint(x, y)?.closest(".mk-tag:not(.vorbei), [data-mk]");
  const loslegen = () => {
    zug.an = true;
    zug.geist = document.createElement("div");
    zug.geist.className = "mk-geist";
    zug.geist.innerHTML = K.eigen.thumb ? `<img src="${esc(K.eigen.thumb)}" alt="">` : `<b>${esc(K.eigen.titel.slice(0, 1))}</b>`;
    document.body.appendChild(zug.geist);
    wurzel.classList.add("zieht");
    bewegen(zug.x, zug.y);
  };
  const bewegen = (x, y) => {
    zug.geist.style.transform = `translate(${x - 18}px, ${y - 24}px) rotate(-4deg)`;
    const ziel = zielUnter(x, y);
    wurzel.querySelectorAll(".mk-tag.ziel").forEach((z) => z.classList.remove("ziel"));
    clearTimeout(zug.blaettern);
    if (ziel?.dataset.tag) ziel.classList.add("ziel");
    else if (ziel?.dataset.mk) zug.blaettern = setTimeout(() => { monat.setMonth(monat.getMonth() + +ziel.dataset.mk); zeichnen(); wurzel.classList.add("zieht"); }, 450);
  };
  const ende = (x, y, ok) => {
    clearTimeout(zug.halten); clearTimeout(zug.blaettern);
    if (zug.an) {
      zug.geist.remove(); wurzel.classList.remove("zieht");
      geradeGezogen = true; setTimeout(() => (geradeGezogen = false), 0);
      const ziel = ok && zielUnter(x, y);
      if (ziel?.dataset.tag) setzen(ziel.dataset.tag); else zeichnen();
    }
    zug = null;
  };
  wurzel.addEventListener("pointerdown", (e) => {
    if (!e.target.closest("[data-eigen]") || e.button !== 0) return;
    e.preventDefault();
    zug = { x: e.clientX, y: e.clientY, startX: e.clientX, startY: e.clientY, id: e.pointerId, an: false, touch: e.pointerType !== "mouse" };
  });
  addEventListener("pointermove", (e) => {
    if (!zug || e.pointerId !== zug.id) return;
    zug.x = e.clientX; zug.y = e.clientY;
    if (!zug.an) {
      const weg = Math.hypot(e.clientX - zug.startX, e.clientY - zug.startY);
      if (weg < 4) return;
      loslegen();
    }
    bewegen(e.clientX, e.clientY);
  });
  addEventListener("touchmove", (e) => { if (zug?.an) e.preventDefault(); }, { passive: false });
  addEventListener("pointerup", (e) => { if (zug && e.pointerId === zug.id) ende(e.clientX, e.clientY, true); });
  addEventListener("pointercancel", (e) => { if (zug && e.pointerId === zug.id) ende(e.clientX, e.clientY, false); });
  wurzel.addEventListener("contextmenu", (e) => { if (e.target.closest("[data-eigen]")) e.preventDefault(); });

  // ---------- Klicks und Tastatur
  wurzel.addEventListener("click", (e) => {
    if (geradeGezogen) return;
    const nav = e.target.closest("[data-mk]");
    if (nav) { monat.setMonth(monat.getMonth() + +nav.dataset.mk); zeichnen(); return; }
    const tag = e.target.closest(".mk-tag:not(.vorbei)");
    if (tag) setzen(tag.dataset.tag);
  });
  wurzel.addEventListener("keydown", (e) => {
    const tag = e.target.closest(".mk-tag:not(.vorbei)");
    if (tag && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); setzen(tag.dataset.tag); wurzel.querySelector(`[data-tag="${tag.dataset.tag}"]`)?.focus(); }
  });
  info?.addEventListener("click", (e) => { if (e.target.closest("[data-mk-hin]")) { zumPost(); zeichnen(); } });
  datum.addEventListener("change", () => { zumPost(); zeichnen(); });
  zeit?.addEventListener("change", zeichnen);
  zeichnen();
})();

// Zu erledigen: Termin direkt in der Liste wählen (gleicher Weg wie Ziehen im Kalender)
document.querySelectorAll("[data-termin]").forEach((feld) => {
  feld.addEventListener("change", async () => {
    if (!feld.value) return;
    const zeile = feld.closest(".auf-zeile"), ok = zeile.querySelector("[data-ok]");
    feld.disabled = true;
    try {
      const r = await fetch(`/planen/${feld.dataset.termin}/termin`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ datum: feld.value }) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.fehler || "Das hat nicht geklappt.");
      ok.hidden = false; zeile.classList.add("erledigt");
      setTimeout(() => location.reload(), 900);
    } catch (e) { feld.disabled = false; ok.hidden = false; ok.textContent = e.message; ok.classList.add("fehler"); }
  });
});
