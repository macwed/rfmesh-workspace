"use strict";

// Shared map core for the exposure pages (jam / hide / emit / ideal). Standalone
// from app.js (which powers locate.html). Exposes window.RFCore.
// Color law everywhere: RED = red team / threat / exposed, GREEN = friendly /
// lower-exposure. "green" is a colour, never a worded "safe" claim — labels stay
// relative (ADR-016/017 honesty locks).

const RFCore = (() => {
  const DEFAULT_VIEW = [50.356, 5.0];
  const TILE_URL = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";

  function addLocateControl(map) {
    let marker = null;
    const control = L.control({ position: "bottomleft" });
    control.onAdd = () => {
      const wrap = L.DomUtil.create("div", "map-locate");
      const button = L.DomUtil.create("button", "", wrap);
      button.type = "button";
      button.textContent = "Locate me";
      button.setAttribute("aria-label", "Locate me");
      L.DomEvent.disableClickPropagation(wrap);
      L.DomEvent.disableScrollPropagation(wrap);
      L.DomEvent.on(button, "click", () => {
        if (!navigator.geolocation) {
          toast("Location is unavailable in this browser.", "err");
          return;
        }
        navigator.geolocation.getCurrentPosition(
          ({ coords }) => {
            const position = [coords.latitude, coords.longitude];
            map.setView(position, Math.max(map.getZoom(), 16));
            if (marker) {
              marker.setLatLng(position);
              return;
            }
            marker = L.circleMarker(position, {
              radius: 6,
              color: "#d6dde6",
              weight: 2,
              fillColor: "#4aa8ff",
              fillOpacity: 1,
            }).bindTooltip("You are here").addTo(map);
          },
          () => toast("Unable to access your location.", "err"),
          { enableHighAccuracy: true, timeout: 10000, maximumAge: 30000 },
        );
      });
      return wrap;
    };
    control.addTo(map);
  }

  function makeMap(elId) {
    const map = L.map(elId, { zoomControl: true }).setView(DEFAULT_VIEW, 13);
    const street = L.tileLayer(TILE_URL, { maxZoom: 19, attribution: "© OpenStreetMap" });
    const terrain = L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
      { maxZoom: 19, attribution: "Esri World Topo" });
    const hill = L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/Elevation/World_Hillshade/MapServer/tile/{z}/{y}/{x}",
      { maxZoom: 19, opacity: 0.35, attribution: "Hillshade © Esri" });
    street.addTo(map); hill.addTo(map);
    L.control.layers({ "Street": street, "Terrain": terrain }, { "Hillshade": hill },
      { position: "topleft", collapsed: true }).addTo(map);
    addLocateControl(map);
    return map;
  }

  function toast(msg, kind) {
    const box = document.getElementById("toasts");
    if (!box) return;
    const el = document.createElement("div");
    el.className = "toast " + (kind || "");
    el.textContent = msg;
    box.appendChild(el);
    setTimeout(() => el.remove(), 6000);
  }

  // ---- point-in-polygon (no library), for the hover inspector ----
  function pointInRing(lon, lat, ring) {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const xi = ring[i][0], yi = ring[i][1], xj = ring[j][0], yj = ring[j][1];
      if (((yi > lat) !== (yj > lat)) && (lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi)) inside = !inside;
    }
    return inside;
  }
  function pointInPolygon(lon, lat, rings) {
    if (!rings.length || !pointInRing(lon, lat, rings[0])) return false;
    for (let h = 1; h < rings.length; h++) if (pointInRing(lon, lat, rings[h])) return false;
    return true;
  }
  function pointInGeometry(lon, lat, geom) {
    if (!geom) return false;
    if (geom.type === "Polygon") return pointInPolygon(lon, lat, geom.coordinates);
    if (geom.type === "MultiPolygon") return geom.coordinates.some((p) => pointInPolygon(lon, lat, p));
    return false;
  }

  // band mass -> fill opacity (inner 50% densest)
  const BAND_OP = { 0.5: 0.5, 0.8: 0.3, 0.95: 0.15 };

  // Render an exposure FeatureCollection. colorFor(kind) -> hex. Safe (`mode`)
  // and danger (`mode_danger`) features get green/red per the page config. Draws
  // danger first, safe on top. Returns the drawn bands for hover hit-testing.
  function renderBands(layer, fc, colorFor) {
    layer.clearLayers();
    const drawn = [];
    if (!fc || !fc.features) return drawn;
    const order = [...fc.features].sort((a, b) => {
      const da = a.properties.feature_kind.endsWith("_danger") ? 0 : 1;
      const db = b.properties.feature_kind.endsWith("_danger") ? 0 : 1;
      if (da !== db) return da - db;                 // danger first
      return b.properties.p_band - a.properties.p_band; // outer -> inner
    });
    for (const ft of order) {
      const kind = ft.properties.feature_kind;
      const hue = colorFor(kind);
      const pb = ft.properties.p_band;
      L.geoJSON(ft, { style: { color: hue, weight: 0.8, fillColor: hue, fillOpacity: BAND_OP[pb] || 0.12 } })
        .bindTooltip(`${kind.endsWith("_danger") ? "exposed" : "lower-exposure"} · ${Math.round(pb * 100)}% mass`, { sticky: true })
        .addTo(layer);
      drawn.push({ p_band: pb, kind, props: ft.properties, geometry: ft.geometry });
    }
    return drawn;
  }

  function redIcon(role, erp) {
    const glyph = role === "jammer" ? "📡" : "👁";
    return L.divIcon({ className: "rf-red", html: `<span>${glyph}</span><b>${(erp || "")[0] || ""}</b>`, iconSize: [26, 18], iconAnchor: [13, 9] });
  }
  function autoIcon(role, erp) {
    const glyph = role === "jammer" ? "📡" : "👁";
    return L.divIcon({ className: "rf-red rf-auto", html: `<span>${glyph}</span><b>${(erp || "")[0] || ""}</b>`, iconSize: [26, 18], iconAnchor: [13, 9] });
  }
  function assetIcon() {
    return L.divIcon({ className: "rf-asset", html: "✚", iconSize: [22, 22], iconAnchor: [11, 11] });
  }

  return { makeMap, toast, pointInGeometry, renderBands, redIcon, autoIcon, assetIcon, DEFAULT_VIEW };
})();
