// Motion-Renderer für Talking-Head-Reels. Alles ist eine Funktion der Zeit: SEEK(t) setzt jedes Element neu,
// deshalb rendert Chrome jedes Bild einzeln und wiederholbar. Daten in window.REGIE (schreibt app/motion/regie.py).
(() => {
  const R = window.REGIE;
  const E = {
    lin: (x) => x,
    out: (x) => 1 - Math.pow(1 - x, 3),
    in: (x) => x * x * x,
    inOut: (x) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2),
    back: (x) => 1 + 2.2 * Math.pow(x - 1, 3) + 1.2 * Math.pow(x - 1, 2),
  };
  const cl = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
  const p = (t, at, dur, ease = E.out) => ease(cl(dur ? (t - at) / dur : t >= at ? 1 : 0));
  const mix = (a, b, k) => a + (b - a) * k;
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];

  // ── Kamera ──────────────────────────────────────────────────────────────
  const cam = $('#cam'), img = $('#camimg');
  const L = R.lagen;
  function rect(t) {
    let r = { ...L[R.kamera[0].lage] };
    for (const k of R.kamera.slice(1)) {
      if (k.t > t) break;
      const z = L[k.lage], q = p(t, k.t, 0.6, E.inOut);
      r = Object.fromEntries(Object.keys(r).map((n) => [n, mix(r[n], z[n], q)]));
    }
    return r;
  }
  function kamera(t) {
    const r = rect(t);
    const z = R.zooms.some(([a, b]) => t >= a && t < b) ? 1.12 : 1;
    const k = Math.max(r.w / R.quelle.w, r.h / R.quelle.h) * z;
    const iw = R.quelle.w * k, ih = R.quelle.h * k;
    const left = cl(r.w / 2 - iw * R.fokus.x, r.w - iw, 0), top = cl(r.h * 0.4 - ih * R.fokus.y, r.h - ih, 0);
    cam.style.cssText = `left:${r.x}px;top:${r.y}px;width:${r.w}px;height:${r.h}px;border-radius:${r.r}px;box-shadow:0 30px 70px rgba(0,0,0,${0.4 * r.sh})`;
    img.style.cssText = `left:${left}px;top:${top}px;width:${iw}px;height:${ih}px`;
  }
  let src = '';
  function bild(t) {
    const n = Math.max(1, Math.min(R.n, Math.round(t * R.fps) + 1));
    const s = R.frames.replace('#', String(n).padStart(5, '0'));
    if (s === src) return Promise.resolve();
    src = s; img.src = s;
    return img.decode().catch(() => {});
  }

  // ── Animationen ─────────────────────────────────────────────────────────
  const A = [];
  for (const sz of $$('.sz')) {
    const s = +sz.dataset.s, e = +sz.dataset.e, bg = $('.bg', sz);
    A.push({ typ: 'sichtbar', el: sz, s, e: e + 0.7 });
    if (bg) A.push({ typ: 'kreis', el: bg, t: s - 0.15, direkt: sz.dataset.direkt === '1' });
    for (const box of $$('.zone, .kopf', sz)) A.push({ typ: 'szene', el: box, s: sz.dataset.direkt === '1' ? s : s + 0.3, e }); // erst wenn die Kamera Platz gemacht hat
    for (const el of $$('[data-t]', sz)) A.push(vorbereiten(el, +el.dataset.t, el.dataset.anim || 'auf'));
  }
  const intro = $('.intro');
  if (intro) {
    $$('[data-t]', intro).forEach((el) => A.push(vorbereiten(el, +el.dataset.t, el.dataset.anim || 'pop')));
    A.push({ typ: 'aus', el: intro, t: R.titel.e - 0.3 });
    A.push({ typ: 'aus', el: $('.verlauf'), t: R.titel.e - 0.3, ein: R.titel.s });
  }
  function vorbereiten(el, t, anim) {
    const a = { typ: anim, el, t };
    if (anim === 'tippen') { a.text = el.dataset.text || el.textContent; el.textContent = ''; }
    if (anim === 'zaehlen') {
      const m = (el.textContent || '').match(/^(\D*)([\d.,]+)(.*)$/s);
      if (m) { a.pre = m[1]; a.post = m[3]; a.dez = (m[2].split(',')[1] || '').length; a.gruppe = m[2].includes('.'); a.ziel = parseFloat(m[2].replace(/\./g, '').replace(',', '.')); }
      else a.typ = 'auf';
    }
    if (anim === 'wachsen') el.style.transformOrigin = 'left center';
    return a;
  }
  function anwenden(a, t) {
    const st = a.el.style;
    switch (a.typ) {
      case 'sichtbar': st.visibility = t >= a.s - 0.2 && t < a.e ? '' : 'hidden'; break;
      case 'kreis': { const r = a.direkt ? (t >= a.t ? 150 : 0) : 150 * p(t, a.t, 0.65, E.inOut); st.clipPath = `circle(${r}% at var(--cx) var(--cy))`; break; }
      case 'szene': {
        const i = p(t, a.s, 0.5, E.out), o = p(t, a.e - 0.3, 0.3, E.in);
        st.opacity = i * (1 - o); st.translate = `0 ${mix(70, 0, i) + 40 * o}px`; st.scale = String(mix(0.96, 1, i) - 0.03 * o); break;
      }
      case 'aus': { const i = a.ein === undefined ? 1 : p(t, a.ein, 0.3, E.lin); st.opacity = i * (1 - p(t, a.t, 0.3, E.in)); break; }
      case 'blende': st.opacity = p(t, a.t, 0.3, E.lin); break;
      case 'auf': { const k = p(t, a.t, 0.45); st.opacity = cl((t - a.t) / 0.25); st.translate = `0 ${mix(44, 0, k)}px`; break; }
      case 'links': case 'rechts': { const k = p(t, a.t, 0.5); st.opacity = cl((t - a.t) / 0.25); st.translate = `${mix(a.typ === 'links' ? -110 : 110, 0, k)}px 0`; break; }
      case 'pop': st.opacity = cl((t - a.t) / 0.16); st.scale = String(t < a.t ? 0.6 : mix(0.6, 1, p(t, a.t, 0.45, E.back))); break;
      case 'tippen': {
        const n = Math.max(0, Math.min(a.text.length, Math.floor((t - a.t) * 22)));
        const caret = t >= a.t && n < a.text.length, k = n + (caret ? '|' : '');
        if (a.k !== k) {
          a.k = k; a.el.textContent = a.text.slice(0, n);
          if (caret) { const c = document.createElement('i'); c.className = 'caret'; a.el.appendChild(c); }
        }
        st.opacity = t >= a.t - 0.05 ? 1 : 0; break;
      }
      case 'zaehlen': {
        const v = a.ziel * p(t, a.t, 1.2, E.out);
        a.el.textContent = a.pre + v.toLocaleString('de-DE', { minimumFractionDigits: a.dez, maximumFractionDigits: a.dez, useGrouping: a.gruppe }) + a.post;
        st.opacity = cl((t - a.t) / 0.2); break;
      }
      case 'wachsen': st.scale = `${p(t, a.t, 0.9, E.inOut)} 1`; st.opacity = t >= a.t ? 1 : 0; break;
      case 'strich': st.setProperty('--m', `${100 * p(t, a.t, 0.5, E.inOut)}%`); break;
    }
  }

  // ── Untertitel ──────────────────────────────────────────────────────────
  const ut = $('.ut'), utp = $('.ut p');
  let key = '';
  function lageBei(t) { let l = R.kamera[0].lage; for (const k of R.kamera) if (k.t <= t + 0.25) l = k.lage; return l; }
  function untertitel(t) {
    if (!ut) return;
    ut.style.top = R.ut_y[lageBei(t)] + 'px';
    const i = R.ut.findIndex((c) => t >= c.s && t < c.e);
    const c = R.ut[i];
    const k = i < 0 ? '' : i + ':' + c.w.map((w) => (w[1] <= t && t < w[2] + 0.12 ? 1 : 0)).join('');
    if (k === key) return;
    key = k;
    utp.innerHTML = i < 0 ? '' : `<span class="l">${c.w.map((w) => `<b class="${w[1] <= t && t < w[2] + 0.12 ? 'on' : ''}">${w[0]}</b>`).join(' ')}</span>`;
  }

  window.SEEK = (t) => {
    kamera(t);
    for (const a of A) anwenden(a, t);
    untertitel(t);
    return bild(t);
  };
  window.READY = Promise.all([document.fonts.ready, ...$$('img').filter((i) => i.src).map((i) => i.decode().catch(() => {}))])
    .then(() => window.SEEK(0)).then(() => true);
  // Vorschau im Browser: ?t=12.5 zeigt ein Standbild, ?play spielt ab
  const q = new URLSearchParams(location.search);
  window.READY.then(() => {
    if (q.has('t')) window.SEEK(+q.get('t'));
    if (q.has('play')) { const t0 = performance.now(); const loop = () => { window.SEEK(((performance.now() - t0) / 1000) % R.dauer); requestAnimationFrame(loop); }; loop(); }
  });
})();
