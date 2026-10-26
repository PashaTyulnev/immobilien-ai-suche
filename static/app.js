"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const eur = v => v == null ? "–" : Math.round(v).toLocaleString("de-DE") + " €";
const img = u => "/img?u=" + encodeURIComponent(u);

const DEFAULT_FILTERS = {
  view: "all", q: "", pricemode: "warm", pmin: "", pmax: "", amin: "", amax: "", rmin: "", rmax: "", flmin: "", flmax: "",
  balcony: false, kitchen: false, elevator: false, garden: false, photos: true, unknown: true,
  novonovia: true, noplatte: true, platte: 50, noprice: true, noswap: true, notemp: true, nofurn: true, nowbs: true, noground: false, dupes: true,
  vonoviaWords: "vonovia, buwog, deutsche wohnen, südost woba, woba dresden, gagfah",
  words: "", sources: {}, sort: "new",
};
let F = loadFilters();
let ALL = [];
let SOURCES = {};
let MATCH = { scores: {}, reasons: {}, mode: "none", likes: 0, dislikes: 0 };
let lastStatusRunning = false;

function loadFilters() {
  try {
    const f = { ...DEFAULT_FILTERS, ...JSON.parse(localStorage.getItem("filters") || "{}") };
    // einmalig: "nur mit Fotos" auch in bereits gespeicherten Filtern einschalten
    if (!f.photosOn) { f.photos = true; f.photosOn = 1; }
    return f;
  } catch { return { ...DEFAULT_FILTERS }; }
}
function saveFilters() { try { localStorage.setItem("filters", JSON.stringify(F)); } catch {} }

// ---------- Ableitungen ----------
function warm(l) {
  if (l.price_warm) return l.price_warm;
  if (l.price_cold && (l.costs_extra || l.costs_heat)) return l.price_cold + (l.costs_extra || 0) + (l.costs_heat || 0);
  return null;
}
const price = l => F.pricemode === "warm" ? (warm(l) ?? (F.unknown ? l.price_cold : null)) : l.price_cold;
const sqm = l => l.price_cold && l.area ? l.price_cold / l.area : null;
const words = s => s.split(/[,;\n]/).map(w => w.trim().toLowerCase()).filter(Boolean);
const textOf = l => (l._text ??= [l.title, l.description, l.provider, l.raw_excerpt, l.district, l.address].join(" ").toLowerCase());
const isVonovia = l => { const w = words(F.vonoviaWords); const t = textOf(l); return w.some(x => t.includes(x)); };
const floorLabel = f => f == null ? null : f === 0 ? "EG" : f < 0 ? "UG" : f + ". OG";
const isNew = l => !l.seen && (Date.now() / 1000 - l.first_seen) < 3 * 86400;

function dupKey(l) {
  if (!l.area || !(l.price_cold || l.price_warm)) return null;
  return [l.zip || "", Math.round(l.area), Math.round((l.price_cold || l.price_warm) / 10)].join("|");
}

// ---------- Filtern ----------
function passes(l, view = F.view) {
  if (view === "hidden") return !!l.hidden;
  if (l.hidden) return false;
  if (view === "fav" && !l.favorite) return false;
  if (!l.active && view !== "fav") return false;  // offline nur noch unter Favoriten (mit Hinweis)
  if (view === "new" && l.seen) return false;
  if (F.sources[l.source] === false) return false;

  const num = v => v === "" || v == null ? null : Number(v);
  const range = (v, lo, hi) => {
    lo = num(lo); hi = num(hi);
    if (lo == null && hi == null) return true;
    if (v == null) return F.unknown;
    return (lo == null || v >= lo) && (hi == null || v <= hi);
  };
  if (!range(price(l), F.pmin, F.pmax)) return false;
  if (!range(l.area, F.amin, F.amax)) return false;
  if (!range(l.rooms, F.rmin, F.rmax)) return false;
  if (!range(l.floor, F.flmin, F.flmax)) return false;

  const must = (v) => v === 1 || (v == null && F.unknown);
  if (F.balcony && !(must(l.balcony) || l.terrace === 1)) return false;
  if (F.kitchen && !must(l.kitchen)) return false;
  if (F.elevator && !must(l.elevator)) return false;
  if (F.garden && !must(l.garden)) return false;
  if (F.photos && !(l.images && l.images.length)) return false;

  if (F.novonovia && isVonovia(l)) return false;
  if (F.noplatte && (l.plattenbau_score ?? 0) >= F.platte) return false;
  if (F.noprice && !(warm(l) || l.price_cold)) return false;
  if (F.noswap && l.swap === 1) return false;
  if (F.notemp && l.temporary === 1) return false;
  if (F.nofurn && l.furnished === 1) return false;
  if (F.nowbs && l.wbs_required === 1) return false;
  if (F.noground && l.floor === 0) return false;
  const ex = words(F.words);
  if (ex.length && ex.some(w => textOf(l).includes(w))) return false;
  if (F.q) {
    const t = textOf(l);
    if (!words(F.q.replace(/\s+/g, ",")).every(w => t.includes(w))) return false;
  }
  return true;
}

function sortFn() {
  const nz = (v, d) => v == null ? d : v;
  const ms = l => MATCH.scores[l.id] ?? -1;
  return {
    match: (a, b) => ms(b) - ms(a) || b.first_seen - a.first_seen,
    new: (a, b) => b.first_seen - a.first_seen,
    price: (a, b) => nz(price(a), 1e9) - nz(price(b), 1e9),
    sqm: (a, b) => nz(sqm(a), 1e9) - nz(sqm(b), 1e9),
    area: (a, b) => nz(b.area, 0) - nz(a.area, 0),
    platte: (a, b) => (a.plattenbau_score ?? 0) - (b.plattenbau_score ?? 0),
  }[F.sort];
}

// ---------- Rendern ----------
function render() {
  $("#swipe").hidden = !F.swipe;
  $("#grid").hidden = !!F.swipe;
  $("#btn-swipe").classList.toggle("on", !!F.swipe);
  if (F.swipe) { $("#empty").hidden = true; renderSwipe(); return; }
  let list = ALL.filter(passes).sort(sortFn());
  if (F.dupes && F.view !== "hidden") {
    const groups = new Map();
    const out = [];
    for (const l of list) {
      const k = dupKey(l);
      if (k && groups.has(k)) { groups.get(k)._dupes.push(l); continue; }
      l._dupes = [];
      if (k) groups.set(k, l);
      out.push(l);
    }
    list = out;
  }
  const active = ALL.filter(l => l.active && !l.hidden).length;
  const newCount = ALL.filter(l => !l.hidden && isNew(l)).length;
  $("#counts").textContent = `${list.length} angezeigt · ${active} aktiv gesamt · ${newCount} neu`;
  $("#empty").hidden = list.length > 0;
  const grid = $("#grid");
  grid.innerHTML = list.slice(0, 600).map(card).join("");
}

function card(l) {
  const w = warm(l);
  const pScore = l.plattenbau_score ?? 0;
  const badges = [
    MATCH.scores[l.id] != null ? `<span class="badge match">${MATCH.scores[l.id]}% Match</span>` : "",
    l.rating === 1 ? `<span class="badge match">♥</span>` : "",
    isNew(l) ? `<span class="badge new">NEU</span>` : "",
    `<span class="badge">${esc(SOURCES[l.source] || l.source)}</span>`,
    pScore >= 50 ? `<span class="badge bad">Platte ${pScore}</span>` : pScore >= 30 ? `<span class="badge warn">Platte? ${pScore}</span>` : "",
    isVonovia(l) ? `<span class="badge bad">Vonovia</span>` : "",
    l.swap === 1 ? `<span class="badge bad">Tausch</span>` : "",
    l.temporary === 1 ? `<span class="badge warn">befristet</span>` : "",
    l.furnished === 1 ? `<span class="badge warn">möbliert</span>` : "",
    l.wbs_required === 1 ? `<span class="badge warn">WBS</span>` : "",
    !l.active ? `<span class="badge bad">offline</span>` : "",
    l._dupes?.length ? `<span class="badge">+${l._dupes.length} Portal${l._dupes.length > 1 ? "e" : ""}</span>` : "",
  ].join("");
  const facts = [
    l.area && `${String(l.area).replace(".", ",")} m²`,
    l.rooms && `${String(l.rooms).replace(".", ",")} Zi.`,
    floorLabel(l.floor) && (floorLabel(l.floor) + (l.floors_total ? ` / ${l.floors_total}` : "")),
    sqm(l) && `${sqm(l).toFixed(2).replace(".", ",")} €/m²`,
    l.year_built && `Bj. ${l.year_built}`,
  ].filter(Boolean).map(f => `<span class="fact">${f}</span>`);
  const yes = [l.balcony === 1 && "Balkon", l.terrace === 1 && "Terrasse", l.garden === 1 && "Garten",
               l.kitchen === 1 && "EBK", l.elevator === 1 && "Aufzug"].filter(Boolean).map(f => `<span class="fact yes">${f}</span>`);
  const pic = l.images && l.images[0];
  return `<article class="card ${l.seen ? "seen" : ""} ${l.active ? "" : "gone"}" data-id="${esc(l.id)}">
    <div class="thumb">
      ${pic ? `<img loading="lazy" src="${img(pic)}" data-u="${esc(pic)}" onerror="imgFail(this)" alt="">` : `<div class="noimg">kein Foto</div>`}
      <div class="badges">${badges}</div>
      <div class="card-actions">
        <button class="iconbtn ${l.favorite ? "on" : ""}" data-act="fav" title="Favorit">${l.favorite ? "★" : "☆"}</button>
        <button class="iconbtn" data-act="hide" title="${l.hidden ? "Wieder einblenden" : "Ausblenden"}">${l.hidden ? "↺" : "✕"}</button>
      </div>
      ${l.images?.length > 1 ? `<span class="badge imgcount">${l.images.length} Fotos</span>` : ""}
    </div>
    <div class="card-body">
      <div class="price">${w || l.price_cold
        ? `<b>${w ? eur(w) : eur(l.price_cold)}</b><small>${w ? "warm" + (l.price_cold ? ` · ${eur(l.price_cold)} kalt` : "") : "kalt"}</small>`
        : `<small>${l.detail_done ? "Preis unbekannt" : "Details werden geladen…"}</small>`}</div>
      <div class="title">${esc(l.title || "(ohne Titel)")}</div>
      <div class="where">${esc([l.district, l.zip].filter(Boolean).join(" · ") || "Dresden")}${l.provider ? " · " + esc(l.provider) : ""}</div>
      <div class="facts">${facts.join("")}${yes.join("")}</div>
      ${MATCH.reasons[l.id]?.length ? `<div class="match-why">passt: ${esc(MATCH.reasons[l.id].join(" · "))}</div>` : ""}
      <div class="meta"><span>${l.status ? esc(l.status) : ""}${l.note ? " 📝" : ""}</span><span>${ago(l.first_seen)}</span></div>
    </div>
  </article>`;
}

function ago(ts) {
  const m = (Date.now() / 1000 - ts) / 60;
  if (m < 60) return `vor ${Math.max(1, Math.round(m))} Min.`;
  if (m < 1440) return `vor ${Math.round(m / 60)} Std.`;
  return `vor ${Math.round(m / 1440)} Tg.`;
}

// ---------- Detailansicht ----------
async function openDetail(id) {
  const l = await (await fetch("/api/listings/" + encodeURIComponent(id))).json();
  const local = ALL.find(x => x.id === id);
  if (!l.seen) { patch(id, { seen: 1 }); }
  const imgs = l.images || [];
  let idx = 0;
  const p = l.plattenbau_score ?? 0;
  const yn = v => v === 1 ? "ja" : v === 0 ? "nein" : "–";
  const rows = [
    ["Warmmiete", eur(warm(l)) + (l.price_warm ? "" : warm(l) ? " (berechnet)" : "")],
    ["Kaltmiete", eur(l.price_cold)], ["Nebenkosten", eur(l.costs_extra)], ["Heizkosten", eur(l.costs_heat)],
    ["Kaution", eur(l.deposit)], ["€/m² kalt", sqm(l) ? sqm(l).toFixed(2).replace(".", ",") + " €" : "–"],
    ["Fläche", l.area ? `${String(l.area).replace(".", ",")} m²` : "–"], ["Zimmer", l.rooms ?? "–"],
    ["Etage", (floorLabel(l.floor) ?? "–") + (l.floors_total ? ` von ${l.floors_total}` : "")],
    ["Baujahr", l.year_built ?? "–"], ["Gebäudetyp (KI)", l.building_type ?? "–"],
    ["Balkon", yn(l.balcony)], ["Terrasse", yn(l.terrace)], ["Garten", yn(l.garden)], ["Einbauküche", yn(l.kitchen)],
    ["Aufzug", yn(l.elevator)], ["Möbliert", yn(l.furnished)], ["Befristet", yn(l.temporary)], ["Tausch", yn(l.swap)], ["WBS nötig", yn(l.wbs_required)],
    ["Frei ab", l.available_from ?? "–"], ["Adresse", l.address || [l.zip, l.district].filter(Boolean).join(" ") || "–"],
    ["Anbieter", l.provider ?? "–"], ["Quelle", SOURCES[l.source] || l.source], ["Gefunden", new Date(l.first_seen * 1000).toLocaleString("de-DE")],
    ["Zuletzt in Liste", new Date(l.last_seen * 1000).toLocaleString("de-DE")],
  ];
  const statuses = ["", "interessant", "angefragt", "Besichtigung", "beworben", "abgesagt", "passt nicht"];
  const dlg = $("#detail");
  dlg.innerHTML = `
    <div class="d-head">
      <div><h2>${esc(l.title)}</h2><div class="sub">${esc([l.district, l.zip].filter(Boolean).join(" · "))}</div></div>
      <div style="display:flex;gap:6px">
        <a class="btn primary" href="${esc(l.url)}" target="_blank" rel="noopener">Zum Inserat ↗</a>
        <button class="btn" data-close>✕</button>
      </div>
    </div>
    <div class="d-grid">
      <div class="gallery">
        <div class="main">
          ${imgs.length ? `<img id="g-main" src="${img(imgs[0])}" alt="">` : `<div class="noimg" style="color:#aaa;display:grid;place-items:center;height:100%">keine Fotos</div>`}
          ${imgs.length > 1 ? `<button class="iconbtn nav prev" data-g="-1">‹</button><button class="iconbtn nav next" data-g="1">›</button>
          <span class="badge counter" id="g-count">1 / ${imgs.length}</span>` : ""}
        </div>
        <div class="thumbs">${imgs.map((u, i) => `<img loading="lazy" src="${img(u)}" data-i="${i}" class="${i ? "" : "active"}">`).join("")}</div>
      </div>
      <div>
        <div class="d-actions">
          <button class="btn" data-dfav>${l.favorite ? "★ Favorit" : "☆ Merken"}</button>
          <button class="btn" data-dhide>${l.hidden ? "↺ Einblenden" : "✕ Ausblenden"}</button>
          <button class="btn" data-dcheck>Noch online?</button>
          <span class="online-state" id="d-online">${onlineText(l)}</span>
          <select id="d-status">${statuses.map(s => `<option ${s === (l.status || "") ? "selected" : ""} value="${s}">${s || "– Status –"}</option>`).join("")}</select>
        </div>
        <textarea class="d-note" id="d-note" placeholder="Notizen (z.B. Besichtigungstermin, Eindruck)">${esc(l.note || "")}</textarea>
        ${l.summary ? `<div class="box"><h4>KI-Zusammenfassung</h4>${esc(l.summary)}</div>` : ""}
        <div class="box ${p >= 50 ? "platte-hi" : p >= 30 ? "platte-mid" : ""}"><h4>Plattenbau-Score: ${p} / 100</h4>
          ${(l.plattenbau_reasons || []).map(esc).join(" · ") || "keine Hinweise"}</div>
        <dl class="kv">${rows.map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join("")}</dl>
      </div>
    </div>
    <div class="desc">${esc(l.description || l.raw_text || "")}</div>`;
  const show = i => {
    idx = (i + imgs.length) % imgs.length;
    $("#g-main", dlg).src = img(imgs[idx]);
    $$(".thumbs img", dlg).forEach((t, j) => t.classList.toggle("active", j === idx));
    const c = $("#g-count", dlg); if (c) c.textContent = `${idx + 1} / ${imgs.length}`;
    $$(".thumbs img", dlg)[idx]?.scrollIntoView({ block: "nearest", inline: "nearest" });
  };
  dlg.onclick = e => {
    if (e.target === dlg || e.target.closest("[data-close]")) dlg.close();
    const g = e.target.closest("[data-g]"); if (g) show(idx + Number(g.dataset.g));
    const t = e.target.closest(".thumbs img"); if (t) show(Number(t.dataset.i));
    if (e.target.closest("[data-dfav]")) { toggle(id, "favorite"); e.target.textContent = local.favorite ? "★ Favorit" : "☆ Merken"; }
    if (e.target.closest("[data-dhide]")) { toggle(id, "hidden"); dlg.close(); }
    if (e.target.closest("[data-dcheck]")) checkOnline(id);
  };
  dlg.onkeydown = e => { if (imgs.length > 1 && e.key === "ArrowRight") show(idx + 1); if (imgs.length > 1 && e.key === "ArrowLeft") show(idx - 1); };
  $("#d-status", dlg).onchange = e => patch(id, { status: e.target.value });
  $("#d-note", dlg).onchange = e => patch(id, { note: e.target.value });
  dlg.showModal();
}

function onlineText(l) {
  const when = l.checked_at ? ` (geprüft ${new Date(l.checked_at * 1000).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" })})` : "";
  return l.active ? `<span class="ok">● online</span>${when}` : `<span class="gone">● offline</span>${when}`;
}

async function checkOnline(id) {
  const el = $("#d-online");
  el.textContent = "prüfe …";
  let res = {};
  try { res = await (await fetch("/api/check/" + encodeURIComponent(id), { method: "POST" })).json(); } catch {}
  const l = ALL.find(x => x.id === id);
  if (res.online == null) { el.textContent = "nicht prüfbar – Portal blockt gerade"; return; }
  if (l) Object.assign(l, { active: res.online ? 1 : 0, checked_at: res.checked_at });
  el.innerHTML = onlineText(l || { active: res.online, checked_at: res.checked_at });
  render();
}

async function patch(id, data) {
  const l = ALL.find(x => x.id === id);
  if (l) Object.assign(l, data);
  render();
  await fetch("/api/listings/" + encodeURIComponent(id), { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
}
function toggle(id, field) {
  const l = ALL.find(x => x.id === id);
  patch(id, { [field]: l[field] ? 0 : 1 });
}

// ---------- Swipe-Ansicht ----------
const UNDO = [];
let swCurrent = null, swPhoto = 0, swDragging = false, swForce = false;

function swipeDeck() {
  // Unbewertete Wohnungen aus den aktuellen Filtern; mit Modell die besten Matches zuerst
  const byMatch = (a, b) => (MATCH.scores[b.id] ?? 0) - (MATCH.scores[a.id] ?? 0) || b.first_seen - a.first_seen;
  const byNew = (a, b) => b.first_seen - a.first_seen;
  return ALL.filter(l => l.rating == null && passes(l, "all")).sort(MATCH.mode === "none" ? byNew : byMatch);
}

function pickNext(deck) {
  // Jede 5. Karte zufällig aus dem Rest, damit das Modell nicht nur Bekanntes bestätigt
  const n = MATCH.likes + MATCH.dislikes;
  if (MATCH.mode !== "none" && n % 5 === 4 && deck.length > 20) return deck[10 + Math.floor(Math.random() * (deck.length - 10))];
  return deck[0];
}

// Schnellgründe für "Warum nicht?" – müssen zu CHIPS in app/match.py passen
const CHIPS = ["zu teuer", "zu klein", "zu groß", "Lage", "Plattenbau/Block", "Erdgeschoss", "kein Balkon",
               "befristet", "dunkel", "hässlich/alt", "Grundriss", "Anbieter"];
let swReasonFor = null;  // zuletzt abgelehnte Wohnung, zu der man noch einen Grund angeben kann

function swCard(l, back) {
  const imgs = l.images || [];
  if (back) {
    return `<div class="sw-card back"><div class="sw-media"><div class="sw-photo">
      ${imgs.length ? `<img src="${img(imgs[0])}" alt="">` : ""}</div></div></div>`;
  }
  const w = warm(l);
  const facts = [
    l.area && `${String(l.area).replace(".", ",")} m²`, l.rooms && `${String(l.rooms).replace(".", ",")} Zi.`,
    floorLabel(l.floor) && floorLabel(l.floor) + (l.floors_total ? ` / ${l.floors_total}` : ""),
    l.year_built && `Bj. ${l.year_built}`, sqm(l) && `${sqm(l).toFixed(2).replace(".", ",")} €/m²`,
  ].filter(Boolean).map(f => `<span class="fact">${f}</span>`);
  const yes = [l.balcony === 1 && "Balkon", l.terrace === 1 && "Terrasse", l.garden === 1 && "Garten",
               l.kitchen === 1 && "EBK", l.elevator === 1 && "Aufzug"].filter(Boolean).map(f => `<span class="fact yes">${f}</span>`);
  const pScore = l.plattenbau_score ?? 0;
  const tags = [
    MATCH.scores[l.id] != null ? `<span class="badge match">${MATCH.scores[l.id]}% Match</span>` : "",
    `<span class="badge">${esc(SOURCES[l.source] || l.source)}</span>`,
    pScore >= 50 ? `<span class="badge bad">Platte ${pScore}</span>` : pScore >= 30 ? `<span class="badge warn">Platte? ${pScore}</span>` : "",
    isVonovia(l) ? `<span class="badge bad">Vonovia</span>` : "",
    l.temporary === 1 ? `<span class="badge warn">befristet</span>` : "",
    l.furnished === 1 ? `<span class="badge warn">möbliert</span>` : "",
  ].join("");
  const why = MATCH.reasons[l.id]?.length ? `<div class="sw-why">♥ passt: ${esc(MATCH.reasons[l.id].join(" · "))}</div>` : "";
  const warn = MATCH.warnings?.[l.id]?.length ? `<div class="sw-warn">${esc(MATCH.warnings[l.id].join(" · "))}</div>` : "";
  const yn = v => v === 1 ? "ja" : v === 0 ? "nein" : "–";
  const kv = [
    ["Warmmiete", eur(w) + (l.price_warm || !w ? "" : " (berechnet)")], ["Kaltmiete", eur(l.price_cold)],
    ["Nebenkosten", eur(l.costs_extra)], ["Heizkosten", eur(l.costs_heat)], ["Kaution", eur(l.deposit)],
    ["Etage", (floorLabel(l.floor) ?? "–") + (l.floors_total ? ` von ${l.floors_total}` : "")],
    ["Baujahr", l.year_built ?? "–"], ["Gebäudetyp", l.building_type ?? "–"], ["Einbauküche", yn(l.kitchen)],
    ["Aufzug", yn(l.elevator)], ["Frei ab", l.available_from ?? "–"],
    ["Adresse", l.address || [l.zip, l.district].filter(Boolean).join(" ") || "–"], ["Anbieter", l.provider ?? "–"],
  ];
  const n = imgs.length;
  return `<div class="sw-card" data-id="${esc(l.id)}">
    <div class="sw-media">
      <div class="sw-photo">
        ${n ? `<div class="sw-bg" id="sw-bg" style="background-image:url('${img(imgs[swPhoto % n])}')"></div>
               <img id="sw-img" src="${img(imgs[swPhoto % n])}" data-u="${esc(imgs[swPhoto % n])}" onerror="imgFail(this)" alt="">`
             : `<div class="sw-nophoto">kein Foto</div>`}
        ${n > 1 ? `<div class="sw-dots">${imgs.slice(0, 20).map((_, i) => `<span class="${i === swPhoto % n ? "on" : ""}"></span>`).join("")}</div>` : ""}
        <div class="sw-tags">${tags}</div>
        <div class="sw-stamp like">GEFÄLLT</div><div class="sw-stamp nope">NEIN</div>
        ${n > 1 ? `<button class="iconbtn sw-nav prev" data-step="-1" title="Foto zurück">‹</button>
                   <button class="iconbtn sw-nav next" data-step="1" title="Foto weiter">›</button>
                   <span class="badge sw-count" id="sw-count">${swPhoto % n + 1} / ${n}</span>` : ""}
      </div>
      ${n > 1 ? `<div class="sw-thumbs">${imgs.map((u, i) => `<img loading="lazy" data-i="${i}" class="${i === swPhoto % n ? "active" : ""}" src="${img(u)}" data-u="${esc(u)}" onerror="imgFail(this)" alt="">`).join("")}</div>` : ""}
    </div>
    <div class="sw-details">
      <div class="sw-price">${w ? eur(w) : eur(l.price_cold)}<small>${w ? "warm" + (l.price_cold ? " · " + eur(l.price_cold) + " kalt" : "") : "kalt"}</small></div>
      <h3 class="sw-title">${esc(l.title || "")}</h3>
      <div class="where">${esc([l.district, l.zip].filter(Boolean).join(" · ") || "Dresden")}</div>
      <div class="facts">${facts.join("")}${yes.join("")}</div>
      ${why}${warn}
      ${l.summary ? `<div class="box"><h4>KI-Zusammenfassung</h4>${esc(l.summary)}</div>` : ""}
      <dl class="kv">${kv.map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join("")}</dl>
      <div class="box ${pScore >= 50 ? "platte-hi" : pScore >= 30 ? "platte-mid" : ""}"><h4>Plattenbau-Score ${pScore} / 100</h4>
        ${(l.plattenbau_reasons || []).map(esc).join(" · ") || "keine Hinweise"}</div>
      ${l.description ? `<div class="box"><h4>Beschreibung</h4><div class="sw-desc">${esc(l.description)}</div></div>` : ""}
      <a class="btn primary sw-link" href="${esc(l.url)}" target="_blank" rel="noopener">Zum Inserat ↗</a>
    </div>
  </div>`;
}

function renderSwipe() {
  if (swDragging) return;
  if (!MATCH.loaded) {  // erst Match-Werte holen, sonst käme zuerst einfach das neueste statt das beste Inserat
    $("#sw-stage").innerHTML = `<div class="sw-empty">Lade passende Wohnungen …</div>`;
    return;
  }
  const deck = swipeDeck();
  // Aktuelle Karte über die ID halten: beim Nachladen während eines Suchlaufs wird ALL durch neue Objekte ersetzt
  const keep = swCurrent && deck.find(l => l.id === swCurrent.id);
  if (keep) swCurrent = keep;
  else { swCurrent = pickNext(deck); swPhoto = 0; }
  const learned = MATCH.mode === "none"
    ? "Like ein paar Wohnungen – danach sortiere ich nach deinem Geschmack."
    : `${MATCH.likes} ♥ · ${MATCH.dislikes} ✕ · ${MATCH.mode === "gelernt" ? "Modell gelernt" : MATCH.mode === "Gründe" ? "nutzt deine Gründe" : "sucht Ähnliche (ab 2 ♥ und 2 ✕ lernt es richtig)"}`;
  const prof = (MATCH.profile || []).map(([k, c]) => `${k} ${c}×`).join(" · ");
  $("#sw-info").textContent = `${learned} · noch ${deck.length} offen` + (prof ? ` · abgelehnt wegen: ${prof}` : "");
  $("#counts").textContent = `Swipen · ${ALL.filter(l => l.rating === 1).length} ♥ gespeichert (unter ★ Favoriten)`;
  renderReason();
  const stage = $("#sw-stage");
  if (!swCurrent) {
    stage.innerHTML = `<div class="sw-empty">Keine unbewerteten Wohnungen für diese Filter.<br>Filter lockern oder neuen Suchlauf starten.</div>`;
    return;
  }
  // Gleiche Karte schon zu sehen -> nicht neu aufbauen (sonst springen Foto und Scrollposition beim Lesen)
  if (!swForce && $(".sw-card:not(.back)", stage)?.dataset.id === swCurrent.id) return;
  swForce = false;
  const next = deck.find(l => l.id !== swCurrent.id);
  stage.innerHTML = (next ? swCard(next, true) : "") + swCard(swCurrent, false);
  bindDrag($(".sw-card:not(.back)", stage));
  // nächste Fotos vorladen, damit das Durchklicken flüssig ist
  [...(swCurrent.images || []).slice(1, 4), next?.images?.[0]].filter(Boolean).forEach(u => { new Image().src = img(u); });
}

function bindDrag(card) {
  const photo = $(".sw-photo", card);
  let x0 = 0, y0 = 0, dx = 0, dy = 0, down = false;
  const stamps = () => {
    $(".sw-stamp.like", card).style.opacity = Math.max(0, Math.min(1, dx / 100));
    $(".sw-stamp.nope", card).style.opacity = Math.max(0, Math.min(1, -dx / 100));
  };
  photo.addEventListener("pointerdown", e => {
    if (e.target.closest("button")) return;
    down = true; x0 = e.clientX; y0 = e.clientY; dx = dy = 0;
    photo.setPointerCapture(e.pointerId); card.classList.add("dragging");
  });
  photo.addEventListener("pointermove", e => {
    if (!down) return;
    dx = e.clientX - x0; dy = e.clientY - y0;
    if (Math.abs(dx) > 5) swDragging = true;
    card.style.transform = `translate(${dx}px, ${dy * 0.25}px) rotate(${dx / 18}deg)`;
    stamps();
  });
  const end = e => {
    if (!down) return;
    down = false; card.classList.remove("dragging");
    swDragging = false;
    if (dx > 110) { decide(true); return; }
    if (dx < -110) {
      card.classList.add("snap"); card.style.transform = ""; dx = 0; stamps();
      setTimeout(() => card.classList.remove("snap"), 200);
      askReason();
      return;
    }
    if (Math.abs(dx) < 6 && Math.abs(dy) < 6) {  // Tippen: linkes Drittel = Foto zurück, sonst weiter
      const r = photo.getBoundingClientRect();
      photoStep(e.clientX - r.left < r.width * 0.3 ? -1 : 1);
    }
    card.classList.add("snap"); card.style.transform = ""; dx = 0; stamps();
    setTimeout(() => card.classList.remove("snap"), 200);
  };
  photo.addEventListener("pointerup", end);
  photo.addEventListener("pointercancel", end);
  card.addEventListener("click", e => {
    const s = e.target.closest("[data-step]"); if (s) setPhoto(swPhoto + Number(s.dataset.step));
    const t = e.target.closest(".sw-thumbs img"); if (t) setPhoto(Number(t.dataset.i));
  });
}

const BROKEN = new Set();  // Fotos, die nicht laden – bleiben auch nach dem Nachladen der Liste draußen

function imgFail(el) {
  // Foto nicht ladbar (z.B. vom Portal gelöscht) -> aus der Liste werfen; ohne Fotos verschwindet das Inserat
  BROKEN.add(el.dataset.u);
  const l = ALL.find(x => x.id === el.closest("[data-id]")?.dataset.id);
  if (!l || !l.images) return;
  l.images = l.images.filter(u => u !== el.dataset.u);
  if (l.id === swCurrent?.id) { swPhoto = 0; swForce = true; }
  clearTimeout(imgFail.t);
  imgFail.t = setTimeout(render, 300);
}

function setPhoto(i) {
  // nur das Bild tauschen, damit der Infobereich nicht nach oben springt
  const imgs = swCurrent?.images || [];
  if (imgs.length < 2) return;
  swPhoto = (i + imgs.length) % imgs.length;
  $("#sw-img").src = img(imgs[swPhoto]);
  $("#sw-img").dataset.u = imgs[swPhoto];
  $("#sw-bg").style.backgroundImage = `url('${img(imgs[swPhoto])}')`;
  $$(".sw-dots span").forEach((d, j) => d.classList.toggle("on", j === swPhoto));
  $$(".sw-thumbs img").forEach((t, j) => t.classList.toggle("active", j === swPhoto));
  $$(".sw-thumbs img")[swPhoto]?.scrollIntoView({ block: "nearest", inline: "nearest" });
  const c = $("#sw-count"); if (c) c.textContent = `${swPhoto + 1} / ${imgs.length}`;
  if (imgs[swPhoto + 1]) new Image().src = img(imgs[swPhoto + 1]);
}
const photoStep = d => setPhoto(swPhoto + d);

function decide(like, reason) {
  const card = $("#sw-stage .sw-card:not(.back)");
  if (!swCurrent || !card) return;
  const l = swCurrent;
  closeAsk();
  card.classList.add("fly");
  card.style.transform = `translate(${like ? 140 : -140}%, 40px) rotate(${like ? 25 : -25}deg)`;
  card.style.opacity = "0";
  UNDO.push({ id: l.id, rating: l.rating ?? null, favorite: l.favorite || 0, hidden: l.hidden || 0, reason: l.reason ?? null });
  setTimeout(() => {
    swCurrent = null;
    patch(l.id, like ? { rating: 1, favorite: 1, seen: 1 } : { rating: -1, hidden: 1, seen: 1, reason: reason || null });
    MATCH[like ? "likes" : "dislikes"]++;
    refreshMatch();
  }, 280);
}

// ---------- "Warum nicht?" – wird beim Nein zuerst gefragt ----------
function parseReason(r) {
  const [chips, ...free] = (r || "").split(";");
  return { chips: chips.split(",").map(s => s.trim()).filter(c => CHIPS.includes(c)), free: free.join(";").trim() };
}

function askReason() {
  // Karte bleibt stehen, das Grund-Feld legt sich darüber
  if (!swCurrent) return;
  if (swReasonFor) { confirmNo(); return; }  // zweites ✕ / ← = ablehnen
  swReasonFor = swCurrent;
  const box = $("#sw-reason");
  const { chips, free } = parseReason(swCurrent.reason);
  box.innerHTML = `
    <div class="sw-reason-head"><b>Warum nicht?</b>
      <span class="sub">${esc((swCurrent.title || "").slice(0, 60))}</span>
      <button class="iconbtn" data-cancel-reason title="Abbrechen (Esc)">✕</button></div>
    <div class="chips">${CHIPS.map((c, i) => `<button class="chip ${chips.includes(c) ? "on" : ""}" data-chip="${esc(c)}">${i < 9 ? `<kbd>${i + 1}</kbd>` : ""}${esc(c)}</button>`).join("")}</div>
    <input id="sw-reason-text" type="text" placeholder="Eigener Grund, z.B. Gasheizung, kleines Bad, Straße zu laut …" value="${esc(free)}">
    <div class="sw-reason-actions">
      <button class="btn" data-cancel-reason>Doch nicht <kbd>Esc</kbd></button>
      <button class="btn danger" data-confirm-no>✕ Ablehnen <kbd>Enter</kbd></button>
    </div>`;
  box.hidden = false;
}

function currentReason() {
  const chips = $$("#sw-reason .chip.on").map(b => b.dataset.chip);
  const free = ($("#sw-reason-text")?.value || "").trim();
  return chips.join(", ") + (free ? "; " + free : "");
}

function confirmNo() {
  if (!swReasonFor) return;
  decide(false, currentReason());
}

function closeAsk() {
  swReasonFor = null;
  const box = $("#sw-reason");
  box.hidden = true;
  box.innerHTML = "";
}

function renderReason() {
  // Grund-Feld schließen, falls die gefragte Karte nicht mehr die aktuelle ist
  if (swReasonFor && swReasonFor.id !== swCurrent?.id) closeAsk();
}

function undo() {
  const u = UNDO.pop();
  if (!u) return;
  closeAsk();
  const { id, ...prev } = u;
  patch(id, prev);
  swCurrent = ALL.find(x => x.id === id) || null;
  swPhoto = 0;
  render();
  refreshMatch();
}

async function refreshMatch() {
  clearTimeout(refreshMatch.t);
  refreshMatch.t = setTimeout(async () => {
    try {
      const r = await fetch("/api/match");
      if (r.ok) MATCH = await r.json();
    } catch {}
    MATCH.loaded = true;
    render();
  }, 250);
}

function bindSwipe() {
  $("#btn-swipe").onclick = () => { F.swipe = !F.swipe; saveFilters(); render(); };
  $("#sw-yes").onclick = () => decide(true);
  $("#sw-no").onclick = askReason;
  $("#sw-undo").onclick = undo;
  $("#sw-info-btn").onclick = () => swCurrent && openDetail(swCurrent.id);
  const rb = $("#sw-reason");
  rb.addEventListener("click", e => {
    const c = e.target.closest("[data-chip]");
    if (c) c.classList.toggle("on");
    if (e.target.closest("[data-confirm-no]")) confirmNo();
    if (e.target.closest("[data-cancel-reason]")) closeAsk();
  });
  rb.addEventListener("keydown", e => {
    if (e.key === "Enter") { e.preventDefault(); confirmNo(); }
    if (e.key === "Escape") { e.preventDefault(); closeAsk(); }
  });
  document.addEventListener("keydown", e => {
    if (!F.swipe || $("#detail").open || /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName)) return;
    if (swReasonFor) {  // Grund-Feld offen
      if (/^[1-9]$/.test(e.key)) $$("#sw-reason .chip")[Number(e.key) - 1]?.classList.toggle("on");
      else if (e.key === "Enter" || e.key === "ArrowLeft") { e.preventDefault(); confirmNo(); }
      else if (e.key === "Escape") closeAsk();
      else if (e.key.toLowerCase() === "t") { e.preventDefault(); $("#sw-reason-text")?.focus(); }
      return;
    }
    if (e.key === "ArrowRight") decide(true);
    else if (e.key === "ArrowLeft") askReason();
    else if (e.key === " ") { e.preventDefault(); photoStep(1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); swCurrent && openDetail(swCurrent.id); }
    else if (e.key.toLowerCase() === "z" || e.key === "Backspace") undo();
  });
}

// ---------- Filter-UI ----------
const BIND = {
  "#f-q": "q", "#f-pricemode": "pricemode", "#f-pmin": "pmin", "#f-pmax": "pmax", "#f-amin": "amin", "#f-amax": "amax",
  "#f-rmin": "rmin", "#f-rmax": "rmax", "#f-flmin": "flmin", "#f-flmax": "flmax", "#f-balcony": "balcony", "#f-kitchen": "kitchen",
  "#f-elevator": "elevator", "#f-garden": "garden", "#f-photos": "photos", "#f-unknown": "unknown", "#f-novonovia": "novonovia",
  "#f-noplatte": "noplatte", "#f-platte": "platte", "#f-noprice": "noprice", "#f-noswap": "noswap", "#f-notemp": "notemp", "#f-nofurn": "nofurn", "#f-nowbs": "nowbs",
  "#f-noground": "noground", "#f-dupes": "dupes", "#f-vonovia-words": "vonoviaWords", "#f-words": "words", "#f-sort": "sort",
};
function syncInputs() {
  for (const [sel, key] of Object.entries(BIND)) {
    const el = $(sel);
    if (el.type === "checkbox") el.checked = !!F[key]; else el.value = F[key];
  }
  $("#platte-val").textContent = F.platte;
  $$("#view-tabs button").forEach(b => b.classList.toggle("active", b.dataset.view === F.view));
  renderSources();
}
function renderSources() {
  $("#f-sources").innerHTML = Object.entries(SOURCES).map(([k, v]) =>
    `<label class="chk"><input type="checkbox" data-src="${k}" ${F.sources[k] === false ? "" : "checked"}> ${esc(v)}
     <span class="sub">(${ALL.filter(l => l.source === k && l.active).length})</span></label>`).join("");
}
function bindInputs() {
  for (const [sel, key] of Object.entries(BIND)) {
    const el = $(sel);
    el.addEventListener(el.tagName === "TEXTAREA" || el.type === "search" || el.type === "number" ? "input" : "change", () => {
      F[key] = el.type === "checkbox" ? el.checked : el.type === "range" ? Number(el.value) : el.value;
      if (key === "vonoviaWords" || key === "words") ALL.forEach(l => delete l._text);
      $("#platte-val").textContent = F.platte;
      saveFilters(); render();
    });
  }
  $("#f-platte").addEventListener("input", e => { $("#platte-val").textContent = e.target.value; F.platte = Number(e.target.value); render(); });
  $("#f-sources").addEventListener("change", e => { F.sources[e.target.dataset.src] = e.target.checked; saveFilters(); render(); });
  $("#view-tabs").addEventListener("click", e => {
    const b = e.target.closest("button"); if (!b) return;
    F.view = b.dataset.view; saveFilters(); syncInputs(); render();
  });
  $("#btn-reset").onclick = () => { F = { ...DEFAULT_FILTERS, sources: {} }; saveFilters(); syncInputs(); render(); };
  $("#btn-filters").onclick = () => $("#sidebar").classList.toggle("open");
  $("#btn-scrape").onclick = async () => { await fetch("/api/scrape", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); pollStatus(); };
  $("#grid").addEventListener("click", e => {
    const c = e.target.closest(".card"); if (!c) return;
    const act = e.target.closest("[data-act]");
    if (act) { e.stopPropagation(); toggle(c.dataset.id, act.dataset.act === "fav" ? "favorite" : "hidden"); return; }
    openDetail(c.dataset.id);
  });
}

// ---------- Daten & Status ----------
async function loadListings() {
  ALL = await (await fetch("/api/listings")).json();
  if (BROKEN.size) ALL.forEach(l => { if (l.images) l.images = l.images.filter(u => !BROKEN.has(u)); });
  renderSources();
  render();
}

async function pollStatus() {
  const s = await (await fetch("/api/status")).json();
  SOURCES = s.sources_available;
  const el = $("#runstatus");
  el.title = (s.log || []).slice(-15).join("\n");
  $("#btn-scrape").disabled = s.running;
  if (s.running) {
    const parts = Object.entries(s.sources).map(([k, v]) =>
      v.phase === "Exposés" ? `${v.label}: Exposé ${v.details}/${v.details_total}` : v.phase === "fertig" ? `${v.label} ✓` : `${v.label}: Seite ${v.page}`);
    const llm = s.llm.phase === "läuft" ? ` · KI: ${s.llm.done} fertig, ${s.llm.todo} offen` : "";
    el.innerHTML = `<span class="dot"></span>Suchlauf: ${esc(parts.join(" · "))}${esc(llm)}`;
  } else {
    const t = s.last_run?.finished;
    el.textContent = t ? `Letzter Suchlauf: ${new Date(t * 1000).toLocaleString("de-DE")}` + (s.llm.phase && s.llm.phase !== "fertig" ? ` · KI: ${s.llm.phase}` : "") : "Noch kein Suchlauf";
  }
  // Während eines Laufs regelmäßig neu laden, am Ende einmal final
  if (s.running || lastStatusRunning) await loadListings();
  lastStatusRunning = s.running;
  clearTimeout(pollStatus.t);
  pollStatus.t = setTimeout(pollStatus, s.running ? 15000 : 60000);
}

(async function init() {
  bindInputs();
  bindSwipe();
  syncInputs();
  refreshMatch();
  await pollStatus();
  await loadListings();
})();
