# rfmesh-operator-console

Click-the-map operator console for TAK. Open it in a browser, pick a message
template, click the map to drop a point (or draw an area), add an optional
note, and the marker ships to the TAK server and appears on every connected
ATAK / WinTAK / iTAK client.

This is the UI on top of `rfmesh-cot` (ADR-018). It is a composition root
(`apps/*`): it imports `rfmesh_cot` to wire the UI to the publisher.

## Run

```bash
rfmesh-operator-console                       # http://127.0.0.1:8088, default TAK endpoint
rfmesh-operator-console --port 9000 --cot-url tcp://35.206.145.140:8087
```

Then open the printed URL.

## How to use

1. (Optional) type a **Callsign** (the on-map label) and a **Note**.
2. Pick a template tab:
   - **Point markers:** hostile, friendly, neutral, unknown, waypoint, spi,
     casevac → **click the map** to drop one.
   - **Areas:** no_go, area_of_interest, search_area → **click vertices** on the
     map, then **Finish area** (needs ≥ 3 points; Cancel to restart).
3. Placed markers appear in the side panel; **delete** un-sends them.

## Why the backend holds one TAK connection open

FreeTAKServer relays a sender's CoT to other connected clients only while the
sender behaves like a connected client. So the server keeps a single
`PyTAKCotPublisher` open, sends a **self-SA** presence event on connect and
every 20 s, and re-broadcasts live markers every 30 s (so a tablet that joins
later still receives them). A one-shot `connect → write → close` does **not**
get relayed — that was the bug behind "I don't see it on ATAK".

## API (what the page calls)

| method | path | body |
| --- | --- | --- |
| GET  | `/api/templates` | — |
| GET  | `/api/markers`   | — |
| POST | `/api/marker`    | `{template, lat, lon, callsign?, remarks?}` or `{template, vertices:[[lat,lon],…], callsign?, remarks?}` |
| POST | `/api/delete`    | `{uid}` |
