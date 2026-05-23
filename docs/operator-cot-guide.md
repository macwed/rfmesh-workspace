# Operator CoT messaging — guide

How an operator puts information onto the shared TAK map: pick a message
template, choose a point or area, send it, and it appears on every connected
ATAK / WinTAK / iTAK client in real time.

This is the **operator → TAK** direction, complementing the existing
**machine → TAK** path (fusion `FixEvent` → hostile-emitter marker).
Design rationale and the keep-contracts-frozen decision live in
[`docs/adr/ADR-018-operator-authored-cot-messaging.md`](adr/ADR-018-operator-authored-cot-messaging.md).

---

## 1. The big idea

The mesh already pushes **machine** markers to TAK automatically:

```
nodes → bearings → fusion → FixEvent → red hostile-emitter dot in ATAK
```

That is sensor output. A real C2 picture also needs the **human** to put
information *onto* the map: confirm a contact, draw a no-go area, mark a
waypoint, annotate a point with a note. That is what this adds — same CoT
wire format, same TAK server, so every connected tablet sees it instantly.

Operator markers carry `how="h-g-i-g-o"` (human-placed), distinct from the
`how="m-r"` on a machine fix — so a consumer can always tell operator intent
from a DF-derived fix. They do not fake a sensor accuracy figure.

---

## 2. How it ties into the project (data flow)

```
   AUTO (already existed)              OPERATOR (this feature)
   nodes → fusion → FixEvent           operator picks template + point/area
              │                                   │
              ▼                                   ▼
        fix_event_to_cot_xml            operator_marker_to_cot_xml
              └──────────► PyTAKCotPublisher ◄────┘
                                 │  TCP 8087
                                 ▼
                   TAK server (tak.frederikwouters.be)
                                 │  relays to every client
                                 ▼
                   ATAK (Android) / WinTAK (Windows) / iTAK (iOS)
```

One publisher, one server, one map. The contracts (`rfmesh-contracts`) were
**not** touched: `publish_marker` / `delete_marker` are concrete methods on
`PyTAKCotPublisher`, not on the frozen `CotPublisher` Protocol — so no schema
bump and no B1 event.

The "thread-safe located places" piece is `OperatorMarkerStore`: a
`threading.Lock`-guarded registry of live markers keyed by stable `uid`, so an
operator-input thread (CLI, a map-click handler) and the publisher's asyncio
TX loop never race.

---

## 3. Where to find it (code map)

All inside `packages/rfmesh-cot/` (Workstream C+D):

| File | What it is |
| --- | --- |
| `src/rfmesh_cot/operator.py` | `OperatorMarker`, `MessageTemplate`, the `TEMPLATES` registry, pure encode (`operator_marker_to_cot_xml`, `operator_delete_to_cot_xml`). No network. |
| `src/rfmesh_cot/store.py` | `OperatorMarkerStore` — thread-safe live-marker registry. |
| `src/rfmesh_cot/publisher.py` | `PyTAKCotPublisher.publish_marker` / `delete_marker` (+ thread-safe enqueue). |
| `src/rfmesh_cot/cli/send.py` | `rfmesh-cot-send` console script — the operator entry point and the backend a map UI calls into. |
| `tests/test_operator_*.py` | Encode, thread-safe store, loopback publish. |
| `README.md` | Per-package quick start + template table. |

Branch `ws-cd/operator-cot-messaging`, worktree `.claude/worktrees/operator-cot`.

---

## 4. Use it now (CLI — tested live)

```bash
cd .claude/worktrees/operator-cot

# hostile contact (point)
uv run rfmesh-cot-send --template hostile --lat 50.066 --lon 4.866 \
    --callsign "Jammer A" --remarks "ELRS uplink, operator-confirmed"

# no-go area (polygon) from a file of 'lat,lon' lines, one per line
uv run rfmesh-cot-send --template no_go --area area.txt --callsign "No-go N"

# move a marker: re-send the SAME --uid with a new position
# remove a marker:
uv run rfmesh-cot-send --delete rfmesh.op.hostile.jammer-a
```

Endpoint defaults to `tcp://35.206.145.140:8087`; override with `--cot-url`.

### Templates

| key | CoT type | geometry | meaning |
| --- | --- | --- | --- |
| `hostile` | `a-h-G` | point | red hostile contact |
| `friendly` | `a-f-G` | point | blue friendly |
| `neutral` | `a-n-G` | point | green neutral |
| `unknown` | `a-u-G` | point | yellow unknown |
| `waypoint` | `b-m-p-w` | point | navigation waypoint |
| `spi` | `b-m-p-s-p-i` | point | sensor point of interest |
| `casevac` | `b-r-f-h-c` | point | CASEVAC request |
| `no_go` | `u-d-f` | polygon | red no-go area |
| `area_of_interest` | `u-d-f` | polygon | amber area of interest |
| `search_area` | `u-d-f` | polygon | blue search area |

Add a template = one line in `TEMPLATES`; the CLI and any UI pick it up
automatically (they read the registry).

---

## 5. See the markers — install + connect a TAK client

You are sending blind until a client is connected to the same server.

| Client | Platform | Where to get it |
| --- | --- | --- |
| **WinTAK** | Windows 10/11 | TAK.gov (free account) — easiest on this laptop |
| **ATAK-CIV** | Android | Google Play ("ATAK-CIV") or TAK.gov APK |
| **iTAK** | iPhone / iPad | Apple App Store |

Then add a server connection in the client:

| Setting | Value |
| --- | --- |
| Host | `tak.frederikwouters.be` (or `35.206.145.140`) |
| Port | `8087` |
| Protocol | `TCP` |

> The admin web panel on port `5000` (login `fre`) is **only** for creating
> system users. Pushing or viewing CoT on port `8087` needs no credentials —
> skip the admin panel entirely for this workflow.

Once connected, run an `rfmesh-cot-send` and the marker appears on the map
immediately. Tap a marker to read its remarks. (Burn server for the
hackathon — fine to drop test markers.)

---

## 6. The UI side — where it is and where it's going

- **Today:** the operator surface is the **`rfmesh-cot-send` CLI**. It is the
  proven backend; everything below just calls `publish_marker`.

- **Ops dashboard** lives in `packages/rfmesh-ops/` (matplotlib figure, panels
  for bearings / fix / GDOP / etc., launched with `rfmesh-ops --connect ...`).
  It is currently **read-only** — it displays the live picture, it does not yet
  let the operator place markers.

- **Planned map picker (not built yet — needs a decision):** a click-to-place
  surface that produces `(template, point | polygon vertices, callsign,
  remarks)` and calls `pub.publish_marker(...)`. Two candidate homes:
  1. **A new panel in `rfmesh-ops`** (matplotlib `button_press_event`): click =
     point, multi-click + close = polygon, template via a small menu. Zero new
     stack, stays in C+D.
  2. **A web map** (Leaflet / MapLibre, like the `deployment/` both3 frontend):
     nicer basemap + UX, but a new surface + a small backend that calls the
     publisher.

  When the picker is built, it binds to `OperatorMarkerStore` to list / edit /
  delete what the operator has placed.

- **Re-broadcast loop (planned):** markers fade at their stale time; a periodic
  task that replays `store.all()` before stale time (and on TAK reconnect)
  keeps them live. The store already supports this; the task is not yet wired.

---

## 7. Programmatic use (what the UI will call)

```python
from rfmesh_cot import OperatorMarker, OperatorMarkerStore, PyTAKCotPublisher

store = OperatorMarkerStore()

async with PyTAKCotPublisher("tcp://35.206.145.140:8087") as pub:
    marker = OperatorMarker(
        template_key="hostile",
        uid="rfmesh.op.hostile.jammer-a",   # stable: re-send to move, delete to remove
        lat_deg=50.066, lon_deg=4.866,
        callsign="Jammer A",
        remarks="operator-confirmed",
    )
    store.put(marker)
    pub.publish_marker(marker)              # safe to call from a non-asyncio thread
```
