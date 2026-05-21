# both3 — RF emitter ops map (PoC)

Self-contained web ops layer for `rfmesh`: an interactive Leaflet map of the
confidence ellipses, a **Send to troops** action that emits a CoT/TAK marker via
the existing `rfmesh-cot` publisher, and a crude FastAPI ingest API. Dockerized
with hot-reload; deploys to a VPS under `both3.huplo.cc` via a remote Docker
context.

Everything lives in `deployment/`. It reuses `rfmesh-cot` and `rfmesh-contracts`
read-only (path-installed into the image) and never edits them. The one file
outside `deployment/` is the workspace-root `.dockerignore` (build infra).

## Run all commands from the WORKSPACE ROOT (`rfmesh-workspace/`)

The Docker build context is the workspace root so the image can reach the two
governed packages.

### Dev (hot-reload) — full demo, everything works

```bash
cp deployment/.env.example deployment/.env   # optional; defaults are fine
docker compose -f deployment/docker-compose.yml -f deployment/docker-compose.dev.yml up --build
```

This brings up **both the backend and a bundled FreeTAKServer**, so every feature
(including **Send to troops**) works with no extra flags. FTS is heavy on first boot
(~15-20 s).

> **FreeTAKServer version (important).** The bundled FTS is the **locally-built
> 1.9.9.6** image in `deployment/fts199/`, pinned via `FTS_IMAGE=fts199:local`
> in `.env`. The dev compose overlay builds it for you (`up --build`), so no
> separate `docker build` is needed. We pin 1.9.9.6 because the upstream 2.x
> `ghcr.io/freetakteam/freetakserver:latest` (digitalpy rewrite) **drops
> marker-only CoT**: it accepts the connection (`client has connected`) but then
> logs `IAMUsersController ... a bytes-like object is required, not 'NoneType'`
> and only ever persists the first event. 1.9.9.6 ingests **every** distinct
> marker over plain TCP with no presence handshake. See the "FreeTAKServer
> 1.9.9.6" note under *Known PoC limits* for the build details (netbase +
> first-run-wizard fixes).

Open <http://localhost:8000>. The map seeds an intersecting-confidence scenario from
`deployment/backend/seed/demo_fixes.json` + `demo_bearings.json`. Edit
`deployment/frontend/*` (refresh) or `deployment/backend/src/**` (uvicorn reloads) and
changes are live.

Just the map, no FreeTAKServer (Send will 502): start only the backend service:

```bash
docker compose -f deployment/docker-compose.yml -f deployment/docker-compose.dev.yml up --build backend
```

### Feed data

```bash
# a fix (flat demo shape OR a full FixEvent JSON both work)
curl -X POST localhost:8000/fixes -H 'content-type: application/json' -d @fix.json
# bearings (full BearingReport JSON) -> draws node markers + LOBs
curl -X POST localhost:8000/bearings -H 'content-type: application/json' -d @bearings.json
curl localhost:8000/health
```

### Send to troops

In dev, FTS is already up (above). Select a fix, click **Send to troops**, then
**Confirm send** (inline; press Esc or pick another fix to defer). Verify: backend
returns 2xx and the `freetakserver` container logs show the inbound CoT
(`client has connected: rfmesh.fix.<id>`). Stop FTS and send again -> the UI shows
an honest failure toast (HTTP 502, `Name or service not known`).

CLI equivalent (any free host port; 8000 is taken on this machine — use 8088):

```bash
curl localhost:8088/fixes                            # pick a fix_id
curl -X POST localhost:8088/fixes/<fix_id>/send      # expect HTTP 200 {"sent":true}
docker logs deployment-freetakserver-1 | grep "client has connected"
```

**Confirm the marker actually persisted** (the real pass criterion — `:latest`
returned `sent:true` but silently dropped the event). The FTS 1.9.9.6 event DB is
`/opt/fts/FTSDataBase.db` (table `Event`, joined `Point`):

```bash
docker exec deployment-freetakserver-1 python3 -c \
  "import sqlite3;c=sqlite3.connect('/opt/fts/FTSDataBase.db');\
print(list(c.execute('SELECT uid,type FROM Event')))"
# each sent fix appears as ('rfmesh.fix.<id>', 'a-h-G-E-X-N')
```

### View what the troops see (FreeTAKServer-UI)

The bundled **FreeTAKServer-UI** is a browser dashboard onto the FTS instance the
backend sends CoT to. Bring up the full dev stack (FTS server + UI default-on):

```bash
BACKEND_HOST_PORT=8088 FTSUI_HOST_PORT=5055 \
  docker compose -f deployment/docker-compose.yml -f deployment/docker-compose.dev.yml up -d --build
```

Wait ~20-30 s for FTS (`docker logs deployment-freetakserver-1` shows
`CoTService started`), then:

- **URL:** <http://localhost:5055>  (host port = `FTSUI_HOST_PORT`, container `:5000`)
- **Login:** user **`admin`** / password **`password`**
  - These are the default FTS **SystemUser** credentials. The UI does not check
    its own DB for login — it forwards `admin`/`password` to the FTS REST API
    (`GET /AuthenticateUser`) using the API token, and logs you in if FTS accepts.
  - The API token the UI sends is `FTS_API_KEY="Bearer token"`. The default FTS
    SystemUser `admin` carries the literal token `token`, so `Bearer token` is
    correct out of the box. No token generation needed for `:latest`.
- The **Dashboard** (`/index`) renders the page chrome and FTS data from the REST
  API on `:19023`. With the pinned **1.9.9.6** server, login (admin/password via
  `GET /AuthenticateUser` → 200) and the core data endpoints
  (`ManageAPI/getStatus`, `getAllUsers`, presence, geo objects) all respond, so
  the UI is functional. A few live status widgets query 2.x-only endpoints
  (`.../serviceStatus/v2`) and may render blank/degraded against 1.9.9.6 — a
  cosmetic mismatch only. The faithful "what the troops see" surface is a real
  ATAK/WinTAK client on `host:8087` (below); marker ingestion + persistence is
  fully verified regardless of the UI widget gaps.

Then send a marker (above) and watch it land: FTS logs `client has connected:
rfmesh.fix.<id>`, and the marker persists in the FTS CoT DB as a hostile
ground-emitter (`a-h-G-E-X-N`) at the fix's lat/lon.

#### Freshness caveat (30 s)

`rfmesh-cot` stamps each CoT with a 30 s stale window, and the backend restamps
`t_unix_ns` to "now" on send (`SEND_FRESH=true`). A marker is therefore only
"live" for ~30 s after you send it. To keep a marker visible on a live map you
must re-send within the window (or drive a steady stream of fixes).

#### The live moving map (`/webmap`) — known limitation

The UI's **WEBMAP** tab embeds an `<iframe>` pointing at the separate
**FreeTAKHub-WebMap** component (`http://<FTS_MAP_EXPOSED_IP>:<FTS_MAP_PORT>/tak-map`).
That component is **not bundled** in `ghcr.io/freetakteam/freetakserver:latest` and
is **not published as a Docker image** — upstream ships it only as a Node-RED flow
(`freetakhub_webmap_v5.json`) that needs `node-red-contrib-web-worldmap` *plus*
custom FreeTAKTeam Node-RED nodes (`tak registration`, `tak ingest`). With no
webmap service running, the WEBMAP tab loads the page chrome but the iframe is
blank (`ERR_NAME_NOT_RESOLVED`). The compose vars `FTS_MAP_EXPOSED_IP` /
`FTS_MAP_PORT` (default host port `8001`, since `8000` is taken) are wired so that
*if* you stand up a webmap at that address the iframe resolves — but doing so is
out of scope for this PoC.

**So: a sent marker is confirmed received and persisted by FTS (logs + CoT DB),
the UI dashboard and login work end-to-end, but the in-UI moving map does not
render markers without the extra Node-RED webmap.** The faithful "what the troops
see" view is a real **ATAK/WinTAK client** connecting to the CoT port at
`host:8087` (or a future webmap container). Point a TAK device at `<host>:8087`
(TCP) and the hostile-emitter marker appears for its 30 s window.

#### Compose changes made to get the UI working

- `FTS_UI_EXPOSED_IP: 0.0.0.0` (was `127.0.0.1`). The UI's `run.py` binds its
  HTTP server to this address; `127.0.0.1` binds container loopback, so the
  published host port could never reach it (curl got HTTP 000). Must be `0.0.0.0`.
- `FTS_UI_SQLALCHEMY_DATABASE_URI` -> `sqlite:////home/freetak/FTSServer-UI.db`
  (was `.../data/FTSServer-UI.db`). The image has no `/home/freetak/data` dir, so
  SQLAlchemy `create_all()` (run on first request) failed to create the DB file.
  The new path is an existing dir on the persisted `fts-ui-db` volume.
- `FTS_MAP_EXPOSED_IP` / `FTS_MAP_PORT` made overridable (`localhost` / `8001`)
  so the webmap iframe targets a browser-reachable address and never `:8000`.

### Prod / deploy to both3.huplo.cc (remote Docker context)

```bash
cp deployment/.env.example deployment/.env.prod   # set SEED_DEMO=false, prod CORS, real COT_ENDPOINT_URL if not bundling FTS
docker context create both3-vps --docker "host=ssh://USER@VPS_HOST"
docker --context both3-vps compose \
  -f deployment/docker-compose.yml -f deployment/docker-compose.prod.yml \
  --env-file deployment/.env.prod up -d --build
```

nginx serves `:80`; Cloudflare terminates TLS in front (orange-cloud the `both3`
record -> VPS IP). Bind-mount hot-reload is local-daemon only; prod bakes code in.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Leaflet frontend |
| GET | `/health` | counts + CoT endpoint |
| POST | `/fixes` | ingest FixEvent(s) — nested or flat |
| POST | `/bearings` | ingest BearingReport(s) |
| GET | `/fixes` | GeoJSON: ellipse polygons + centre points |
| GET | `/bearings` | GeoJSON: node points + LOB rays |
| POST | `/fixes/{fix_id}/send` | publish a fix to CoT/TAK |

## Known PoC limits

- In-memory store (no DB); restart clears non-seed data.
- **Port 8000 clash:** if something else holds host `:8000`, set a different host
  port for dev, e.g. `BACKEND_HOST_PORT=8088 docker compose ... up`.
- **FreeTAKServer 1.9.9.6 (pinned) — why and how.** The bundled FTS is built
  locally from `deployment/fts199/` (Dockerfile + `start-fts.sh`) and pinned via
  `FTS_IMAGE=fts199:local`. The upstream 2.x `ghcr.io/freetakteam/freetakserver:latest`
  (digitalpy rewrite) is **broken for marker ingestion**: it accepts the TCP
  connection but drops the event with `IAMUsersController ... a bytes-like object
  is required, not 'NoneType'` and only ever persists the first marker. No
  prebuilt 1.9.x image survives on a public registry, so we build the proven
  1.9.9.6 line from PyPI on `ubuntu:20.04 + python3.8`. Two fixes were needed to
  make 1.9.9.6 run headless in a container:
  1. **`netbase`** is installed in the Dockerfile. The minimal `ubuntu:20.04`
     image omits `/etc/protocols`; FTS imports eventlet, whose greendns shim
     imports dnspython's `dns.rdtypes.IN.WKS` at load, calling
     `socket.getprotobyname('tcp')` → `OSError: protocol not found` and crashing
     before the CoT listener binds. `netbase` ships `/etc/protocols` and fixes it.
  2. **First-run wizard disabled.** On a fresh install 1.9.9.6 runs an interactive
     `input()`-based config wizard that blocks on EOF in a detached container.
     `start-fts.sh` flips `MainConfig.first_start = False` and pre-seeds a minimal
     env-driven YAML config so the server starts non-interactively with the DB on
     the persistent volume.
  Verified: distinct marker uids each land in `/opt/fts/FTSDataBase.db` (`Event`
  table), with NO IAM NoneType error, and no presence handshake required.
- MGRS works when the `mgrs` wheel + its `packaging` dep are installed (both wheeled
  in the image); otherwise the field shows `—` (graceful degrade).
- Seeded fixes carry no bearings, so node markers/LOBs appear only after you POST
  `/bearings`.
- **FTS-UI moving map is not wired** — the WEBMAP tab needs the separate Node-RED
  FreeTAKHub-WebMap (no Docker image upstream; see "View what the troops see").
  The UI dashboard + login work; a sent marker is verified received/persisted by
  FTS but only renders on a real ATAK client at `host:8087` or a future webmap.
- **UI login = FTS SystemUser** `admin` / `password` (default). If you change the
  FTS `admin` password (via the UI or the FTS CLI / `postSystemUser` API), the UI
  login changes with it. The UI->FTS API token is `FTS_API_KEY="Bearer token"`
  (the default `admin` SystemUser token is the literal string `token`); regenerate
  a SystemUser token and update `FTS_API_KEY` for a hardened setup.
