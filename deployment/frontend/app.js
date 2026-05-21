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
  firstFit: false,
};

// ---- map ----
const map = L.map("map", { zoomControl: true }).setView(DEFAULT_VIEW, 13);
L.tileLayer(TILE_URL, {
  maxZoom: 19,
  attribution: "© OpenStreetMap contributors",
}).addTo(map);

const posteriorLayer = L.layerGroup().addTo(map);  // under the ellipse (added first)
const ellipseLayer = L.layerGroup().addTo(map);
const centerLayer = L.layerGroup().addTo(map);
const bearingLayer = L.layerGroup().addTo(map);

// RF-plausibility heat: inner (higher mass) = more opaque cyan.
const POSTERIOR_STYLE = {
  0.5: { fillOpacity: 0.40, color: "#00e5ff", weight: 1 },
  0.8: { fillOpacity: 0.22, color: "#00e5ff", weight: 0.8 },
  0.95: { fillOpacity: 0.10, color: "#00e5ff", weight: 0.6 },
};

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

async function fetchPosterior(id) {
  if (!$("#show-posterior").checked) { state.posterior = null; return; }
  try {
    const res = await fetch(`fixes/${id}/posterior`);
    if (!res.ok) throw new Error("HTTP " + res.status);
    state.posterior = { fixId: id, fc: await res.json() };
  } catch (e) {
    state.posterior = null;
  }
  render();
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
  if (f.showPosterior && state.posterior && state.posterior.fixId === state.selected) {
    for (const feat of state.posterior.fc.features) {
      const st = POSTERIOR_STYLE[feat.properties.p_band] || POSTERIOR_STYLE[0.95];
      L.geoJSON(feat, { style: { ...st, fillColor: st.color } }).addTo(posteriorLayer);
    }
  }

  // ellipses + centers
  for (const [id, props] of state.fixes) {
    if (!passesFilter(props, f)) continue;
    const sel = id === state.selected;
    const color = confColor(props);
    const ring = state.ellipses.get(id);
    if (ring) {
      L.polygon(ring, {
        color, weight: sel ? 3 : 1.5, opacity: sel ? 1 : 0.85,
        fillColor: color, fillOpacity: props.stale ? 0.05 : (sel ? 0.3 : 0.18),
        dashArray: props.stale ? "4 4" : null,
      }).on("click", () => selectFix(id)).addTo(ellipseLayer);
    }
    const c = state.centers.get(id);
    if (c) {
      L.circleMarker(c, {
        radius: sel ? 7 : 5, color: "#0b0e12", weight: 1,
        fillColor: color, fillOpacity: 1,
      }).on("click", () => selectFix(id)).addTo(centerLayer);
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
  const p = state.selected ? state.fixes.get(state.selected) : null;
  if (!p) {
    dl.hidden = true; btn.hidden = true; confirm.hidden = true; empty.hidden = false;
    state.armed = false;
    return;
  }
  empty.hidden = true; dl.hidden = false;
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
    ["GDOP", p.gdop.toFixed(2)],
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
    fetchPosterior(id);     // async; re-renders when it arrives
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
