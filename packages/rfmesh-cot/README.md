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

## Viewing the markers

Install a TAK client and connect it to the same server (host
`tak.frederikwouters.be`, port `8087`, protocol TCP). Markers you send show
up on its map in real time. See the project docs for the full operator
workflow.
