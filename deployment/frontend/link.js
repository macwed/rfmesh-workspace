/*
 * link.js — comms-first soldier UI behaviour.
 *
 * Wires the link.html shell to the backend:
 *   - WebSocket /ws/ui for live bearing + state push from backend
 *   - HTTP POST /command/{node_id} for manual-steer
 *   - HTTP POST /command_broadcast for ALL-STOP
 *
 * No matplotlib. Pure Leaflet + DOM. Soldier-grade UX requirements
 * sourced from the demo-integrity council report (ADR-018):
 *   1. Slider hard-clamps at calibrated arc.
 *   2. Backend refuses out-of-arc commands; we render the red toast.
 *   3. ALL-STOP two-tap confirm.
 *   4. Calibration check disables steering for uncalibrated nodes.
 *   5. Link margin shown in dB ("+18 dB"), never absolute.
 *   6. Last-acquired timestamp visible on every link.
 *   7. WS reconnect with banner; controls disabled when offline.
 *
 * The backend doesn't push node-cal-limits yet (it lives in the firmware
 * NVS and isn't yet exposed over HTTP). For v1.0 we default the slider
 * to ±90°; once the backend grows a /node/{id}/capabilities route, we
 * pull the per-node clamps from there.
 */

(function () {
  "use strict";

  // ---------------------------------------------------------------
  // State
  // ---------------------------------------------------------------

  const state = {
    nodes: new Map(), // node_id -> {node_id, lat, lon, state, last_bearing_t, ...}
    selectedNodeId: null,
    ws: null,
    wsConnected: false,
    reconnectDelay: 1000,
  };

  // ---------------------------------------------------------------
  // Leaflet map setup
  // ---------------------------------------------------------------

  const map = L.map("map").setView([50.33, 5.0], 9);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map);

  // One Leaflet marker per node; redrawn on every state update.
  const nodeMarkers = new Map(); // node_id -> L.Marker
  const nodeArrows = new Map(); // node_id -> L.Polyline (boresight pointer)

  function nodeIconHtml(stateName) {
    return `<div class="node-marker ${stateName || "stale"}"></div>`;
  }

  function renderNode(node) {
    if (node.lat == null || node.lon == null) return;
    const latlng = [node.lat, node.lon];
    let m = nodeMarkers.get(node.node_id);
    if (!m) {
      m = L.marker(latlng, {
        icon: L.divIcon({
          className: "",
          html: nodeIconHtml(node.state),
          iconSize: [22, 22],
          iconAnchor: [11, 11],
        }),
        title: node.node_id,
      }).addTo(map);
      m.on("click", () => selectNode(node.node_id));
      nodeMarkers.set(node.node_id, m);
    } else {
      m.setLatLng(latlng);
      m.setIcon(
        L.divIcon({
          className: "",
          html: nodeIconHtml(node.state),
          iconSize: [22, 22],
          iconAnchor: [11, 11],
        }),
      );
    }
    // Pointing arrow: ~500 m along the boresight (azimuth_deg).
    if (typeof node.azimuth_deg === "number") {
      const dest = projectAlongAzimuth(node.lat, node.lon, node.azimuth_deg, 500);
      let arrow = nodeArrows.get(node.node_id);
      if (!arrow) {
        arrow = L.polyline([latlng, dest], { color: "#4aa8ff", weight: 3 }).addTo(map);
        nodeArrows.set(node.node_id, arrow);
      } else {
        arrow.setLatLngs([latlng, dest]);
      }
    }
  }

  function projectAlongAzimuth(lat, lon, azDeg, distM) {
    // Approximate equirectangular projection — fine for short distances.
    const R = 6371000;
    const az = (azDeg * Math.PI) / 180;
    const dLat = (distM * Math.cos(az)) / R;
    const dLon = (distM * Math.sin(az)) / (R * Math.cos((lat * Math.PI) / 180));
    return [lat + (dLat * 180) / Math.PI, lon + (dLon * 180) / Math.PI];
  }

  // ---------------------------------------------------------------
  // Node list (sidebar)
  // ---------------------------------------------------------------

  const nodeListEl = document.getElementById("node-list");
  const noNodesHintEl = document.getElementById("no-nodes-hint");

  function renderNodeList() {
    nodeListEl.innerHTML = "";
    const nodes = [...state.nodes.values()];
    nodes.sort((a, b) => a.node_id.localeCompare(b.node_id));
    for (const node of nodes) {
      const li = document.createElement("li");
      if (node.node_id === state.selectedNodeId) li.classList.add("selected");
      const cs = document.createElement("span");
      cs.className = "callsign";
      cs.textContent = node.node_id;
      const badge = document.createElement("span");
      badge.className = "badge " + (node.state || "stale");
      badge.textContent = badgeLabel(node);
      li.append(cs, badge);
      li.addEventListener("click", () => selectNode(node.node_id));
      nodeListEl.append(li);
    }
    noNodesHintEl.hidden = nodes.length > 0;
  }

  // ---------------------------------------------------------------
  // Detail drawer
  // ---------------------------------------------------------------

  const detailEl = document.getElementById("node-detail");
  const detailCallsignEl = document.getElementById("detail-callsign");
  const detailStateBadge = document.getElementById("detail-state-badge");
  const detailCalEl = document.getElementById("detail-cal");
  const detailPeerEl = document.getElementById("detail-peer");
  const detailMarginEl = document.getElementById("detail-margin");
  const detailLastEl = document.getElementById("detail-last");
  const detailSliderEl = document.getElementById("detail-slider");
  const detailSliderValEl = document.getElementById("detail-slider-value");
  const detailSendBtn = document.getElementById("detail-send-steer");
  const detailStopBtn = document.getElementById("detail-stop");
  const detailMsgEl = document.getElementById("detail-msg");
  const detailClearFaultBtn = document.getElementById("detail-clear-fault");
  const detailCloseBtn = document.getElementById("detail-close");

  function selectNode(nodeId) {
    state.selectedNodeId = nodeId;
    // ADR-022: pull a fresh per-node capability snapshot. The WS push
    // would have already merged the same payload, but a freshly-opened
    // page that selects a node before any push arrives must still be
    // able to clamp the slider.
    fetchNodeCapabilities(nodeId);
    renderNodeList();
    renderDetail();
  }

  async function fetchNodeCapabilities(nodeId) {
    try {
      const resp = await fetch(`/node/${encodeURIComponent(nodeId)}/capabilities`);
      if (!resp.ok) return;
      const body = await resp.json();
      mergeHello(body.node_id, body.hello || {});
    } catch (e) {
      console.warn(`capability fetch failed for ${nodeId}: ${e.message}`);
    }
  }

  async function hydrateAllCapabilities() {
    try {
      const resp = await fetch("/nodes/capabilities");
      if (!resp.ok) return;
      const body = await resp.json();
      for (const entry of body.nodes || []) {
        mergeHello(entry.node_id, entry.hello || {});
      }
    } catch (e) {
      console.warn(`capability list fetch failed: ${e.message}`);
    }
  }

  detailCloseBtn.addEventListener("click", () => {
    state.selectedNodeId = null;
    renderNodeList();
    renderDetail();
  });

  function renderDetail() {
    const id = state.selectedNodeId;
    if (!id || !state.nodes.has(id)) {
      detailEl.hidden = true;
      return;
    }
    const n = state.nodes.get(id);
    detailEl.hidden = false;
    detailCallsignEl.textContent = n.node_id;
    detailStateBadge.className = "badge " + (n.state || "stale");
    detailStateBadge.textContent = badgeLabel(n);
    detailCalEl.textContent = n.cal_label || "unknown";
    detailPeerEl.textContent = (n.peer && n.peer.node_id) || n.peer_id || "—";
    detailMarginEl.textContent =
      typeof n.link_margin_db === "number"
        ? `${n.link_margin_db.toFixed(1)} dB`
        : "—";
    detailLastEl.textContent = n.last_acquired_age || "never";

    // ADR-022 manual-steer slider clamp: if the node has reported a
    // calibrated_geographic_arc_deg in its node_hello, clamp the slider
    // to that arc. A wrap-around arc (min > max — straddles 0° true)
    // is not safely representable on a single linear <input type=range>;
    // refuse loudly rather than render a broken slider (B3).
    n.cal_arc_wraps = false;
    if (n.cal_arc && Number.isFinite(n.cal_arc.min) && Number.isFinite(n.cal_arc.max)) {
      if (n.cal_arc.min > n.cal_arc.max) {
        n.cal_arc_wraps = true;
        console.warn(
          `${n.node_id}: calibrated arc straddles 0° (` +
            `${n.cal_arc.min}-${n.cal_arc.max}); slider disabled until ` +
            `wrap-arc rendering ships.`,
        );
      } else {
        detailSliderEl.min = n.cal_arc.min;
        detailSliderEl.max = n.cal_arc.max;
        const cur = parseFloat(detailSliderEl.value);
        if (!(cur >= n.cal_arc.min && cur <= n.cal_arc.max)) {
          const mid = (n.cal_arc.min + n.cal_arc.max) / 2;
          detailSliderEl.value = mid;
          detailSliderValEl.textContent = `${mid.toFixed(1)}°`;
        }
      }
    }

    // Steering controls enabled iff (a) WS to backend is up,
    // (b) node has a known calibrated arc that does NOT wrap 0°,
    // (c) node has a wired controller (ADR-022 stub refuses until
    // NodeController lands), (d) node is not in FAULT.
    const calibrated =
      !!(n.cal_arc && Number.isFinite(n.cal_arc.min)) && !n.cal_arc_wraps;
    const controllerReady = !!n.controller_ready;
    const canSteer =
      state.wsConnected && calibrated && controllerReady && n.state !== "fault";
    detailSliderEl.disabled = !canSteer;
    detailSendBtn.disabled = !canSteer;
    if (!canSteer) {
      let reason;
      if (!state.wsConnected) reason = "Backend offline — controls disabled.";
      else if (!calibrated) reason = "Node arc unknown — awaiting handshake.";
      else if (!controllerReady) {
        reason = "Manual steering not yet enabled on this node.";
        console.warn(
          `${n.node_id}: NodeController not wired (ADR-022 stub); steer disabled.`,
        );
      } else if (n.state === "fault") {
        // ADR-024 §8: FAULT is sticky and requires operator ack.
        // Surface status_detail + show the Clear FAULT button below.
        const detail = n.status_detail ? ` — ${n.status_detail}` : "";
        reason = `Node is FAULT${detail}. Press "Clear FAULT" after fixing the underlying condition.`;
      } else reason = "Manual steering disabled.";
      detailMsgEl.textContent = reason;
      detailMsgEl.style.color = "var(--low)";
    } else {
      detailMsgEl.textContent = "";
    }
    // ADR-024 §8: Clear FAULT button visible only in FAULT state.
    detailClearFaultBtn.hidden = n.state !== "fault";
  }

  detailSliderEl.addEventListener("input", () => {
    detailSliderValEl.textContent = `${detailSliderEl.value}°`;
  });

  detailSendBtn.addEventListener("click", async () => {
    const id = state.selectedNodeId;
    if (!id) return;
    const angle = parseFloat(detailSliderEl.value);
    detailMsgEl.textContent = "Sending…";
    detailMsgEl.style.color = "var(--muted)";
    try {
      const resp = await fetch(`/command/${encodeURIComponent(id)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          kind: "manual_steer",
          axis: 0,
          target_angle_deg: angle,
          requestor_id: "ui-link",
        }),
      });
      const body = await resp.json().catch(() => ({}));
      if (resp.ok) {
        detailMsgEl.textContent = `Sent → ${id}`;
        detailMsgEl.style.color = "var(--high)";
      } else {
        const reason = body.detail || resp.statusText;
        detailMsgEl.textContent = `Refused: ${reason}`;
        detailMsgEl.style.color = "var(--low)";
      }
    } catch (e) {
      detailMsgEl.textContent = `Network error: ${e.message}`;
      detailMsgEl.style.color = "var(--low)";
    }
  });

  detailStopBtn.addEventListener("click", async () => {
    // STOP this node by sending a manual_steer to its current
    // commanded angle (no-op move). The future protocol will add a
    // dedicated single-node "stop" command, but for v1.0 the same
    // mechanism is enough.
    if (!state.selectedNodeId) return;
    detailMsgEl.textContent = "STOP this-node not yet implemented; use ALL STOP.";
    detailMsgEl.style.color = "var(--medium)";
  });

  detailClearFaultBtn.addEventListener("click", async () => {
    const id = state.selectedNodeId;
    if (!id) return;
    detailMsgEl.textContent = "Clearing FAULT…";
    detailMsgEl.style.color = "var(--muted)";
    try {
      const resp = await fetch(`/node/${encodeURIComponent(id)}/clear_fault`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: "clear_fault", requestor_id: "ui-link" }),
      });
      const body = await resp.json().catch(() => ({}));
      if (resp.ok) {
        detailMsgEl.textContent = `FAULT cleared on ${id}.`;
        detailMsgEl.style.color = "var(--high)";
      } else {
        detailMsgEl.textContent = `Clear FAULT refused: ${body.detail || resp.statusText}`;
        detailMsgEl.style.color = "var(--low)";
      }
    } catch (e) {
      detailMsgEl.textContent = `Network error: ${e.message}`;
      detailMsgEl.style.color = "var(--low)";
    }
  });

  // ---------------------------------------------------------------
  // ALL-STOP
  // ---------------------------------------------------------------

  const allStopBtn = document.getElementById("all-stop-btn");
  const allStopConfirm = document.getElementById("all-stop-confirm");
  const allStopConfirmBtn = document.getElementById("all-stop-confirm-btn");
  const allStopCancelBtn = document.getElementById("all-stop-cancel-btn");

  allStopBtn.addEventListener("click", () => {
    allStopConfirm.hidden = false;
    allStopBtn.disabled = true;
  });
  allStopCancelBtn.addEventListener("click", () => {
    allStopConfirm.hidden = true;
    allStopBtn.disabled = false;
  });
  allStopConfirmBtn.addEventListener("click", async () => {
    try {
      const resp = await fetch("/command_broadcast", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: "all_stop", requestor_id: "ui-link" }),
      });
      const body = await resp.json().catch(() => ({}));
      if (resp.ok) {
        setStatus(
          `ALL-STOP sent: delivered=${(body.delivered_to || []).length}, ` +
            `failed=${(body.failed || []).length}`,
        );
      } else {
        setStatus(`ALL-STOP failed: ${body.detail || resp.statusText}`);
      }
    } catch (e) {
      setStatus(`ALL-STOP network error: ${e.message}`);
    } finally {
      allStopConfirm.hidden = true;
      allStopBtn.disabled = false;
    }
  });

  // ---------------------------------------------------------------
  // WS connection + push consumption
  // ---------------------------------------------------------------

  const statusLineEl = document.getElementById("status-line");
  function setStatus(s) {
    statusLineEl.textContent = s;
  }

  function connectWs() {
    const url =
      (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws/ui";
    let ws;
    try {
      ws = new WebSocket(url);
    } catch (e) {
      setStatus(`WS construct failed: ${e.message}`);
      scheduleReconnect();
      return;
    }
    state.ws = ws;
    ws.addEventListener("open", () => {
      state.wsConnected = true;
      state.reconnectDelay = 1000;
      setStatus("live");
      renderDetail();
    });
    ws.addEventListener("close", () => {
      state.wsConnected = false;
      setStatus("WS closed — reconnecting…");
      renderDetail();
      scheduleReconnect();
    });
    ws.addEventListener("error", () => {
      setStatus("WS error");
    });
    ws.addEventListener("message", (ev) => {
      let payload;
      try {
        payload = JSON.parse(ev.data);
      } catch (e) {
        console.warn("non-JSON WS frame", e);
        return;
      }
      handlePayload(payload);
    });
  }

  function scheduleReconnect() {
    const delay = state.reconnectDelay;
    state.reconnectDelay = Math.min(state.reconnectDelay * 2, 30000);
    setTimeout(connectWs, delay);
  }

  function handlePayload(payload) {
    if (!payload || !payload.kind) return;
    if (payload.kind === "bearing") {
      mergeBearing(payload.data || {});
    } else if (payload.kind === "node_hello") {
      // ADR-022: backend forwarded a freshly-arrived capability snapshot.
      // Cache the slider clamp + light up the node in the list even
      // before the first /bearings push arrives.
      mergeHello(payload.node_id, payload.data || {});
    } else if (payload.kind === "node_state") {
      // ADR-024: NodeController emitted a state transition. Update the
      // badge + countdown without waiting for the next heartbeat.
      mergeControllerState(payload.node_id, payload.data || {});
    } else if (payload.kind === "node_status") {
      // ADR-022 heartbeat fan-out: NodeStatus arrived (~2s cadence).
      // Carries position + gnss_locked + healthy + status_detail.
      mergeNodeStatus(payload.node_id, payload.data || {});
    } else if (payload.kind === "command_refused") {
      // Node-side refusal (B3). Surface red toast on the detail panel.
      showRefusal(payload.node_id, payload.data || {});
    }
    // Future kinds. Schema additive; unknown kinds ignored honestly.
  }

  function mergeNodeStatus(nodeId, status) {
    if (!nodeId) return;
    const n = state.nodes.get(nodeId) || { node_id: nodeId };
    if (status.position) {
      n.lat = status.position.lat_deg;
      n.lon = status.position.lon_deg;
    }
    n.gnss_locked = !!status.gnss_locked;
    n.healthy = !!status.healthy;
    // Only overwrite status_detail when the heartbeat carries one --
    // the controller's node_state push is the more current source for
    // mode-specific reasons (e.g. mode_drain_timeout).
    if (status.status_detail) {
      n.status_detail = status.status_detail;
    }
    n.last_heartbeat_t = Date.now();
    state.nodes.set(nodeId, n);
    renderNode(n);
    renderNodeList();
    if (state.selectedNodeId === nodeId) renderDetail();
  }

  function mergeControllerState(nodeId, snap) {
    if (!nodeId) return;
    const n = state.nodes.get(nodeId) || { node_id: nodeId };
    if (typeof snap.state === "string") n.state = snap.state;
    n.status_detail = snap.status_detail || "";
    n.manual_hold_expires_at_ns = snap.manual_hold_expires_at_ns ?? null;
    n.last_commanded_angle_deg = snap.last_commanded_angle_deg ?? null;
    if (typeof snap.controller_ready === "boolean") {
      n.controller_ready = snap.controller_ready;
    }
    state.nodes.set(nodeId, n);
    renderNode(n);
    renderNodeList();
    if (state.selectedNodeId === nodeId) renderDetail();
  }

  function mergeHello(nodeId, hello) {
    if (!nodeId) return;
    const n = state.nodes.get(nodeId) || { node_id: nodeId };
    if (hello.position) {
      n.lat = hello.position.lat_deg;
      n.lon = hello.position.lon_deg;
    }
    n.active_capabilities = hello.active_capabilities || [];
    n.heading_deg = hello.heading_deg;
    n.cal_arc = hello.calibrated_geographic_arc_deg || null;
    n.cal_label = hello.cal_provenance
      ? `${hello.cal_provenance}`
      : "unknown";
    n.peer = hello.peer || null;
    n.controller_ready = !!hello.controller_ready;
    // ADR-024 controller snapshot, if present on this hello.
    if (typeof hello.state === "string") n.state = hello.state;
    n.status_detail = hello.status_detail || "";
    n.manual_hold_expires_at_ns = hello.manual_hold_expires_at_ns ?? null;
    n.last_commanded_angle_deg = hello.last_commanded_angle_deg ?? null;
    // Fallback for nodes that have not yet sent a state: "searching"
    // until a bearing arrives.
    if (!n.state) n.state = "searching";
    state.nodes.set(nodeId, n);
    renderNode(n);
    renderNodeList();
    if (state.selectedNodeId === nodeId) renderDetail();
  }

  function manualHoldRemainingS(node) {
    if (!node || node.state !== "manual_hold" || !node.manual_hold_expires_at_ns) {
      return null;
    }
    const remainingMs = node.manual_hold_expires_at_ns / 1e6 - Date.now();
    return remainingMs > 0 ? Math.ceil(remainingMs / 1000) : 0;
  }

  function badgeLabel(node) {
    const s = node.state || "stale";
    if (s === "manual_hold") {
      const r = manualHoldRemainingS(node);
      return r !== null ? `manual · ${r}s` : "manual";
    }
    if (s === "acquired_peer") return "linked";
    return s;
  }

  function showRefusal(nodeId, payload) {
    if (state.selectedNodeId !== nodeId) {
      // Not selected -- log to console; the next time the user opens
      // this node's drawer they'll see the controller_ready=false note.
      console.warn(`refusal from ${nodeId}: ${payload.reason || "(no reason)"}`);
      return;
    }
    detailMsgEl.textContent = `Refused: ${payload.reason || "(no reason)"}`;
    detailMsgEl.style.color = "var(--low)";
  }

  function mergeBearing(report) {
    const id = report.node_id;
    if (!id) return;
    const n = state.nodes.get(id) || { node_id: id };
    if (report.node_position) {
      n.lat = report.node_position.lat_deg;
      n.lon = report.node_position.lon_deg;
    }
    n.azimuth_deg = report.azimuth_deg;
    n.azimuth_sigma_deg = report.azimuth_sigma_deg;
    n.snr_db = report.snr_db;
    n.last_bearing_t = Date.now();
    // For v1.0 we treat every emitted bearing as "acquired"; the
    // proper state machine (SWEEPING / ACQUIRED_PEER / etc.) lands
    // when NodeController + bearing_purpose tag ship.
    n.state = "acquired";
    n.last_acquired_age = "just now";
    state.nodes.set(id, n);
    renderNode(n);
    renderNodeList();
    if (state.selectedNodeId === id) renderDetail();
  }

  // Periodic 1 s tick: bearing-age labels + MANUAL_HOLD countdown.
  setInterval(() => {
    const now = Date.now();
    let changed = false;
    for (const n of state.nodes.values()) {
      // Bearing-driven legacy state. Skip when the controller has set
      // an authoritative state (sweeping/manual_hold/parked/fault/
      // acquired_peer) -- those flips come from the node_state push.
      const controllerOwnsState = [
        "sweeping",
        "manual_hold",
        "parked",
        "fault",
        "acquired_peer",
      ].includes(n.state);
      if (!controllerOwnsState && n.last_bearing_t != null) {
        const ageS = Math.floor((now - n.last_bearing_t) / 1000);
        const newLabel =
          ageS < 2
            ? "just now"
            : ageS < 60
              ? `${ageS} s ago`
              : `${Math.floor(ageS / 60)} min ago`;
        const newState = ageS < 10 ? "acquired" : ageS < 30 ? "searching" : "stale";
        if (n.last_acquired_age !== newLabel || n.state !== newState) {
          n.last_acquired_age = newLabel;
          n.state = newState;
          changed = true;
          renderNode(n);
        }
      }
      // ADR-024 MANUAL_HOLD countdown: re-render once a second so the
      // badge label ticks down (manual · 7s -> 6s -> ...). When the
      // countdown reaches 0, the node-side controller auto-resumes and
      // publishes a node_state frame, which flips us out of MANUAL_HOLD.
      if (n.state === "manual_hold" && n.manual_hold_expires_at_ns) {
        changed = true;
        renderNode(n);
      }
    }
    if (changed) {
      renderNodeList();
      if (state.selectedNodeId) renderDetail();
    }
  }, 1000);

  // ---------------------------------------------------------------
  // Boot
  // ---------------------------------------------------------------

  connectWs();
  hydrateAllCapabilities();
  renderNodeList();
  renderDetail();
})();
