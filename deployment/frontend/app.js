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
};

// ---- map ----
const map = L.map("map", { zoomControl: true }).setView(DEFAULT_VIEW, 13);
// Topographic basemap (contour lines + hillshade) makes the terrain — and so the
// RF-shadow heat's cause — visible. Default to it; keep plain OSM as an option.
const baseTerrain = L.tileLayer("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", {
  maxZoom: 17, attribution: "© OpenTopoMap (CC-BY-SA) · © OpenStreetMap contributors",
});
const baseStreet = L.tileLayer(TILE_URL, { maxZoom: 19, attribution: "© OpenStreetMap contributors" });
baseTerrain.addTo(map);
L.control.layers({ "Terrain (contours)": baseTerrain, "Street": baseStreet }, null, { position: "topleft" }).addTo(map);

// Live RF-status badge over the map (computing / terrain-effect strength / no-data).
const rfStatusEl = document.createElement("div");
rfStatusEl.id = "rf-status";
rfStatusEl.hidden = true;
document.getElementById("map").appendChild(rfStatusEl);

const posteriorLayer = L.layerGroup().addTo(map);  // under the ellipse (added first)
const ellipseLayer = L.layerGroup().addTo(map);
const centerLayer = L.layerGroup().addTo(map);
const bearingLayer = L.layerGroup().addTo(map);

// RF-plausibility heat: inner (higher mass) = more opaque cyan.
const POSTERIOR_STYLE = {
  0.5: { fillOpacity: 0.45, color: "#00e5ff", weight: 1 },
  0.8: { fillOpacity: 0.24, color: "#00e5ff", weight: 0.8 },
  0.95: { fillOpacity: 0.10, color: "#00e5ff", weight: 0.6 },
};

// Map legend: an accordion of expandable "how to read it" rows. Each symbol
// row expands a plain-language block (Means / Read / Do / But) so a no-context
// operator can decode it under stress. Native <details> = zero-JS, accessible,
// keyboard-friendly, localStorage-persistable.
const LEGEND_ROWS = [
  {
    k: "ellipse",
    swatch: '<span class="lg-line" style="border-top:2px dashed #f1c40f"></span>',
    label: "bearing 95% ellipse",
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
    swatch: '<span class="lg-dot"></span><span class="lg-line" style="border-top:2px dashed #4aa8ff"></span>',
    label: "sensor node · bearing",
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

function confColor(props) {
  if (props.stale) return STALE_COLOR;
  return COLORS[props.confidence_level] || STALE_COLOR;
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
  if (!$("#show-posterior").checked) { state.posterior = null; updateRfStatus(); return; }
  state.posterior = { fixId: id, loading: true }; // show "computing…" immediately
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
  render();
}

// Live status badge over the map: computing / terrain-effect strength / no-data.
function updateRfStatus() {
  const el = document.getElementById("rf-status");
  if (!el) return;
  const sp = state.posterior;
  const on = $("#show-posterior") && $("#show-posterior").checked;
  if (!on || !state.selected || !sp || sp.fixId !== state.selected) { el.hidden = true; return; }
  el.hidden = false;
  el.className = "";
  if (sp.loading) { el.innerHTML = '<span class="rf-bar"></span> Computing RF-plausibility…'; el.classList.add("rf-loading"); return; }
  if (sp.error) { el.textContent = "RF-plausibility unavailable"; el.classList.add("rf-warn"); return; }
  const p = sp.props || {};
  if ((p.rf_model || "").startsWith("none")) { el.textContent = "RF-plausibility: no terrain data"; el.classList.add("rf-warn"); return; }
  const lab = (p.rf_effect_label || "?").toUpperCase();
  const ghz = p.band_hz ? (p.band_hz / 1e9).toFixed(2) + " GHz" : "";
  el.textContent = `RF terrain effect: ${lab}${ghz ? " · " + ghz : ""}`;
  el.classList.add("rf-eff-" + (p.rf_effect_label || "na"));
}

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
      if (h && state.selected) fetchPosterior(state.selected, h);
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
function render() {
  const f = activeFilters();
  posteriorLayer.clearLayers();
  ellipseLayer.clearLayers();
  centerLayer.clearLayers();
  bearingLayer.clearLayers();

  // RF-plausibility posterior (under the ellipse), for the selected fix only.
  const heatOn = f.showPosterior && state.posterior && state.posterior.fixId === state.selected && state.posterior.fc;
  if (heatOn) {
    for (const feat of state.posterior.fc.features) {
      const st = POSTERIOR_STYLE[feat.properties.p_band] || POSTERIOR_STYLE[0.95];
      const pct = Math.round(feat.properties.p_band * 100);
      L.geoJSON(feat, { style: { ...st, fillColor: st.color } })
        .bindTooltip(`RF-plausible area · ${pct}% of probability`, { sticky: true })
        .addTo(posteriorLayer);
    }
    // Mark the dominant blocking ridge with its height above the sight-line, so
    // the dimmed area has an obvious, labelled cause.
    const ob = state.posterior.props && state.posterior.props.obstruction;
    if (ob) {
      L.marker([ob.lat, ob.lon], {
        icon: L.divIcon({ className: "ob-marker", html: `▲ +${ob.clearance_m} m`, iconSize: [60, 18], iconAnchor: [30, 9] }),
      })
        .bindTooltip(`Blocking ridge · ground ${ob.terrain_m} m, standing +${ob.clearance_m} m above the line-of-sight to ${ob.node_id}. The signal must diffract over it, so the area beyond is down-weighted.`, { sticky: true })
        .addTo(posteriorLayer);
    }
  }

  // ellipses + centers
  for (const [id, props] of state.fixes) {
    if (!passesFilter(props, f)) continue;
    const sel = id === state.selected;
    const color = confColor(props);
    const ring = state.ellipses.get(id);
    if (ring) {
      // When the heat is shown under a selected fix, draw the ellipse as a
      // DASHED OUTLINE only so the cyan posterior is the readable fill and the
      // two layers don't blend into one blob.
      const outline = sel && heatOn;
      L.polygon(ring, {
        color, weight: sel ? 3 : 1.5, opacity: sel ? 1 : 0.85,
        fillColor: color,
        fillOpacity: props.stale ? 0.04 : (outline ? 0.0 : (sel ? 0.25 : 0.18)),
        dashArray: outline ? "7 5" : (props.stale ? "4 4" : null),
      })
        .bindTooltip(`Bearing 95% ellipse · ${props.confidence_level.toUpperCase()}`, { sticky: true })
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
      const col = isOut ? COLORS.low : "#4aa8ff";
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
  ul.innerHTML = "";
  const items = [...state.fixes.values()]
    .filter((p) => passesFilter(p, f))
    .sort((a, b) => b.t_unix_ns - a.t_unix_ns);
  $("#fix-count").textContent = items.length;
  for (const p of items) {
    const li = document.createElement("li");
    li.className = "fix-item" + (p.fix_id === state.selected ? " selected" : "") + (p.stale ? " stale" : "");
    li.innerHTML = `
      <span class="swatch" style="background:${confColor(p)}"></span>
      <div>
        <div class="meta"><b>${p.confidence_level.toUpperCase()}</b> · ${p.contributing_nodes.length} nodes · ${fmtAge(p)}</div>
        <div class="meta">GDOP ${p.gdop.toFixed(2)} · ${p.method}${p.emitter_class ? " · " + p.emitter_class : ""}</div>
      </div>`;
    li.onclick = () => selectFix(p.fix_id);
    ul.appendChild(li);
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
    ["GDOP " + iTag("gdop"), p.gdop.toFixed(2)],
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
  if (id !== state.selected) {
    state.armed = false; // picking another fix defers any pending send
    state.posterior = null; // drop stale heat until the new one loads
    state.investigation = null;
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
  else { state.posterior = null; render(); }
});

async function tick() {
  await Promise.all([pollFixes(), pollBearings()]);
  render();
}
tick();
setInterval(tick, POLL_MS);
