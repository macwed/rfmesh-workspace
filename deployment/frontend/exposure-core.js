"use strict";

// Generic boot for an exposure use-case page. Each page (jam/hide/emit/ideal)
// supplies a config and calls bootExposurePage(cfg). Reuses window.RFCore.
// Setup flow is explicit numbered steps so the operator knows what to place.

const PAGES = [
  { key: "link", href: "link.html", label: "Connect nodes" },
  { key: "locate", href: "locate.html", label: "Locate jammer" },
  { key: "jam", href: "jam.html", label: "Avoid jam", expertOnly: true },
  { key: "hide", href: "hide.html", label: "Hide" },
  { key: "emit", href: "emit.html", label: "Site emitter", expertOnly: true },
  { key: "ideal", href: "ideal.html", label: "Ideal site", expertOnly: true },
];

function bootExposurePage(cfg) {
  document.body.dataset.page = cfg.key;
  const S = {
    cfg, map: null, options: null, bands: new Set(cfg.defaultBands || []),
    placing: false, placeRole: cfg.placeRoles[0].role, placeErp: cfg.placeRoles[0].defErp || "medium",
    asset: null, reach: 3000, assetH: cfg.assetH || 2.0, includeAuto: !!cfg.autoSource,
    drawn: [], fcProps: {}, probeTok: 0, probeTimer: null,
    settingsOpen: false,
  };
  S.map = RFCore.makeMap("map");
  S.heat = L.layerGroup().addTo(S.map);
  S.redsLayer = L.layerGroup().addTo(S.map);
  S.assetLayer = L.layerGroup().addTo(S.map);
  S.insp = document.getElementById("expo-inspector");

  buildSidebar(S);
  loadOptions(S);
  refreshReds(S);
  S.map.on("click", (e) => onMapClick(S, e));
  S.map.on("mousemove", (e) => onHover(S, e));
  S.map.on("mouseout", () => { S.insp.hidden = true; clearTimeout(S.probeTimer); });
}

function navHtml(active) {
  const links = PAGES.map((p) =>
    `<a href="${p.href}" class="${p.key === active ? "on " : ""}${p.expertOnly ? "expert-only" : ""}">${p.label}</a>`).join("");
  return `<nav class="page-nav"><a href="index.html" class="home">⌂</a>${links}</nav>`;
}

function bandChipsHtml(S) {
  const bands = (S.options && S.options.bands) || [];
  if (!bands.length) return '<div class="muted small">loading bands…</div>';
  return bands.map((b) => {
    const on = S.bands.has(b.key) ? " checked" : "";
    const lbl = b.center_hz >= 1e9 ? (b.center_hz / 1e9).toFixed(2) + "G" : Math.round(b.center_hz / 1e6) + "M";
    const dsm = b.needs_dsm ? ' <span class="ex-dsm" title="needs LiDAR DSM; coarse on 30 m DEM">DSM</span>' : "";
    return `<label class="ex-band"><input type="checkbox" data-band="${b.key}" data-hz="${b.center_hz}"${on}> ${lbl}${dsm}</label>`;
  }).join("");
}

function buildSidebar(S) {
  const cfg = S.cfg;
  const roleSel = cfg.placeRoles.length > 1
    ? `<select id="ex-role">${cfg.placeRoles.map((r) => `<option value="${r.role}">${r.label}</option>`).join("")}</select>`
    : `<input type="hidden" id="ex-role" value="${cfg.placeRoles[0].role}">`;
  const erps = (cfg.placeRoles[0].erps || ["low", "medium", "high", "very_high"]);
  const presetBtns = cfg.presets
    ? `<div class="row ex-presets"><span class="muted small">presets:</span>${Object.keys(cfg.presets).map((k) => `<button class="ex-pre" type="button" data-preset="${k}">${k}</button>`).join("")}<button class="ex-pre" data-preset="clear" type="button">clear</button></div>`
    : "";
  const autoRow = cfg.autoSource
    ? `<label class="toggle"><input id="ex-auto" type="checkbox"${S.includeAuto ? " checked" : ""}> use mesh ${cfg.placeRoles[0].role}s</label>` : "";

  document.getElementById("sidebar").innerHTML = `
    <header id="brand">
      <span class="dot"></span>
      <div><h1>both3</h1><p class="sub">${cfg.title}</p></div>
    </header>
    ${navHtml(cfg.key)}
    <section class="panel">
      ${legendHtml(cfg)}
      <p class="muted small">${cfg.disclaimer || "Relative terrain cover (diffraction). Colour shows team/exposure; never a guarantee — power isn't modelled, shadows never zero."}</p>
    </section>
    <div class="settings-toggle-wrap">
      <button id="settings-toggle" class="ghost" type="button" aria-controls="settings-drawer" aria-expanded="${S.settingsOpen}">&#9881; Settings</button>
    </div>
    <div id="settings-drawer"${S.settingsOpen ? "" : " hidden"}>
    <section class="panel step"><div class="step-n">1</div><div class="step-b">
      <h2>Place red team</h2>
      <div class="row ex-place">${roleSel}
        <select id="ex-erp">${erps.map((e) => `<option value="${e}"${e === S.placeErp ? " selected" : ""}>${e}</option>`).join("")}</select>
        <button id="ex-place" type="button">＋ place</button>
        <button id="ex-clear-red" type="button" title="clear placed">🗑</button>
      </div>${autoRow}
      <div class="muted small" id="ex-redcount">no reds placed</div>
    </div></section>
    <section class="panel step"><div class="step-n">2</div><div class="step-b">
      <h2>${cfg.bandLabel}</h2>
      ${presetBtns}
      <div class="row ex-bands">${bandChipsHtml(S)}</div>
    </div></section>
    <section class="panel step"><div class="step-n">3</div><div class="step-b">
      <h2>${cfg.assetLabel || "Asset / area"}</h2>
      <div class="row">
        <button id="ex-asset" type="button">set point</button>
        <label>reach m<input id="ex-reach" type="number" min="200" max="6000" step="100" value="${S.reach}"></label>
        <label>asset h<input id="ex-asseth" type="number" min="0" step="0.5" value="${S.assetH}"></label>
      </div>
      <div class="muted small" id="ex-assetnote">using map centre</div>
    </div></section>
    <section class="panel step"><div class="step-n">4</div><div class="step-b">
      <h2>Run</h2>
      <button id="ex-run" type="button" class="ex-run">Run ${cfg.title.toLowerCase()}</button>
      <div id="ex-status" class="ex-status"></div>
    </div></section>
    </div>`;
  wireSidebar(S);
}

function legendHtml(cfg) {
  const L = cfg.legend || {};
  return `<div class="lens-legend">
    <div class="ll-row"><span class="ll-sw ll-safe"></span>${L.safe || "lower exposure (friendly)"}</div>
    <div class="ll-row"><span class="ll-sw ll-danger"></span>${L.danger || "exposed (red team reaches you)"}</div>
    <div class="ll-row"><span class="ll-sw ll-red"></span>red-team node</div>
    <div class="ll-row"><span class="ll-sw ll-asset"></span>your asset</div>
  </div>`;
}

function wireSidebar(S) {
  const q = (s) => document.querySelector(s);
  q("#settings-toggle").onclick = (e) => {
    S.settingsOpen = !S.settingsOpen;
    q("#settings-drawer").hidden = !S.settingsOpen;
    e.currentTarget.setAttribute("aria-expanded", String(S.settingsOpen));
  };
  q("#ex-erp").onchange = (e) => { S.placeErp = e.target.value; };
  const role = q("#ex-role"); if (role.tagName === "SELECT") role.onchange = (e) => { S.placeRole = e.target.value; };
  q("#ex-place").onclick = () => { S.placing = S.placing === "red" ? false : "red"; reflectArm(S); };
  q("#ex-asset").onclick = () => { S.placing = S.placing === "asset" ? false : "asset"; reflectArm(S); };
  q("#ex-clear-red").onclick = () => clearReds(S);
  q("#ex-reach").onchange = (e) => { S.reach = parseFloat(e.target.value) || 3000; };
  q("#ex-asseth").onchange = (e) => { S.assetH = parseFloat(e.target.value) || 0; };
  q("#ex-run").onclick = () => run(S);
  const auto = q("#ex-auto"); if (auto) auto.onchange = (e) => { S.includeAuto = e.target.checked; refreshReds(S); };
  document.querySelectorAll(".ex-band input").forEach((i) => {
    i.onchange = () => { i.checked ? S.bands.add(i.dataset.band) : S.bands.delete(i.dataset.band); };
  });
  document.querySelectorAll(".ex-pre").forEach((b) => {
    b.onclick = () => {
      S.bands = new Set(b.dataset.preset === "clear" ? [] : (S.cfg.presets[b.dataset.preset] || []));
      buildSidebar(S);
    };
  });
  reflectArm(S);
}

function reflectArm(S) {
  const p = document.querySelector("#ex-place"), a = document.querySelector("#ex-asset");
  if (p) { p.classList.toggle("armed", S.placing === "red"); p.textContent = S.placing === "red" ? "click map…" : "＋ place"; }
  if (a) { a.classList.toggle("armed", S.placing === "asset"); a.textContent = S.placing === "asset" ? "click map…" : "set point"; }
}

function selectedBandHz(S) {
  const out = [];
  document.querySelectorAll(".ex-band input:checked").forEach((i) => out.push(parseFloat(i.dataset.hz)));
  return out;
}

// ---- red markers ----
async function loadOptions(S) {
  try { const r = await fetch("exposure/options"); if (r.ok) S.options = await r.json(); } catch (e) { /* */ }
  buildSidebar(S);
}
async function refreshReds(S) {
  try {
    const r = await fetch("exposure/reds"); if (!r.ok) return;
    const d = await r.json();
    drawReds(S, d.manual || [], S.includeAuto ? (d.auto || []) : []);
    const el = document.querySelector("#ex-redcount");
    if (el) el.textContent = `${(d.manual || []).length} placed${S.includeAuto && d.auto.length ? ` + ${d.auto.length} mesh` : ""}`;
  } catch (e) { /* */ }
}
function drawReds(S, manual, auto) {
  S.redsLayer.clearLayers();
  for (const m of manual)
    L.marker([m.lat, m.lon], { icon: RFCore.redIcon(m.role, m.erp_class) })
      .bindTooltip(`${m.role} · ${m.erp_class} (click to remove)`, { sticky: true })
      .on("click", () => deleteRed(S, m.id)).addTo(S.redsLayer);
  for (const a of auto)
    L.marker([a.lat, a.lon], { icon: RFCore.autoIcon(a.role, a.erp_class) })
      .bindTooltip(`mesh ${a.role} · ${a.erp_class}`, { sticky: true }).addTo(S.redsLayer);
}
async function placeRed(S, latlng) {
  try {
    await fetch("exposure/reds", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lat: latlng.lat, lon: latlng.lng, role: S.placeRole, erp_class: S.placeErp, h_m: 3.0 }),
    });
    RFCore.toast(`placed ${S.placeRole} (${S.placeErp})`, "ok"); refreshReds(S);
  } catch (e) { RFCore.toast("place failed", "err"); }
}
async function deleteRed(S, id) { try { await fetch("exposure/reds/" + id, { method: "DELETE" }); refreshReds(S); } catch (e) { /* */ } }
async function clearReds(S) { try { await fetch("exposure/reds", { method: "DELETE" }); S.redsLayer.clearLayers(); refreshReds(S); } catch (e) { /* */ } }

function setAsset(S, latlng) {
  S.asset = [latlng.lat, latlng.lng];
  S.assetLayer.clearLayers();
  L.marker(S.asset, { icon: RFCore.assetIcon() }).bindTooltip("asset / AOI centre", { sticky: true }).addTo(S.assetLayer);
  const n = document.querySelector("#ex-assetnote"); if (n) n.textContent = `asset @ ${S.asset[0].toFixed(4)}, ${S.asset[1].toFixed(4)}`;
}

function onMapClick(S, e) {
  if (S.placing === "asset") { setAsset(S, e.latlng); S.placing = false; reflectArm(S); }
  else if (S.placing === "red") { placeRed(S, e.latlng); S.placing = false; reflectArm(S); }
}

// ---- run + render ----
function setStatus(S, msg, kind) {
  const el = document.querySelector("#ex-status"); if (!el) return;
  if (msg) { el.textContent = msg; el.className = "ex-status " + (kind || ""); return; }
  const p = S.fcProps || {};
  const n = (S.fc && S.fc.features || []).length;
  let s = `${n} band${n === 1 ? "" : "s"} drawn`;
  if (p.burnthrough) s += " · ⚠ burnthrough: shadow won't hold (high-ERP jammer)";
  if (p.fidelity_warning) s += " · ⚠ coarse DEM for high band";
  el.textContent = s; el.className = "ex-status" + (p.burnthrough ? " warn" : "");
}

async function run(S) {
  const cfg = S.cfg;
  const bands = selectedBandHz(S);
  if (!bands.length) { RFCore.toast("select at least one band", "err"); return; }
  const c = S.asset || [S.map.getCenter().lat, S.map.getCenter().lng];
  const body = {
    center_lat: c[0], center_lon: c[1], reach_m: S.reach, asset_h_m: S.assetH,
    bands_hz: bands, include_auto: S.includeAuto, include_stored: true,
    polarity: cfg.polarity || "both", reds: [],
  };
  if (cfg.lens !== "combined") body.mode = cfg.lens;
  setStatus(S, "computing…");
  try {
    const r = await fetch(cfg.endpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(d.detail || ("HTTP " + r.status)); }
    S.fc = await r.json(); S.fcProps = S.fc.properties || {};
    S.drawn = RFCore.renderBands(S.heat, S.fc, (kind) => kind.endsWith("_danger") ? cfg.colors.danger : cfg.colors.safe);
    setStatus(S);
    if (!(S.fc.features || []).length) RFCore.toast(S.fcProps.note || "no eligible reds / empty", "");
  } catch (e) { setStatus(S, "failed: " + e.message, "warn"); RFCore.toast("failed: " + e.message, "err"); }
}

// ---- hover probe ----
function onHover(S, e) {
  if (!S.drawn.length) { S.insp.hidden = true; return; }
  const lon = e.latlng.lng, lat = e.latlng.lat;
  let hit = null;
  for (const pb of [0.5, 0.8, 0.95]) {
    const safe = S.drawn.find((x) => x.p_band === pb && !x.kind.endsWith("_danger") && RFCore.pointInGeometry(lon, lat, x.geometry));
    const dang = S.drawn.find((x) => x.p_band === pb && x.kind.endsWith("_danger") && RFCore.pointInGeometry(lon, lat, x.geometry));
    if (safe) { hit = { band: safe, danger: false }; break; }
    if (dang) { hit = { band: dang, danger: true }; break; }
  }
  if (!hit) { S.insp.hidden = true; clearTimeout(S.probeTimer); return; }
  const bp = hit.band.props;
  const prot = bp.loss_db != null ? `${bp.loss_db.toFixed(1)} dB` : "—";
  S.insp.hidden = false;
  S.insp.innerHTML = `
    <div class="ei-h ${hit.danger ? "ei-danger" : "ei-safe"}">${hit.danger ? "EXPOSED" : "lower exposure"} · ${Math.round(hit.band.p_band * 100)}% mass</div>
    <div class="ei-row"><span>terrain attenuation</span><b>${prot} (${bp.loss_label || "?"})</b></div>
    <div class="ei-slot">probing…</div>
    <div class="ei-foot">relative diffraction · not "safe" · power not modelled</div>`;
  if (S.cfg.lens === "combined") { S.insp.querySelector(".ei-slot").textContent = "concealed ∩ jam-shadow"; return; }
  probe(S, e.latlng);
}
function probe(S, latlng) {
  const tok = ++S.probeTok;
  clearTimeout(S.probeTimer);
  S.probeTimer = setTimeout(async () => {
    try {
      const body = { mode: S.cfg.lens, bands_hz: selectedBandHz(S), lat: latlng.lat, lon: latlng.lng, asset_h_m: S.assetH, include_auto: S.includeAuto, include_stored: true, reds: [] };
      const r = await fetch("exposure/probe", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (!r.ok) return;
      const p = await r.json();
      if (tok !== S.probeTok || S.insp.hidden) return;
      const slot = S.insp.querySelector(".ei-slot"); if (!slot) return;
      const rows = (p.nodes || []).map((n) => `<div class="ei-node">${n.node_id || n.erp_class}: ${n.distance_m}m · ${n.loss_db}dB → T ${n.weight}</div>`).join("");
      slot.innerHTML = `<div class="ei-surv">survival ${p.survival != null ? p.survival : "—"} · worst ${p.band_hz ? (p.band_hz / 1e9).toFixed(2) + "G" : "—"}</div>${rows}`;
    } catch (e) { /* */ }
  }, 150);
}
