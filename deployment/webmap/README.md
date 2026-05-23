# FreeTAKHub-WebMap — the live CoT marker map ("what the troops see")

The FTS admin UI at `http://localhost:5055` is **only a dashboard**. It does **not**
render a map of markers. The marker map is a *separate* component:
**FreeTAKHub-WebMap**. This directory stands it up in Docker and wires it to our
bundled FreeTAKServer (`freetakserver`, CoT TCP `:8087`), so an operator can watch
the hostile-emitter markers our app sends land on a real Leaflet map.

---

## TL;DR

| | |
|---|---|
| **URL** | **http://localhost:8081/tak-map** (redirects to `/tak-map/`) |
| **Bring up** | `BACKEND_HOST_PORT=8088 FTSUI_HOST_PORT=5055 docker compose -f deployment/docker-compose.yml -f deployment/docker-compose.dev.yml -f deployment/docker-compose.webmap.yml up -d` |
| **Send a marker** | `curl -X POST localhost:8088/fixes/aaaaaaaa-0000-4000-8000-000000000001/send` |
| **Renders a marker?** | **Yes — verified.** A hostile-emitter (`a-h-G-E-X-N`) marker named `rfmesh` appears at the fix lat/lon. |

> Run the compose command **from the workspace root**
> (`C:\Users\b\Desktop\belgia\rfmesh-workspace`), not from inside `deployment/`.

---

## What this is (and what was chosen)

Upstream FreeTAKHub-WebMap ships **two** ways:

1. A **Node-RED flow** (`freetakhub_webmap_v5.json`) you import into a Node-RED you
   install yourself, plus the `node-red-contrib-web-worldmap` palette and custom
   FreeTAKTeam nodes. More moving parts; nothing prebuilt.
2. A **compiled AMD64 Linux binary** (`FTH-webmap-linux-0.2.5`) — a self-contained
   bundle of Node-RED 1.3.4 + the worldmap palette + the TAK-map flow, baked into a
   single executable. You just run it with a config file.

**We use the compiled binary.** It is the path that actually runs in Docker with no
Node-RED install, no palette management, and no internet at runtime. There is **no
official Docker image** for the webmap, so `Dockerfile` here wraps the upstream
binary in a thin `debian:bookworm-slim` container (it downloads the release zip at
build time rather than vendoring a 94 MB blob into the repo).

### How it connects to FTS (the data path)

```
  our app (backend :8088)                FreeTAKServer                 FreeTAKHub-WebMap
  POST /fixes/<id>/send  ──CoT XML──▶  freetakserver:8087  ──forwards──▶  webmap (TCP client)
                                          (CoT broker)        live CoT      │
                                                                            ▼
                                                          parses CoT, draws marker on
                                                          Leaflet worldmap at /tak-map
                                                                            │  SockJS push
                                                                            ▼
                                                                   your browser tab
```

The webmap opens a **TCP client** connection *out* to FTS's CoT port (`8087`) and
receives the live CoT stream; it pushes each marker to the browser over **SockJS**
(`/tak-map/socket`). It is purely a *consumer* of the same CoT our app already
sends — nothing about the app, the contracts, or `rfmesh-cot` changes.

### Config — `webMAP_config.json`

The binary takes one CLI argument: the path to its config (mounted read-only at
`/config/webMAP_config.json`). Our values:

```json
{
  "BOT_TOKEN": "unused",
  "FTH_FTS_URL": "freetakserver",   ← FTS hostname on the compose `both3` network
  "ChatId": "unused",
  "FTH_FTS_API_Auth": "token",      ← FTS SystemUser 'admin' token (literal "token")
  "FTH_FTS_API_Port": 19023,        ← FTS REST API
  "FTH_FTS_TCP_Port": 8087          ← FTS CoT TCP — the live marker stream
}
```

> **Note on the websocket key.** Our FTS is configured with
> `FTS_WEBSOCKET_KEY="YourWebsocketKey"`. The **compiled webmap v0.2.5 does not use a
> websocket key** — it consumes CoT over the **TCP CoT port (8087)**, authenticating
> to the REST API with `FTH_FTS_API_Auth` (the SystemUser token, `"token"`). The
> websocket key is an FTS-internal/UI concept; it is not a field the webmap binary
> reads. Markers still flow correctly over 8087, which is what we verified below.

### Port choice

Published on host **8081** (container `8000`). Deliberately **not** `8000`, `5678`,
`8088`, `5055`, `8087`, or `19023` (all taken). Host `8001` is reserved as the
FTS-UI iframe target (`FTSMAP_HOST_PORT`), so the standalone webmap uses `8081`
(verified free with `netstat` at bring-up). Override with `WEBMAP_HOST_PORT` if needed.

---

## Bring it up

From the **workspace root**:

```bash
BACKEND_HOST_PORT=8088 FTSUI_HOST_PORT=5055 \
docker compose -f deployment/docker-compose.yml \
               -f deployment/docker-compose.dev.yml \
               -f deployment/docker-compose.webmap.yml up -d
```

(Or just the webmap, if the rest of the stack is already up: append `webmap` to the
end of the command, and add `--build` the first time.)

Confirm it is up and connected to FTS:

```bash
docker ps --filter name=deployment-webmap-1            # Up, 0.0.0.0:8081->8000/tcp
docker logs deployment-webmap-1 | grep "TAK Map"       # "[worldmap:TAK Map] started at /tak-map"
docker logs deployment-freetakserver-1 | grep "connected client"   # "number of connected client: 2"
```

The benign `[DataIn] TypeError: Cannot read property 'length' of undefined` line in
the webmap log is a harmless quirk of the bundled flow's debug node echoing the
config path — it does **not** mean the FTS connection failed. (The FTS log proves
the webmap connected: it appears as client `node-red` and gets CoT "sent to client
node-red".)

---

## ⭐ WHERE THE SENT SIGNAL APPEARS — step by step

The operator's #1 confusion: *"I sent a marker, where do I look?"* Here it is.

1. **Open the map and KEEP THE TAB OPEN.** Browse to
   **http://localhost:8081/tak-map** . You will see a world Leaflet map. The map is a
   **live view** fed over a socket — if you close the tab, you stop receiving. There
   is **no history**: opening the tab shows only markers whose CoT is *currently*
   live; it does **not** replay anything sent earlier.

2. **Get a fix id to send.** In another terminal:
   ```bash
   curl -s localhost:8088/fixes | grep -o '"fix_id":"[^"]*"'
   ```
   The seeded demo gives three, e.g. `aaaaaaaa-0000-4000-8000-000000000001`.

3. **Send the marker:**
   ```bash
   curl -X POST localhost:8088/fixes/aaaaaaaa-0000-4000-8000-000000000001/send
   # -> {"sent":true,"detail":"encoded and queued to tcp://freetakserver:8087"}
   ```

4. **Look at the map — within ~30 seconds.** A marker appears at the fix position
   (the demo fixes are in Belgium, around **lat 50.355, lon 5.000**). If the map is
   zoomed elsewhere, **zoom/pan to that area** (or use the map's search/coords tool).
   The marker is a **red hostile diamond** (CoT type `a-h-G-E-X-N`, milsymbol
   `shGpEXN`). **Click it** to see the popup:
   ```
   Callsign: rfmesh   UID: rfmesh.fix.aaaaaaaa-...0001
   Type: a-h-G-E-X-N  SIDC: shGpEXN--------
   method=stansfield+mle GDOP=1.600 ... confidence=medium
   contributing_nodes=node-l1-north,node-l1-south
   ```
   That popup text is exactly the `<remarks>` our `rfmesh-cot` publisher writes — so
   you are seeing the real fix, GDOP and all.

5. **⏱️ The marker fades after ~30 s — RE-SEND to keep it visible.** Each CoT we
   send carries a **30-second stale window** (`rfmesh-cot` stamps it; the backend
   restamps `t_unix_ns` to "now" on each send). After ~30 s the marker goes stale and
   the map drops it. To keep one visible for a demo, **re-send within the window**, or
   loop it:
   ```bash
   while true; do curl -s -X POST localhost:8088/fixes/aaaaaaaa-0000-4000-8000-000000000001/send >/dev/null; sleep 10; done
   ```
   (10 s < 30 s, so the marker never goes stale.)

### The three caveats, restated (this is what trips people up)

- **Keep the browser tab open** — it is a live websocket/SockJS view, not a snapshot.
- **No history / no persistence** — the webmap shows only markers with *currently
  live* CoT. A marker you sent before you opened the tab will **not** be there. Send
  again with the tab open.
- **Re-send within 30 s** — markers are stale 30 s after a send. If you walk up to a
  map that's been idle, it will be empty even though sends "succeeded" earlier; fire a
  fresh `…/send` (or the loop above) and watch it pop in.

---

## Verification (evidence this actually renders a marker)

Done end-to-end on 2026-05-21 against the running stack:

1. **FTS forwarded our CoT to the webmap.** `docker logs deployment-freetakserver-1`
   after a send showed `number of connected client: 2`, the client dict listing
   `'node-red'` at the webmap container IP (`172.22.0.5:…` → FTS `:8087`), and the
   full hostile-emitter CoT XML `… sent to client node-red`.

2. **The marker reached the browser-render channel.** Subscribing to the webmap's
   SockJS feed (`http://localhost:8081/tak-map/socket`, the exact channel the Leaflet
   page uses) while re-sending the marker captured the worldmap "add marker" payload:
   ```
   "name":"rfmesh"
   "SIDC":"shGpEXN--------"
   "layer":"a-h-G-E-X-N"
   "tooltip":"Callsign: rfmesh  UID: rfmesh.fix.aaaaaaaa-...0001 ...
              method=stansfield+mle GDOP=1.600 ... confidence=medium ..."
   ```
   plus the fix `lat 50.3551000`. That is the rendered marker object the browser draws.

So: a sent marker **does render** on the webmap as a hostile ground-emitter at the
fix position, with the LOB/ellipse remarks in its popup — the faithful "what the
troops see" view, without needing a physical ATAK device.

---

## Files in this directory

- `Dockerfile` — wraps the upstream compiled webmap binary in a slim container
  (downloads `FTH-webmap-linux-0.2.5.zip` at build time, runs it with our config).
- `webMAP_config.json` — points the webmap at our FTS (`freetakserver:8087` CoT,
  `:19023` API), bind-mounted read-only into the container.
- `README.md` — this file.

The compose wiring lives one level up in `../docker-compose.webmap.yml` (an override
that adds the `webmap` service to the `both3` network). No existing compose file,
Dockerfile, or app code was modified.

---

## Tear down

```bash
docker compose -f deployment/docker-compose.yml \
               -f deployment/docker-compose.dev.yml \
               -f deployment/docker-compose.webmap.yml stop webmap
# or remove just the webmap:
docker rm -f deployment-webmap-1
```

## Alternative: a real ATAK/WinTAK client

The webmap is the no-device way to see markers. The other faithful view is a real
**ATAK/WinTAK** client connected to the CoT port: point the device at `<host>:8087`
(TCP) and the same hostile-emitter marker appears for its 30 s window. The webmap and
a real client can be connected at the same time (FTS fans out to all CoT clients).
