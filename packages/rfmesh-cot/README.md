# rfmesh-cot

CoT/ATAK adapter for rfmesh. Owned by Workstream C+D. Two directions:

1. **Machine → TAK** (existing): the fusion server turns a `FixEvent` /
   `NodeStatus` into a hostile-emitter / friendly-node marker
   (`fix_event_to_cot_xml`, `node_status_to_cot_xml`).
2. **Operator → TAK** (ADR-018): the operator drops a marker by picking a
   **template** and a **point or area**, and it appears on every connected
   ATAK / WinTAK / iTAK client (`operator.py`, `store.py`,
   `PyTAKCotPublisher.publish_marker` / `delete_marker`).

Both render the same CoT wire format and ship over the same
`PyTAKCotPublisher` (TCP/UDP/TLS via PyTAK).

There is also a **third transport** for operator point updates: the
FreeTAKServer **REST API** (`FreeTakServerRestClient`), ported from the
`drupal/atak` module. Instead of holding a CoT socket open, it makes one
authenticated HTTP call and lets FTS fan the marker out to every client —
no self-SA keepalive, no persistent connection. It models **points only**
(no polygons, no CoT delete); use the `PyTAKCotPublisher` path for those.
See "REST API path" below.

## Operator messaging — quick start

The endpoint defaults to the BoTH3 hackathon TAK server
(`tcp://35.206.145.140:8087`). Override with `--cot-url`.

```bash
# Drop a hostile contact (point)
rfmesh-cot-send --template hostile --lat 50.066 --lon 4.866 \
    --callsign "Jammer A" --remarks "ELRS uplink, operator-confirmed"

# Draw a no-go area (polygon) from a vertex file (one 'lat,lon' per line)
rfmesh-cot-send --template no_go --area area.txt --callsign "No-go N"

# Un-send a marker you placed earlier
rfmesh-cot-send --delete rfmesh.op.hostile.jammer-a
```

### Templates

| key                | CoT type        | geometry | meaning                     |
| ------------------ | --------------- | -------- | --------------------------- |
| `hostile`          | `a-h-G`         | point    | red hostile contact         |
| `friendly`         | `a-f-G`         | point    | blue friendly               |
| `neutral`          | `a-n-G`         | point    | green neutral               |
| `unknown`          | `a-u-G`         | point    | yellow unknown              |
| `waypoint`         | `b-m-p-w`       | point    | navigation waypoint         |
| `spi`              | `b-m-p-s-p-i`   | point    | sensor point of interest    |
| `casevac`          | `b-r-f-h-c`     | point    | CASEVAC request             |
| `no_go`            | `u-d-f`         | polygon  | red no-go area              |
| `area_of_interest` | `u-d-f`         | polygon  | amber area of interest      |
| `search_area`      | `u-d-f`         | polygon  | blue search area            |

### Programmatic use (the path a map UI calls into)

```python
from rfmesh_cot import OperatorMarker, OperatorMarkerStore, PyTAKCotPublisher

store = OperatorMarkerStore()                 # thread-safe live-marker registry

async with PyTAKCotPublisher("tcp://35.206.145.140:8087") as pub:
    marker = OperatorMarker(
        template_key="hostile",
        uid="rfmesh.op.hostile.jammer-a",     # stable: re-send to move, delete to remove
        lat_deg=50.066, lon_deg=4.866,
        callsign="Jammer A",
        remarks="operator-confirmed",
    )
    store.put(marker)
    pub.publish_marker(marker)                 # thread-safe; callable off-loop
```

`publish_marker` / `delete_marker` are safe to call from a non-asyncio
thread (a map-click handler, a CLI thread): the encode runs on the caller's
thread and the queue hand-off is marshalled onto the publisher's loop.

## REST API path (`FreeTakServerRestClient`)

A Python port of the `drupal/atak` module: push point markers to
FreeTAKServer over its REST API with a `Authorization: Bearer <token>`
header. The token is a FreeTAKServer **System-User token** (FTS Web UI →
User → give a user token); the `Bearer` prefix is not part of the token.
The default REST port is `19023` (distinct from the CoT streaming port
`8087`).

```bash
# Connectivity + auth smoke test (prints API version + endpoints)
rfmesh-cot-rest --base-url http://tak.example.com:19023 --token "$FTS_API_TOKEN" --help-api

# Drop a hostile contact (point templates only)
rfmesh-cot-rest --base-url http://tak.example.com:19023 --token "$FTS_API_TOKEN" \
    --template hostile --lat 50.066 --lon 4.866 --callsign "Jammer A"

# Move it: re-send the same uid
rfmesh-cot-rest --base-url http://tak.example.com:19023 --token "$FTS_API_TOKEN" \
    --template hostile --lat 50.07 --lon 4.87 --uid rfmesh.op.hostile.jammer-a

# REST has no delete; stale it now (re-PUT with a 1 s timeout)
rfmesh-cot-rest --base-url http://tak.example.com:19023 --token "$FTS_API_TOKEN" \
    --template hostile --lat 50.07 --lon 4.87 --uid rfmesh.op.hostile.jammer-a --expire
```

The token can be passed with `--token` or the `FTS_API_TOKEN` env var.

### Programmatic use

```python
from rfmesh_cot import FreeTakServerRestClient, OperatorMarker

client = FreeTakServerRestClient("http://tak.example.com:19023", api_token="...")
client.get_help()                                  # connectivity + auth check

uid = client.post_geo_object(                       # low-level: the FTS fields
    name="Jammer A", latitude=50.066, longitude=4.866, attitude="hostile",
)
client.put_geo_object(uid=uid, latitude=50.07, longitude=4.87, attitude="hostile")

# or reuse the operator vocabulary (point/affiliation templates only):
client.publish_marker(OperatorMarker(
    template_key="hostile", uid="rfmesh.op.hostile.jammer-a",
    lat_deg=50.066, lon_deg=4.866, callsign="Jammer A",
))
```

`publish_marker` raises `CotRestError` for polygon templates or any CoT
type with no FTS affiliation (e.g. `waypoint`), pointing you at the
`PyTAKCotPublisher` path rather than guessing — REST models points only.
Every HTTP failure surfaces as `CotRestError` carrying `.status_code` and
`.body` (401/403 → bad token; 500 → bad payload; `None` → unreachable).

### REST vs. streaming CoT — which to use

| | `PyTAKCotPublisher` (CoT) | `FreeTakServerRestClient` (REST) |
| --- | --- | --- |
| transport | long-lived TCP/UDP/TLS socket | one HTTP call per update |
| relay to clients | needs self-SA keepalive | FTS fans out for you |
| points | ✅ | ✅ |
| polygons / areas | ✅ | ❌ |
| delete | ✅ (`t-x-d-d`) | ❌ (stale via `timeout`) |
| high-rate `FixEvent` flow | ✅ | not intended |
| best for | machine fixes, areas, deletes | occasional operator markers |

## Viewing the markers

Install a TAK client and connect it to the same server (host
`tak.frederikwouters.be`, port `8087`, protocol TCP). Markers you send show
up on its map in real time. See the project docs for the full operator
workflow.
