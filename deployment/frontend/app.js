"use strict";

// ---- config ----
const POLL_MS = 2500;
const COLORS = { high: "#2ecc71", medium: "#f1c40f", low: "#e74c3c" };
const STALE_COLOR = "#7a8493";
const OUTLIER_SIGMA = 3.0;
const DEFAULT_VIEW = [50.356, 5.0];
const TILE_URL = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";

// ---- state ----
const state = {
  fixes: new Map(),     // fix_id -> properties (from ellipse feature)
  ellipses: new Map(),  // fix_id -> ring latlngs
  centers: new Map(),   // fix_id -> [lat, lon]
  bearings: [],         // node/lob features
  bearingByNode: new Map(), // node_id -> latest node feature props
  selected: null,
  armed: false,   // send-confirm shown inline for the selected fix
  posterior: null, // { fixId, fc } RF-plausibility for the selected fix
  investigation: null, // { fixId, data } ranked candidates for the selected fix
  firstFit: false,
  emitterH: null,    // current emitter height the UI is tracking (from investigation), else null
  enhanceOptions: null, // cached { lidar_available, lidar_detail, options:[{res_m,eta_s}] }
  enhance: null,     // active enhance job: { fixId, jobId, state, phase, progress, degraded, detail, res, abort, timer }
  enhanced: null,    // { fixId, fc, props } the applied enhanced/degraded posterior override, when present
  panelOpen: false,  // enhance density panel visibility
};

// ---- map ----
const map = L.map("map", { zoomControl: true }).setView(DEFAULT_VIEW, 13);
// Basemaps. Default to OSM (always loads). Esri World Topo (relief + contours) and
// an Esri hillshade OVERLAY make terrain — and the RF-shadow's cause — visible, both
// key-free and not rate-limited like OpenTopoMap (which left tiles pending → blank map).
const baseStreet = L.tileLayer(TILE_URL, { maxZoom: 19, attribution: "© OpenStreetMap contributors" });
const baseTerrain = L.tileLayer(
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
  { maxZoom: 19, attribution: "Tiles © Esri — World Topo Map" });
const hillshade = L.tileLayer(
  "https://server.arcgisonline.com/ArcGIS/rest/services/Elevation/World_Hillshade/MapServer/tile/{z}/{y}/{x}",
  { maxZoom: 19, opacity: 0.35, attribution: "Hillshade © Esri" });
baseStreet.addTo(map);
hillshade.addTo(map); // relief shading over the street map by default
L.control.layers(
  { "Street (OSM)": baseStreet, "Terrain (Esri)": baseTerrain },
  { "Hillshade relief": hillshade },
  { position: "topleft", collapsed: true },
).addTo(map);

// Live RF-status badge over the map (computing / terrain-effect strength / no-data).
const rfStatusEl = document.createElement("div");
rfStatusEl.id = "rf-status";
rfStatusEl.hidden = true;
document.getElementById("map").appendChild(rfStatusEl);

// Hover inspector — pinned bottom-LEFT (opposite the legend at bottom-right).
const inspectorEl = document.createElement("div");
inspectorEl.id = "rf-inspector";
inspectorEl.hidden = true;
document.getElementById("map").appendChild(inspectorEl);

// Persistent "ⓘ how to read these numbers" chip + a plain-language explainer card,
// so the operator/client can decode the hover inspector's terms. The hover panel
// itself is a pass-through overlay (can't host a clickable icon), so this lives
// next to it and stays put.
const rfHelpChip = document.createElement("button");
rfHelpChip.id = "rf-help-chip";
rfHelpChip.type = "button";
rfHelpChip.hidden = true;
rfHelpChip.innerHTML = "ⓘ how to read these numbers";
document.getElementById("map").appendChild(rfHelpChip);

const rfHelpCard = document.createElement("div");
rfHelpCard.id = "rf-help-card";
rfHelpCard.hidden = true;
rfHelpCard.innerHTML = `
  <div class="hc-head"><span>Reading the RF-plausibility numbers</span>
    <button class="hc-x" type="button" aria-label="close">×</button></div>
  <div class="hc-body">
    <p><b>Plausibility</b> = how strongly <i>this spot</i> fits <b>both</b> the sensor
    bearings <b>and</b> the terrain, compared to the most-likely spot on the map
    (which scores <b>1.0</b>). A relative cue — not a probability of a hit.</p>
    <p>It multiplies two things, for every contributing sensor:</p>
    <p class="hc-h">1 · AoA — bearing geometry</p>
    <ul>
      <li><b>meas</b> — the direction the sensor actually measured to the emitter (°).</li>
      <li><b>pred</b> — the direction <i>from that sensor to this spot</i> (°).</li>
      <li><b>Δ</b> (residual) — how far <b>pred</b> is from <b>meas</b>.</li>
      <li><b>σ</b> — that sensor's own stated 1-σ bearing uncertainty.</li>
      <li><b>L</b> (likelihood) — how well this spot agrees with the sensor: 1.0 = bang
        on, falling off as Δ grows past σ.</li>
    </ul>
    <p class="hc-h">2 · RF — terrain path (knife-edge diffraction, ITU-R P.526)</p>
    <ul>
      <li><b>d</b> — distance from this spot to the sensor.</li>
      <li><b>clr</b> (clearance) — how far terrain rises <b>above (+)</b> or below (−) the
        straight line-of-sight. A “+” means a hill/ridge is in the way.</li>
      <li><b>v</b> — Fresnel diffraction parameter (larger = more blocked).</li>
      <li><b>loss</b> — modelled signal attenuation from that obstruction, in dB.
        <i>A model number, not measured power.</i></li>
      <li><b>w</b> — soft weight: 1.0 = clear path, dropping toward a floor when blocked —
        <b>never 0</b>, because radio waves bend around edges.</li>
    </ul>
    <p class="hc-h">Putting it together</p>
    <ul>
      <li><b>Π AoA × RF</b> — multiply every sensor's L and w together = the raw score.</li>
      <li><b>plausibility</b> = raw ÷ the peak score on the map.</li>
    </ul>
    <p class="hc-foot">Terrain never fully blocks a signal, so shadowed spots are
    down-weighted, not erased. It's a <b>cue, not a target</b> — confirm with a second
    sensor / PID before acting. Power is not measured (no dBm).</p>
  </div>`;
document.getElementById("map").appendChild(rfHelpCard);
rfHelpChip.onclick = () => { rfHelpCard.hidden = !rfHelpCard.hidden; };
rfHelpCard.querySelector(".hc-x").onclick = () => { rfHelpCard.hidden = true; };
document.addEventListener("keydown", (e) => { if (e.key === "Escape") rfHelpCard.hidden = true; });
document.addEventListener("click", (e) => {
  if (!rfHelpCard.hidden && !rfHelpCard.contains(e.target) && e.target !== rfHelpChip) {
    rfHelpCard.hidden = true;
  }
});

const posteriorLayer = L.layerGroup().addTo(map);  // under the ellipse (added first)
const ellipseLayer = L.layerGroup().addTo(map);
const centerLayer = L.layerGroup().addTo(map);
const bearingLayer = L.layerGroup().addTo(map);

// RF-plausibility heat: the emitter is RED TEAM, so likelihood reads RED
// (inner/higher mass = more opaque). Friendly sensors are drawn green elsewhere.
const POSTERIOR_STYLE = {
  0.5: { fillOpacity: 0.45, color: "#ff4d4f", weight: 1 },
  0.8: { fillOpacity: 0.24, color: "#ff4d4f", weight: 0.8 },
  0.95: { fillOpacity: 0.10, color: "#ff4d4f", weight: 0.6 },
};

// Map legend: an accordion of expandable "how to read it" rows. Each symbol
// row expands a plain-language block (Means / Read / Do / But) so a no-context
// operator can decode it under stress. Native <details> = zero-JS, accessible,
// keyboard-friendly, localStorage-persistable.
const LEGEND_ROWS = [
  {
    k: "ellipse",
    swatch: '<span class="lg-line" style="border-top:2px dashed #f1c40f"></span>',
    label: "95% estimated emitter area",
    means: "Where the emitter likely is, from crossed sensor bearings alone.",
    read: "Smaller = more certain; long & thin = sensors nearly in a line (weak geometry — check GDOP).",
    act: "Treat as the search area.",
    but: "95% — it can still be outside. Not a target box.",
  },
  {
    k: "rf",
    swatch: '<span class="lg-box lg-box-grad"></span>',
    label: "RF-plausible (LIKELY-HERE)",
    means: "The bearing area refined by terrain (a soft prior).",
    read: "Brightest inner band = most likely ground; outer bands = still possible.",
    act: "Start your search in the bright core.",
    but: "A cue, NOT a hit — never act on the glow alone (radio bends around hills). Confirm via PID + 2nd sensor.",
  },
  {
    k: "node",
    swatch: '<span class="lg-dot lg-dot-friendly"></span><span class="lg-line" style="border-top:2px dashed #2ecc71"></span>',
    label: "sensor node · bearing (friendly)",
    means: "A sensor (dot) and the direction it heard the signal (dashed line).",
    read: "Lines cross at the fix. A red line = outlier — suspect that node.",
    act: "2 lines = a guess, 3+ = a fix.",
    but: "These are YOUR sensors, not the threat.",
  },
  {
    k: "ridge",
    swatch: '<span class="lg-ob">▲</span>',
    label: "blocking ridge (+m)",
    means: "The terrain that shadows part of the area (shown on the topo basemap).",
    read: "The “+N m” is how far the ridge rises ABOVE the straight sight-line to a sensor.",
    act: "Expect the ground BEYOND it (away from the sensors) to be less likely.",
    but: "Radio still diffracts over — beyond isn't impossible, just down-weighted.",
  },
];

function legendRowHtml(r) {
  const open = localStorage.getItem("legend.row." + r.k) === "1" ? " open" : "";
  return `<details class="lg-row" data-k="${r.k}"${open}>
    <summary><span class="lg-swatch">${r.swatch}</span><span class="lg-label">${r.label}</span></summary>
    <div class="lg-detail">
      <div><b>Means</b> ${r.means}</div>
      <div><b>Read</b> ${r.read}</div>
      <div><b>Do</b> ${r.act}</div>
      <div class="lg-but"><b>But</b> ${r.but}</div>
    </div></details>`;
}

const legend = L.control({ position: "bottomright" });
legend.onAdd = () => {
  const d = L.DomUtil.create("div", "map-legend");
  const shellOpen = localStorage.getItem("legend.open") !== "0" ? " open" : "";
  const seen = localStorage.getItem("legend.seen") === "1";
  d.innerHTML = `<details class="lg-shell"${shellOpen}>
    <summary class="lg-chip">ⓘ Legend — how to read this${seen ? "" : '<span class="lg-new">new</span>'}</summary>
    <div class="lg-body">
      <div class="lg-title">Selected emitter</div>
      ${LEGEND_ROWS.map(legendRowHtml).join("")}
      <div class="lg-note">soft cue · not a target point</div>
    </div></details>`;
  // Don't let clicks/scroll inside the legend pan or zoom the map.
  L.DomEvent.disableClickPropagation(d);
  L.DomEvent.disableScrollPropagation(d);
  // Persist open/closed state.
  d.querySelector(".lg-shell").addEventListener("toggle", (e) => {
    localStorage.setItem("legend.open", e.target.open ? "1" : "0");
    localStorage.setItem("legend.seen", "1");
    const n = d.querySelector(".lg-new"); if (n) n.remove();
  });
  for (const row of d.querySelectorAll(".lg-row")) {
    row.addEventListener("toggle", (e) =>
      localStorage.setItem("legend.row." + e.target.dataset.k, e.target.open ? "1" : "0"));
  }
  return d;
};
legend.addTo(map);

function centerCallout(props) {
  const inv = state.investigation && state.investigation.fixId === props.fix_id ? state.investigation.data : null;
  if (inv && inv.candidates) {
    const top = inv.candidates.find((c) => !c.is_unknown);
    if (top) {
      const act = (top.recommended_action || "").toUpperCase().replace(/_/g, " ");
      return `${top.name} · ${Math.round(top.confidence * 100)}%${act ? " · " + act : ""}`;
    }
  }
  return `${props.confidence_level.toUpperCase()} · ${props.contributing_nodes.length} nodes`;
}

// ---- shared info popover (ⓘ glyphs explain off-map sidebar symbols) ----
const GLOSSARY = {
  gdop: "Geometry quality. Sensors bunched on one side → high GDOP → stretched, less trustworthy fix. Spread out → low → tight.",
  confidence: "How sure the system is THIS gear is the emitter. Low → confirm before acting. Includes a reserved UNKNOWN.",
  targets: "What the emitter attacks: GPS/GNSS, Starlink/SATCOM, GSM, wifi, FPV 2.4 & 5.8 GHz drone links.",
  mobility: "How it's deployed: mobile (vehicle / man-portable) vs fixed; mast-high vs ground-level.",
  action: "AVOID / RE-ROUTE = move. CUE ISR = point eyes at it. CUE FIRES = hand to shooters (needs confirm). EW COUNTER = jam/spoof back.",
  engage: "You may NOT engage on this alone — needs PID + a 2nd sensor. This tool produces look/cue, not weapons release.",
  nodbm: "Power is NOT measured (uncalibrated SDR). Don't infer range or strength — direction & geometry only.",
  timecrit: "Fleeting — mobile or about to move. Decide now or lose it.",
};
function iTag(k) {
  const t = (GLOSSARY[k] || "").replace(/"/g, "&quot;");
  return `<span class="info-i" data-tip="${t}" tabindex="0" role="button" aria-label="explain">ⓘ</span>`;
}
const _tip = document.createElement("div");
_tip.className = "tip-pop";
_tip.hidden = true;
document.body.appendChild(_tip);
let _tipSticky = false;
function _showTip(el) {
  const t = el.getAttribute("data-tip");
  if (!t) return;
  _tip.textContent = t;
  _tip.hidden = false;
  const r = el.getBoundingClientRect();
  _tip.style.left = Math.max(8, Math.min(window.innerWidth - 268, r.left)) + "px";
  _tip.style.top = (r.bottom + 6) + "px";
}
function _hideTip() { _tip.hidden = true; _tipSticky = false; }
document.addEventListener("mouseover", (e) => {
  const el = e.target.closest(".info-i");
  if (el && !_tipSticky) _showTip(el);
});
document.addEventListener("mouseout", (e) => {
  if (e.target.closest(".info-i") && !_tipSticky) _hideTip();
});
document.addEventListener("click", (e) => {
  const el = e.target.closest(".info-i");
  if (el) { e.stopPropagation(); _tipSticky = true; _showTip(el); }
  else if (_tipSticky) _hideTip();
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") _hideTip(); });

// ---- helpers ----
const $ = (sel) => document.querySelector(sel);
const ringToLatLngs = (lonlatRing) => lonlatRing.map(([lon, lat]) => [lat, lon]);

function fmtAge(props) {
  if (props.age_s == null) return props.seeded ? "demo" : "—";
  const s = props.age_s;
  if (s < 0) return "future";
  if (s < 90) return `${Math.round(s)}s`;
  if (s < 5400) return `${Math.round(s / 60)}m`;
  return `${Math.round(s / 3600)}h`;
}

// The emitter is RED TEAM — always red (stale = grey). Confidence is conveyed by
// ellipse size + the HIGH/MED/LOW label, not hue (user: red=threat, green=friendly).
const THREAT = "#ff4d4f";
function confColor(props) {
  if (props.stale) return STALE_COLOR;
  return THREAT;
}
function confidenceColor(props) {
  if (props.stale) return STALE_COLOR;
  const level = String(props.confidence_level).toLowerCase();
  return COLORS[level === "med" ? "medium" : level] || COLORS.low;
}
// Confidence → fill opacity (stronger = more certain) so the red emitter still
// shows certainty without changing hue.
function confOpacity(props) {
  return { high: 0.32, medium: 0.20, low: 0.12 }[props.confidence_level] || 0.15;
}

function activeFilters() {
  const conf = new Set(
    [...document.querySelectorAll("#conf-filter input:checked")].map((i) => i.value)
  );
  return {
    conf,
    klass: $("#class-filter").value,
    minNodes: parseInt($("#min-nodes").value || "2", 10),
    showStale: $("#show-stale").checked,
    showBearings: $("#show-bearings").checked,
    showPosterior: $("#show-posterior").checked,
  };
}

async function fetchPosterior(id, emitterH) {
  if (!$("#show-posterior").checked) { state.posterior = null; renderPosterior(); updateRfStatus(); return; }
  state.posterior = { fixId: id, loading: true }; // show "computing…" immediately
  renderPosterior();
  updateRfStatus();
  const q = emitterH != null ? `?emitter_h=${emitterH}` : "";
  try {
    const res = await fetch(`fixes/${id}/posterior${q}`);
    if (!res.ok) throw new Error("HTTP " + res.status);
    const fc = await res.json();
    state.posterior = { fixId: id, fc, props: fc.properties || {} };
  } catch (e) {
    state.posterior = { fixId: id, error: true };
  }
  renderPosterior(); // draw the heat once, in its own layer
  render();          // update ellipse outline + status
}

// ---- terrain-source label, honestly derived from the FC properties ----
// Maps a dem_source string to a short human label + a "coarse/sharp" qualifier.
// Never hardcodes the resolution — reads dem_res_m from the FC.
function terrainLabel(props) {
  const src = (props && props.dem_source) || "";
  const res = props && props.dem_res_m != null ? Math.round(props.dem_res_m) : null;
  const lid = props && props.scan_density_m != null ? Math.round(props.scan_density_m) : null;
  if (props && props.enhanced) {
    // LiDAR-enhanced (sharp). Use the scan density actually computed + the surface.
    const scan = lid != null ? lid : res;
    const surf = props.surface === "dtm" ? "MNT · bare earth" : "MNS · buildings";
    return { text: `Wallonia LiDAR ${surf} · ${scan} m`, kind: "sharp", reason: src };
  }
  if (src.startsWith("copernicus")) {
    // Default coarse, or a degraded enhance fallback (carries "(lidar unavailable: …)").
    const degraded = src.includes("unavailable");
    const reason = degraded ? src.replace(/^.*unavailable:\s*/, "").replace(/\)\s*$/, "") : "";
    const base = res != null ? `Copernicus GLO-30 · ${res} m` : "Copernicus GLO-30";
    return {
      text: degraded ? `Copernicus ${res != null ? res + " m" : "30 m"} (LiDAR unavailable)` : `${base} — coarse`,
      kind: degraded ? "degraded" : "coarse",
      reason,
    };
  }
  if (src) return { text: src + (res != null ? ` · ${res} m` : ""), kind: "coarse", reason: "" };
  return { text: "terrain source unknown", kind: "coarse", reason: "" };
}

// Live status badge over the map: a primary line (terrain-effect strength) plus
// a secondary line (terrain source + Enhance affordance / progress / revert).
function updateRfStatus() {
  const el = document.getElementById("rf-status");
  if (!el) return;
  const sp = state.posterior;
  const on = $("#show-posterior") && $("#show-posterior").checked;
  if (!on || !state.selected || !sp || sp.fixId !== state.selected) {
    el.hidden = true; rfHelpChip.hidden = true; rfHelpCard.hidden = true; return;
  }
  el.hidden = false;
  el.className = "";
  rfHelpChip.hidden = true;  // shown only once a real posterior is present (below)

  if (sp.loading) { el.innerHTML = '<span class="rf-bar"></span> Computing RF-plausibility…'; el.classList.add("rf-loading"); return; }
  if (sp.error) { el.textContent = "RF-plausibility unavailable"; el.classList.add("rf-warn"); return; }
  const p = sp.props || {};
  if ((p.rf_model || "").startsWith("none")) { el.textContent = "RF-plausibility: no terrain data"; el.classList.add("rf-warn"); return; }
  rfHelpChip.hidden = false;  // real terrain-aware posterior -> the help chip applies

  // Which FC properties are live right now (enhanced override wins for the label).
  const liveProps = (state.enhanced && state.enhanced.fixId === state.selected) ? state.enhanced.props : p;
  const lab = (p.rf_effect_label || "?").toUpperCase();
  const ghz = p.band_hz ? (p.band_hz / 1e9).toFixed(2) + " GHz" : "";
  el.classList.add("rf-eff-" + (p.rf_effect_label || "na"));

  // Primary line.
  const line1 = `<div class="rf-line1">RF terrain effect: ${lab}${ghz ? " · " + ghz : ""}</div>`;

  // Secondary line: state machine — running > applied(enhanced/degraded) > default.
  let line2 = "";
  const enh = state.enhance;
  if (enh && enh.fixId === state.selected && ["queued", "running", "cancelling"].includes(enh.state)) {
    line2 = enhanceProgressHtml(enh);
  } else {
    const tl = terrainLabel(liveProps);
    const cached = state.enhanced && state.enhanced.fixId === state.selected && state.enhanced.cached;
    if (state.enhanced && state.enhanced.fixId === state.selected) {
      // Applied enhanced or degraded result: source label + revert.
      const reasonAttr = tl.reason ? ` data-tip="terrain source: ${(tl.reason).replace(/"/g, "&quot;")}"` : "";
      const reasonI = tl.reason ? ` <span class="info-i rf-reason" tabindex="0" role="button" aria-label="terrain source"${reasonAttr}>ⓘ</span>` : "";
      line2 = `<div class="rf-line2 rf-src-${tl.kind}">terrain: ${tl.text}${cached ? " (cached)" : ""}${reasonI}
        <button class="rf-revert" type="button">↩ revert</button></div>`;
    } else {
      // Default Copernicus posterior: source label + Enhance button.
      line2 = `<div class="rf-line2 rf-src-${tl.kind}">terrain: ${tl.text}
        <button class="rf-enhance" type="button">⚡ Enhance</button></div>`;
      if (state.panelOpen) line2 += enhancePanelHtml();
    }
  }

  el.innerHTML = line1 + line2;
  wireRfStatus(el);
}

// ---- enhance panel (scan-density slider) ----
function enhancePanelHtml() {
  const opt = state.enhanceOptions;
  // default to 2 m; clamp into the available res set.
  const cur = state.enhanceRes || 2;
  const eta = etaFor(cur);
  const surf = currentSurface();
  const lidarNote = opt && opt.lidar_available === false
    ? `<div class="enh-note">1 m LiDAR not staged yet — Enhance will fall back to Copernicus (labelled honestly)</div>`
    : "";
  return `<div class="enh-panel">
    ${surfaceTogglesHtml(surf)}
    <div class="enh-row">
      <span class="enh-ext">1 m — sharpest · slowest</span>
      <span class="enh-ext enh-ext-r">4 m — fastest · coarser</span>
    </div>
    <input class="enh-slider" type="range" min="1" max="4" step="1" value="${cur}" />
    <div class="enh-row enh-readout">
      <span>scan density <b class="enh-val">${cur} m</b></span>
      <span class="enh-eta">~${eta}s</span>
    </div>
    ${lidarNote}
    <button class="rf-run" type="button">Run enhance</button>
  </div>`;
}

// Which terrain surface the operator picked (DSM=buildings preferred when staged).
// Falls back to an available surface if the picked one isn't staged.
function currentSurface() {
  const opt = state.enhanceOptions;
  const avail = ((opt && opt.surfaces) || []).filter((s) => s.available).map((s) => s.surface);
  if (state.enhanceSurface && avail.includes(state.enhanceSurface)) return state.enhanceSurface;
  return (opt && opt.default_surface) || avail[0] || "dsm";
}

// DTM (bare earth) vs DSM (surface, incl. buildings) chooser. Unstaged surfaces
// render disabled with a hint; the model honestly reflects what's available.
function surfaceTogglesHtml(surf) {
  const opt = state.enhanceOptions;
  const surfaces = (opt && opt.surfaces) || [];
  if (!surfaces.length) return "";
  const meta = {
    dsm: { name: "surface · buildings", tip: "MNS — ground + buildings + canopy (best for built-up areas)" },
    dtm: { name: "bare earth", tip: "MNT — ground only, no buildings/canopy" },
  };
  const btns = ["dsm", "dtm"].map((k) => {
    const s = surfaces.find((x) => x.surface === k);
    if (!s) return "";
    const on = surf === k ? " on" : "";
    const dis = s.available ? "" : " disabled";
    const note = s.available ? "" : " · not staged";
    const m = meta[k];
    return `<button class="enh-surf-btn${on}${dis ? " enh-surf-off" : ""}" data-surf="${k}"${dis} data-tip="${m.tip.replace(/"/g, "&quot;")}">${m.name}${note}</button>`;
  }).join("");
  return `<div class="enh-row enh-surf"><span class="enh-surf-lbl">terrain</span>${btns}</div>`;
}

function etaFor(res) {
  const opt = state.enhanceOptions;
  const o = opt && opt.options && opt.options.find((x) => x.res_m === res);
  return o ? o.eta_s : "?";
}

function enhanceProgressHtml(enh) {
  const phaseTxt = { queued: "queued…", fetch: "fetching…", resample: "resampling…", compute: "computing…", done: "done" }[enh.phase] || (enh.phase + "…");
  const pct = enh.progress != null ? enh.progress : 0;
  return `<div class="rf-line2 enh-progress">
    <span class="enh-phase">${phaseTxt}</span>
    <span class="enh-pbar"><span class="enh-pfill" style="width:${pct}%"></span></span>
    <span class="enh-pct">${pct}%</span>
    <button class="rf-cancel" type="button">Cancel</button>
  </div>`;
}

// Wire the buttons/slider inside the badge after each re-render. The badge itself
// has pointer-events:none for the text; interactive controls re-enable them.
function wireRfStatus(el) {
  const enhBtn = el.querySelector(".rf-enhance");
  if (enhBtn) enhBtn.onclick = (e) => { e.stopPropagation(); state.panelOpen = !state.panelOpen; updateRfStatus(); };
  const runBtn = el.querySelector(".rf-run");
  if (runBtn) runBtn.onclick = (e) => { e.stopPropagation(); runEnhance(); };
  const cancelBtn = el.querySelector(".rf-cancel");
  if (cancelBtn) cancelBtn.onclick = (e) => { e.stopPropagation(); cancelEnhance("user cancelled"); };
  const revertBtn = el.querySelector(".rf-revert");
  if (revertBtn) revertBtn.onclick = (e) => { e.stopPropagation(); revertEnhance(); };
  const slider = el.querySelector(".enh-slider");
  if (slider) {
    slider.oninput = (e) => {
      state.enhanceRes = parseInt(e.target.value, 10);
      // light-touch live update of the readout without a full re-render (keeps focus on slider)
      const v = el.querySelector(".enh-val"); if (v) v.textContent = state.enhanceRes + " m";
      const et = el.querySelector(".enh-eta"); if (et) et.textContent = "~" + etaFor(state.enhanceRes) + "s";
    };
  }
  el.querySelectorAll(".enh-surf-btn").forEach((b) => {
    if (b.disabled) return;
    b.onclick = (e) => { e.stopPropagation(); state.enhanceSurface = b.dataset.surf; updateRfStatus(); };
  });
}

// ---- enhance options (fetched once, cached) ----
async function fetchEnhanceOptions() {
  try {
    const res = await fetch("enhance/options");
    if (!res.ok) throw new Error("HTTP " + res.status);
    state.enhanceOptions = await res.json();
  } catch (e) { state.enhanceOptions = null; }
}

// ---- run / poll / cancel / revert ----
async function runEnhance() {
  const id = state.selected;
  if (!id) return;
  const res = state.enhanceRes || 2;
  const surface = currentSurface();
  state.panelOpen = false;
  state.enhance = { fixId: id, jobId: null, state: "queued", phase: "queued", progress: 0, res, surface, abort: false };
  renderPosterior();   // dim the existing heat underneath
  updateRfStatus();
  const hq = state.emitterH != null ? `&emitter_h=${state.emitterH}` : "";
  try {
    const r = await fetch(`fixes/${id}/enhance?res=${res}&surface=${surface}${hq}`, { method: "POST" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    const job = await r.json();
    // a stale fix-switch may have happened while awaiting; bail honestly.
    if (state.selected !== id || (state.enhance && state.enhance.abort)) return;
    state.enhance.jobId = job.job_id;
    state.enhance.state = job.state;
    state.enhance.phase = job.phase;
    state.enhance.progress = job.progress || 0;
    // Cache hit / immediate done: swap instantly.
    if (job.state === "done" && job.result) {
      applyEnhanceResult(job.result, !!job.cached);
      return;
    }
    if (job.state === "error") { failEnhance(job.detail || "enhance failed"); return; }
    pollEnhance();
  } catch (e) {
    failEnhance(e.message || "enhance request failed");
  }
}

function pollEnhance() {
  const enh = state.enhance;
  if (!enh || !enh.jobId) return;
  enh.timer = setTimeout(async () => {
    const cur = state.enhance;
    if (!cur || cur.jobId !== enh.jobId || cur.abort) return;
    // stop if RF-plausibility toggled off or another fix selected.
    if (!$("#show-posterior").checked || state.selected !== cur.fixId) { cancelEnhance("aborted"); return; }
    try {
      const r = await fetch(`enhance/jobs/${cur.jobId}`);
      if (!r.ok) throw new Error("HTTP " + r.status);
      const j = await r.json();
      if (!state.enhance || state.enhance.jobId !== cur.jobId) return;
      state.enhance.state = j.state;
      state.enhance.phase = j.phase;
      state.enhance.progress = j.progress != null ? j.progress : state.enhance.progress;
      state.enhance.degraded = j.degraded;
      state.enhance.detail = j.detail;
      if (j.state === "done" && j.result) { applyEnhanceResult(j.result, !!j.cached); return; }
      if (j.state === "error") { failEnhance(j.detail || "enhance failed"); return; }
      if (j.state === "cancelled") { failEnhance(j.detail || "enhance cancelled", true); return; }
      updateRfStatus();
      pollEnhance();
    } catch (e) {
      failEnhance(e.message || "enhance poll failed");
    }
  }, 500);
}

function applyEnhanceResult(fc, cached) {
  const props = fc.properties || {};
  state.enhanced = { fixId: state.selected, fc, props, cached: !!cached };
  state.enhance = null;
  renderPosterior();       // ONE render path — feeds the result FC through it
  updateRfStatus();
  if (props.enhanced) {
    toast(`Enhanced ✓ Wallonia LiDAR · ${props.scan_density_m} m${cached ? " (cached)" : ""}`, "ok");
  } else if (props.degraded) {
    const reason = (props.dem_source || "").replace(/^.*unavailable:\s*/, "").replace(/\)\s*$/, "");
    toast(`Degraded to Copernicus 30 m — LiDAR unavailable${reason ? ": " + reason : ""}`, "");
  } else {
    toast(`Terrain refreshed${cached ? " (cached)" : ""}`, "ok");
  }
}

function failEnhance(detail, isCancel) {
  const enh = state.enhance;
  if (enh && enh.timer) clearTimeout(enh.timer);
  state.enhance = null;
  renderPosterior(); // un-dim, revert to whatever heat (enhanced override or Copernicus) is current
  updateRfStatus();
  toast((isCancel ? "Enhance cancelled" : "Enhance failed") + (detail ? ": " + detail : ""), isCancel ? "" : "err");
}

// Cancel the in-flight job (best-effort POST), stop polling, revert heat.
function cancelEnhance(reason) {
  const enh = state.enhance;
  if (!enh) return;
  enh.abort = true;
  if (enh.timer) clearTimeout(enh.timer);
  const jobId = enh.jobId;
  state.enhance = null;
  renderPosterior();
  updateRfStatus();
  if (jobId) fetch(`enhance/jobs/${jobId}/cancel`, { method: "POST" }).catch(() => {});
  if (reason === "user cancelled") toast("Enhance cancelled", "");
}

// Revert the applied enhanced/degraded heat back to the Copernicus posterior.
function revertEnhance() {
  state.enhanced = null;
  renderPosterior();
  updateRfStatus();
  toast("Reverted to Copernicus 30 m", "");
}

// ---- hover inspector (point-in-polygon, no library) ----
// Ray-casting test: is [lon,lat] inside a single linear ring (array of [lon,lat])?
function pointInRing(lon, lat, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const xi = ring[i][0], yi = ring[i][1];
    const xj = ring[j][0], yj = ring[j][1];
    const intersect = ((yi > lat) !== (yj > lat)) &&
      (lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi);
    if (intersect) inside = !inside;
  }
  return inside;
}

// A Polygon = [outerRing, hole1, …]; inside iff in outer and in NO hole.
function pointInPolygon(lon, lat, rings) {
  if (!rings.length || !pointInRing(lon, lat, rings[0])) return false;
  for (let h = 1; h < rings.length; h++) {
    if (pointInRing(lon, lat, rings[h])) return false; // in a hole
  }
  return true;
}

// Handles GeoJSON Polygon and MultiPolygon geometry shapes.
function pointInGeometry(lon, lat, geom) {
  if (!geom) return false;
  if (geom.type === "Polygon") return pointInPolygon(lon, lat, geom.coordinates);
  if (geom.type === "MultiPolygon") {
    for (const poly of geom.coordinates) {
      if (pointInPolygon(lon, lat, poly)) return true;
    }
  }
  return false;
}

const BAND_LABEL = { 0.5: "50% core", 0.8: "80%", 0.95: "95% outer" };

// Short DEM-source name for the inspector (e.g. "Wallonia LiDAR", "Copernicus").
function demShort(props) {
  const src = (props && props.dem_source) || "";
  if (props && props.enhanced) return "Wallonia LiDAR";
  if (src.startsWith("copernicus")) return "Copernicus";
  return src.split(" ")[0] || "terrain";
}

// Probe query params that make the breakdown match the *displayed* layer.
function inspectorParams(fcp) {
  let q = "";
  if (fcp && fcp.enhanced && fcp.scan_density_m && fcp.surface) {
    q += `&res=${fcp.scan_density_m}&surface=${fcp.surface}`;
  }
  if (state.emitterH != null) q += `&emitter_h=${state.emitterH}`;
  return q;
}

// The "show the math" block: per-node AoA + RF terms, the products, and the
// normalized plausibility. This is the trust-builder — every number that feeds
// the plausibility is on screen.
function probeMathHtml(p) {
  if (!p) return `<div class="ins-math ins-math-wait">computing…</div>`;
  const nodeRows = (p.nodes || []).map((n) => {
    const clr = n.clearance_m != null ? `${n.clearance_m >= 0 ? "+" : ""}${n.clearance_m} m` : "—";
    const rf = n.loss_db != null
      ? `d ${n.distance_m} m · clr ${clr} vs LOS · v ${n.fresnel_v} · loss ${n.loss_db} dB → w ${n.weight}`
      : "(no terrain)";
    return `<div class="ins-node">
      <div class="ins-node-id">${n.node_id}</div>
      <div class="ins-aoa">AoA: meas ${n.azimuth_meas_deg}° · pred ${n.bearing_pred_deg}° · Δ${n.residual_deg}° · σ${n.sigma_deg}° → L ${n.aoa_likelihood}</div>
      <div class="ins-rf">RF: ${rf}</div>
    </div>`;
  }).join("");
  const rfp = p.rf_product != null ? ` × RF ${p.rf_product}` : "";
  return `<div class="ins-math">
    <div class="ins-math-h">why this value</div>
    ${nodeRows}
    <div class="ins-prod">Π AoA ${p.aoa_product}${rfp} = ${p.raw}</div>
    <div class="ins-plaus">plausibility <b>${p.plausibility}</b> of local peak</div>
  </div>`;
}

let _probeTimer = null;
let _probeToken = 0;
let _lastProbe = null;

function fetchProbe(latlng) {
  const id = state.selected;
  if (!id) return;
  const token = ++_probeToken;
  clearTimeout(_probeTimer);
  _probeTimer = setTimeout(async () => {
    try {
      const q = inspectorParams(renderedFcProps);
      const r = await fetch(`fixes/${id}/posterior/probe?lat=${latlng.lat}&lon=${latlng.lng}${q}`);
      if (!r.ok) return;
      const p = await r.json();
      if (token !== _probeToken || state.selected !== id) return; // stale move / fix switch
      _lastProbe = p;
      const slot = inspectorEl.querySelector(".ins-math-slot");
      if (slot && !inspectorEl.hidden) slot.innerHTML = probeMathHtml(p);
    } catch (e) { /* ignore transient probe errors */ }
  }, 140);
}

function updateInspector(latlng) {
  const el = inspectorEl;
  // Only when heat is actually drawn for the selected fix.
  if (!renderedBands.length || !$("#show-posterior").checked) { el.hidden = true; return; }
  const lon = latlng.lng, lat = latlng.lat;
  // Find the TIGHTEST band containing the cursor: innermost 0.5 first, then 0.8, then 0.95.
  const order = [0.5, 0.8, 0.95];
  let hit = null;
  for (const pb of order) {
    const band = renderedBands.find((b) => b.p_band === pb);
    if (band && pointInGeometry(lon, lat, band.geometry)) { hit = band; break; }
  }
  if (!hit) { el.hidden = true; clearTimeout(_probeTimer); return; }
  const bp = hit.props;
  const fcp = renderedFcProps || {};
  const bandLabel = BAND_LABEL[hit.p_band] || `${Math.round(hit.p_band * 100)}%`;
  const demRes = fcp.dem_res_m != null ? Math.round(fcp.dem_res_m) : "?";
  const terr = bp.terrain_m != null ? bp.terrain_m.toFixed(0) + " m" : "—";
  const lossLabel = (bp.loss_label || "?").replace(/^\w/, (c) => c.toUpperCase());
  const lossModel = bp.loss_db != null ? ` <span class="ins-model">(${bp.loss_db.toFixed(1)} dB, model)</span>` : "";
  const model = fcp.rf_model || "knife-edge";
  el.hidden = false;
  el.innerHTML = `
    <div class="ins-band ins-band-${String(hit.p_band).replace(".", "")}">${bandLabel}</div>
    <div class="ins-row"><span class="ins-k">Terrain</span><span class="ins-v">${demShort(fcp)} · ${demRes} m</span></div>
    <div class="ins-row"><span class="ins-k">Model</span><span class="ins-v">${model}</span></div>
    <div class="ins-row"><span class="ins-k">Terrain height</span><span class="ins-v">${terr}</span></div>
    <div class="ins-row"><span class="ins-k">Diffraction loss</span><span class="ins-v ins-loss-${bp.loss_label || "na"}">${lossLabel}${lossModel}</span></div>
    <div class="ins-math-slot">${probeMathHtml(_lastProbe)}</div>
    <div class="ins-foot">cue, not a hit · power not measured</div>`;
  fetchProbe(latlng); // debounced; fills the math slot with live per-node numbers
}

map.on("mousemove", (e) => updateInspector(e.latlng));
map.on("mouseout", () => { inspectorEl.hidden = true; clearTimeout(_probeTimer); });

const TARGET_ICON = {
  gnss_gps: "🛰GPS", glonass: "🛰GLO", starlink_leo_satcom: "📡SAT", gsm_cellular: "📶GSM",
  wifi: "📶WiFi", fpv_2g4: "🚁2.4", fpv_5g8: "🚁5.8", rc_control_link: "🎮RC",
  droneid: "🆔DID", satcom_lband: "📡L",
};
const MOBILITY_GLYPH = {
  man_portable: "🚶man-portable", vehicle: "🚚vehicle", fixed: "⚓fixed",
  mast: "🗼mast", airborne: "✈airborne",
};
const ACTION_COLOR = {
  cue_fires: "#e74c3c", ew_counter: "#e67e22", re_route: "#f1c40f",
  avoid: "#f1c40f", cue_isr: "#4aa8ff", report: "#8a94a3",
};

async function fetchInvestigation(id) {
  try {
    const res = await fetch(`fixes/${id}/investigate`);
    if (!res.ok) throw new Error("HTTP " + res.status);
    state.investigation = { fixId: id, data: await res.json() };
  } catch (e) {
    state.investigation = null;
  }
  renderInvestigation();
}

function renderInvestigation() {
  const box = $("#investigation");
  if (!box) return;
  const inv = state.investigation;
  if (!inv || inv.fixId !== state.selected || !inv.data) { box.hidden = true; return; }
  box.hidden = false;
  const d = inv.data;
  const m = d.measured || {};
  const band = m.band_name || "unknown band";
  const mhz = m.center_freq_hz ? (m.center_freq_hz / 1e6).toFixed(1) + " MHz" : "freq n/a";
  const cards = (d.candidates || []).map((c) => {
    if (c.is_unknown) {
      return `<div class="cand unknown"><div class="cand-top"><b>UNKNOWN</b>
        <span class="conf"><span style="width:${Math.round(c.confidence*100)}%"></span></span>
        <span class="pct">${Math.round(c.confidence*100)}%</span></div>
        <div class="muted small">evidence insufficient for a confident ID</div></div>`;
    }
    const h = (c.antenna_height_class_m && c.antenna_height_class_m.typ) || null;
    const targets = (c.targets || []).map((t) => `<span class="chip-t">${TARGET_ICON[t] || t}</span>`).join("");
    const ev = (c.evidence || []).slice(0, 4).map((e) => `<span class="chip-e">${e}</span>`).join("");
    const act = (c.recommended_action || "report").toUpperCase().replace(/_/g, " ");
    const acol = ACTION_COLOR[c.recommended_action] || "#8a94a3";
    return `<div class="cand" data-h="${h ?? ""}" title="click to re-weight RF heat for this antenna height">
      <div class="cand-top"><b>${c.name}</b>
        <span class="conf"><span style="width:${Math.round(c.confidence*100)}%"></span></span>
        <span class="pct">${Math.round(c.confidence*100)}%</span></div>
      <div class="cand-row">${targets}
        <span class="chip-m">${MOBILITY_GLYPH[c.mobility] || c.mobility || "?"}</span>
        ${h != null ? `<span class="chip-m">↕${h}m</span>` : ""}
        <span class="act" style="background:${acol}">${act}${c.time_critical ? " ⏱" : ""}</span></div>
      ${ev ? `<div class="cand-row">${ev}</div>` : ""}
    </div>`;
  }).join("");
  box.innerHTML = `
    <h2>Investigation</h2>
    <div class="measured">measured: <b>${band}</b> · ${mhz} · <span class="muted">no dBm</span> ${iTag("nodbm")}</div>
    <div class="inv-key">key: conf ${iTag("confidence")} · targets ${iTag("targets")} · deploy ${iTag("mobility")} · action ${iTag("action")} · engage ${iTag("engage")}</div>
    <div class="cands">${cards}</div>`;
  for (const el of box.querySelectorAll(".cand[data-h]")) {
    el.onclick = () => {
      const h = el.getAttribute("data-h");
      if (h && state.selected) {
        state.emitterH = parseFloat(h);
        fetchPosterior(state.selected, h);
      }
    };
  }
}

function passesFilter(props, f) {
  if (!f.conf.has(props.confidence_level)) return false;
  if (f.klass && props.emitter_class !== f.klass) return false;
  if ((props.contributing_nodes || []).length < f.minNodes) return false;
  if (props.stale && !f.showStale) return false;
  return true;
}

function toast(msg, kind) {
  const el = document.createElement("div");
  el.className = "toast " + (kind || "");
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), 6000);
}

// ---- data fetch ----
async function pollFixes() {
  try {
    const res = await fetch("fixes");
    if (!res.ok) throw new Error("HTTP " + res.status);
    const fc = await res.json();
    state.fixes.clear();
    state.ellipses.clear();
    state.centers.clear();
    const classes = new Set();
    for (const feat of fc.features) {
      const p = feat.properties;
      if (p.emitter_class) classes.add(p.emitter_class);
      if (p.feature_kind === "ellipse") {
        state.fixes.set(p.fix_id, p);
        state.ellipses.set(p.fix_id, ringToLatLngs(feat.geometry.coordinates[0]));
      } else if (p.feature_kind === "center") {
        const [lon, lat] = feat.geometry.coordinates;
        state.centers.set(p.fix_id, [lat, lon]);
      }
    }
    refreshClassFilter(classes);
    $("#status-line").textContent = "live · " + state.fixes.size + " fixes";
  } catch (e) {
    $("#status-line").textContent = "backend unreachable";
  }
}

async function pollBearings() {
  try {
    const res = await fetch("bearings");
    if (!res.ok) return;
    const fc = await res.json();
    state.bearings = fc.features;
    state.bearingByNode.clear();
    for (const feat of fc.features) {
      if (feat.properties.feature_kind === "node") {
        state.bearingByNode.set(feat.properties.node_id, feat.properties);
      }
    }
  } catch (e) { /* ignore */ }
}

function refreshClassFilter(classes) {
  const sel = $("#class-filter");
  const have = new Set([...sel.options].map((o) => o.value));
  for (const c of classes) {
    if (!have.has(c)) {
      const opt = document.createElement("option");
      opt.value = c; opt.textContent = c;
      sel.appendChild(opt);
    }
  }
}

// ---- residual outliers (needs bearing sigma, joined to selected fix) ----
function outlierNodes(props) {
  const out = new Set();
  const nodes = props.contributing_nodes || [];
  const res = props.residuals_deg || [];
  for (let i = 0; i < nodes.length; i++) {
    const b = state.bearingByNode.get(nodes[i]);
    if (b && Math.abs(res[i]) > OUTLIER_SIGMA * b.azimuth_sigma_deg) out.add(nodes[i]);
  }
  return out;
}

// ---- render ----
// The posterior heat is heavy (filled bands) and changes only on (de)select or
// toggle — NOT every poll. Draw it in its own layer here, called only when it
// changes, so the periodic render() never clears/rebuilds it (no flicker).
// The set of band features currently drawn on the map, kept for the hover
// inspector's point-in-polygon test (no re-fetch, no re-derive).
let renderedBands = [];   // [{ p_band, props, geometry }]
let renderedFcProps = {}; // FC-level properties (dem_source, dem_res_m, rf_model, …) for the inspector

// Render the RF-plausibility heat. Accepts an optional FC override (the enhanced
// or degraded result); defaults to the fetched Copernicus posterior. ONE render
// path — the enhanced state reuses this exactly, only the source FC differs.
function renderPosterior(fcOverride) {
  posteriorLayer.clearLayers();
  renderedBands = [];
  renderedFcProps = {};
  const on = $("#show-posterior") && $("#show-posterior").checked;
  const sp = state.posterior;
  // Choose the FC: explicit override > applied enhanced override > fetched Copernicus.
  let fc = fcOverride;
  let fcProps = null;
  if (!fc) {
    if (state.enhanced && state.enhanced.fixId === state.selected) {
      fc = state.enhanced.fc; fcProps = state.enhanced.props;
    } else if (sp && sp.fixId === state.selected) {
      fc = sp.fc; fcProps = sp.props;
    }
  } else {
    fcProps = fc.properties || {};
  }
  if (!on || !state.selected || !fc) return;
  // Dim the heat underneath while an enhance compute is in flight for this fix.
  const computing = state.enhance && state.enhance.fixId === state.selected
    && ["queued", "running", "cancelling"].includes(state.enhance.state);
  renderedFcProps = fcProps || {};
  for (const feat of fc.features) {
    const st = POSTERIOR_STYLE[feat.properties.p_band] || POSTERIOR_STYLE[0.95];
    const pct = Math.round(feat.properties.p_band * 100);
    L.geoJSON(feat, {
      style: { ...st, fillColor: st.color, className: computing ? "posterior-dim" : "" },
    })
      .bindTooltip(`RF-plausible area · ${pct}% of probability`, { sticky: true })
      .addTo(posteriorLayer);
    renderedBands.push({ p_band: feat.properties.p_band, props: feat.properties, geometry: feat.geometry });
  }
  const ob = fcProps && fcProps.obstruction;
  if (ob) {
    L.marker([ob.lat, ob.lon], {
      icon: L.divIcon({ className: "ob-marker", html: `▲ +${ob.clearance_m} m`, iconSize: [60, 18], iconAnchor: [30, 9] }),
    })
      .bindTooltip(`Blocking ridge · ground ${ob.terrain_m} m, standing +${ob.clearance_m} m above the line-of-sight to ${ob.node_id}. The signal must diffract over it, so the area beyond is down-weighted.`, { sticky: true })
      .addTo(posteriorLayer);
  }
}

function render() {
  const f = activeFilters();
  ellipseLayer.clearLayers();
  centerLayer.clearLayers();
  bearingLayer.clearLayers();

  // heat is drawn by renderPosterior() (its own layer); here we only need to know
  // if it's on, to draw the selected ellipse as a dashed outline. Either the
  // fetched Copernicus posterior OR an applied enhanced override counts as heat.
  const enhOn = state.enhanced && state.enhanced.fixId === state.selected && !!state.enhanced.fc;
  const baseOn = state.posterior && state.posterior.fixId === state.selected && !!state.posterior.fc;
  const heatOn = f.showPosterior && (enhOn || baseOn);

  // ellipses + centers
  for (const [id, props] of state.fixes) {
    if (!passesFilter(props, f)) continue;
    const sel = id === state.selected;
    const color = confColor(props);
    const ring = state.ellipses.get(id);
    if (ring) {
      const ellipseColor = confidenceColor(props);
      // When the heat is shown under a selected fix, draw the ellipse as a
      // DASHED OUTLINE only so the cyan posterior is the readable fill and the
      // two layers don't blend into one blob.
      const outline = sel && heatOn;
      L.polygon(ring, {
        color: ellipseColor, weight: sel ? 3 : 1.5, opacity: sel ? 1 : 0.85,
        fillColor: ellipseColor,
        fillOpacity: props.stale ? 0.04 : (outline ? 0.0 : (sel ? confOpacity(props) + 0.06 : confOpacity(props))),
        dashArray: outline ? "7 5" : (props.stale ? "4 4" : null),
      })
        .bindTooltip("95% estimated emitter area", { sticky: true })
        .on("click", () => selectFix(id)).addTo(ellipseLayer);
    }
    const c = state.centers.get(id);
    if (c) {
      const m = L.circleMarker(c, {
        radius: sel ? 7 : 5, color: "#0b0e12", weight: 1,
        fillColor: color, fillOpacity: 1,
      }).on("click", () => selectFix(id)).addTo(centerLayer);
      if (sel) m.bindTooltip(centerCallout(props), { permanent: true, direction: "top", className: "fix-callout", offset: [0, -8] });
    }
  }

  // bearings (node markers + LOBs); outliers highlighted vs selected fix
  if (f.showBearings) {
    const selProps = state.selected ? state.fixes.get(state.selected) : null;
    const outliers = selProps ? outlierNodes(selProps) : new Set();
    for (const feat of state.bearings) {
      const p = feat.properties;
      const isOut = outliers.has(p.node_id);
      // Friendly sensors = GREEN; a residual outlier = amber (suspect), never red
      // (red is reserved for the threat/emitter).
      const col = isOut ? "#f1c40f" : "#2ecc71";
      if (p.feature_kind === "node") {
        const [lon, lat] = feat.geometry.coordinates;
        L.circleMarker([lat, lon], {
          radius: 5, color: "#0b0e12", weight: 1, fillColor: col, fillOpacity: 1,
        }).bindTooltip(p.node_id + (isOut ? " ⚠ outlier" : ""), { className: "node-label" })
          .addTo(bearingLayer);
      } else if (p.feature_kind === "lob") {
        const latlngs = feat.geometry.coordinates.map(([lon, lat]) => [lat, lon]);
        L.polyline(latlngs, { color: col, weight: isOut ? 2.5 : 1.2, opacity: 0.7, dashArray: "6 6" })
          .addTo(bearingLayer);
      }
    }
  }

  renderList(f);
  renderDetail();
  renderInvestigation();
  updateRfStatus();

  if (!state.firstFit && state.centers.size > 0) {
    const pts = [...state.centers.values()];
    map.fitBounds(pts, { padding: [60, 60], maxZoom: 14 });
    state.firstFit = true;
  }
}

function renderList(f) {
  const ul = $("#fix-list");
  const detailPanel = $("#detail-panel");
  detailPanel.remove();
  ul.innerHTML = "";
  const items = [...state.fixes.values()]
    .filter((p) => passesFilter(p, f))
    .sort((a, b) => b.t_unix_ns - a.t_unix_ns);
  $("#fix-count").textContent = items.length;
  let detailPlaced = false;
  for (const p of items) {
    const li = document.createElement("li");
    li.className = "fix-item" + (p.fix_id === state.selected ? " selected" : "") + (p.stale ? " stale" : "");
    li.innerHTML = `
      <span class="swatch" style="background:${confidenceColor(p)}"></span>
      <div>
        <div class="meta"><b>${p.confidence_level.toUpperCase()}</b> · ${p.contributing_nodes.length} nodes · ${fmtAge(p)}</div>
        <div class="meta">GDOP ${p.gdop.toFixed(2)} · ${p.method}${p.emitter_class ? " · " + p.emitter_class : ""}</div>
      </div>`;
    li.onclick = () => selectFix(p.fix_id);
    ul.appendChild(li);
    if (p.fix_id === state.selected) {
      const slot = document.createElement("li");
      slot.appendChild(detailPanel);
      ul.appendChild(slot);
      detailPlaced = true;
    }
  }
  if (!detailPlaced) {
    $("#list-panel").after(detailPanel);
  }
}

function renderDetail() {
  const dl = $("#detail");
  const empty = $("#detail-empty");
  const btn = $("#send-btn");
  const confirm = $("#send-confirm");
  const wall = $("#engage-wall");
  const p = state.selected ? state.fixes.get(state.selected) : null;
  if (!p) {
    dl.hidden = true; btn.hidden = true; confirm.hidden = true; empty.hidden = false;
    if (wall) wall.hidden = true;
    state.armed = false;
    return;
  }
  empty.hidden = true; dl.hidden = false;
  if (wall) wall.hidden = false;
  // arm-then-send: either the Send button OR the inline confirm, never both.
  btn.hidden = state.armed;
  confirm.hidden = !state.armed;
  if (state.armed) {
    $("#confirm-target").textContent =
      `${p.confidence_level.toUpperCase()} · ${p.mgrs || (p.lat.toFixed(4) + "," + p.lon.toFixed(4))} · ` +
      `${Math.round(p.semi_major_m)}×${Math.round(p.semi_minor_m)} m`;
  }

  const outliers = outlierNodes(p);
  const nodeRows = (p.contributing_nodes || []).map((n, i) => {
    const r = (p.residuals_deg[i] ?? 0).toFixed(2);
    const cls = outliers.has(n) ? ' class="res-out"' : "";
    return `<span${cls}>${n}: ${r}°${outliers.has(n) ? " ⚠" : ""}</span>`;
  }).join("<br>");

  const rows = [
    ["MGRS", p.mgrs || "—"],
    ["Lat/Lon", `${p.lat.toFixed(5)}, ${p.lon.toFixed(5)}`],
    ["Confidence", p.confidence_level.toUpperCase()],
    ["Age", fmtAge(p) + (p.stale ? " (STALE)" : "")],
    ["Semi-major", `${Math.round(p.semi_major_m)} m`],
    ["Semi-minor", `${Math.round(p.semi_minor_m)} m`],
    ["Orientation", `${p.orientation_deg.toFixed(1)}°`],
    ["GDOP " + iTag("gdop"), p.gdop_uncomputable_reason
      ? `uncomputable (${p.gdop_uncomputable_reason})`
      : p.gdop.toFixed(2)],
    ["Method", p.method + (p.method === "fallback_centroid" ? " ⚠ suspect" : "")],
    ["Emitter", p.emitter_class || "—"],
    ["Residuals", nodeRows || "—"],
  ];
  dl.innerHTML = rows.map(([k, v]) => {
    const bad = (k === "Age" && p.stale) || (k === "Method" && p.method === "fallback_centroid");
    return `<dt>${k}</dt><dd class="${bad ? "bad" : ""}">${v}</dd>`;
  }).join("");
}

function selectFix(id) {
  if (id === state.selected) {
    state.armed = false;
    state.selected = null;
    state.posterior = null;
    state.investigation = null;
    state.emitterH = null;
    if (state.enhance) cancelEnhance("aborted");
    state.enhanced = null;
    state.panelOpen = false;
    renderPosterior();
    render();
    return;
  }
  if (id !== state.selected) {
    state.armed = false; // picking another fix defers any pending send
    state.posterior = null; // drop stale heat until the new one loads
    state.investigation = null;
    state.emitterH = null;  // reset tracked emitter height for the new fix
    if (state.enhance) cancelEnhance("aborted"); // abort any in-flight enhance for the old fix
    state.enhanced = null;  // drop any applied enhanced override
    state.panelOpen = false;
    fetchPosterior(id);     // async; re-renders when it arrives
    fetchInvestigation(id); // async; ranked candidate panel
  }
  state.selected = id;
  const c = state.centers.get(id);
  if (c) map.panTo(c, { animate: true });
  render();
}

// ---- send flow: inline arm -> confirm. Defer = do nothing / pan / pick another / Esc ----
function arm() {
  if (!state.selected) return;
  state.armed = true;
  render();
  $("#confirm-send").focus();
}
function disarm() {
  if (!state.armed) return;
  state.armed = false;
  render();
  $("#send-btn").focus();
}

async function doSend() {
  const id = state.selected;
  state.armed = false;
  render();
  if (!id) return;
  try {
    const res = await fetch(`fixes/${id}/send`, { method: "POST" });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || ("HTTP " + res.status));
    toast("Sent to troops ✓ " + (body.detail || ""), "ok");
  } catch (e) {
    toast("Send failed: " + e.message, "err");
  }
}

// ---- wire up ----
$("#send-btn").onclick = arm;
$("#confirm-cancel").onclick = disarm;
$("#confirm-send").onclick = doSend;
document.addEventListener("keydown", (e) => { if (e.key === "Escape") disarm(); });
for (const el of document.querySelectorAll(
  "#conf-filter input, #class-filter, #min-nodes, #show-stale, #show-bearings, #show-posterior"
)) {
  el.addEventListener("change", () => { state.armed = false; render(); });
}
$("#show-posterior").addEventListener("change", (e) => {
  if (e.target.checked && state.selected) fetchPosterior(state.selected);
  else {
    // Toggling off aborts any in-flight enhance and clears the applied override.
    if (state.enhance) cancelEnhance("aborted");
    state.enhanced = null;
    state.panelOpen = false;
    state.posterior = null; renderPosterior(); render();
  }
});

async function tick() {
  await Promise.all([pollFixes(), pollBearings()]);
  render();
}
fetchEnhanceOptions(); // cache enhance options once on load
tick();
setInterval(tick, POLL_MS);
