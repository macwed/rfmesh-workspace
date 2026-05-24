# rfgeofence — ATAK-CIV plugin

Draws rfmesh **RF-plausibility** outlines as **native ATAK geofences** on one
device. It fetches the level-set contour from the rfmesh backend over HTTP and
creates a closed `DrawingShape` + an entry-monitored `GeoFence` directly in ATAK
— bypassing FreeTAKServer, which strips drawing-shape detail when relaying.

- **Why:** a control test proved FTS relays point markers but drops `u-d-f`
  drawing shapes, so a geofence sent through the server never renders. Doing it
  on-device with the ATAK SDK is the robust path.
- **Honesty (ADR-016/017):** red `Jx` shape; remarks say it is a *cue, not a
  confirmed location*, and that power is not modelled (no dBm).

## Flow

```
ATAK tool "RF Geofence"
  -> Backend URL + Load fixes      GET  {base}/fixes
  -> pick fix + cutoff %           GET  {base}/fixes/{id}/posterior/contour?cutoff=
  -> Create geofence (Jx)          DrawingShape + GeoFence (Entry, All) on the map
```

No backend change: it reuses endpoints the rfmesh deployment already serves.

## Layout

- `app/src/main/assets/plugin.xml` — declares the Lifecycle + Tool extensions.
- `app/src/main/java/com/atakmap/android/rfgeofence/`
  - `plugin/RfGeofenceLifecycle.java`, `plugin/RfGeofenceTool.java` — entry points.
  - `RfGeofenceMapComponent.java` — registers the drop-down.
  - `RfGeofenceDropDownReceiver.java` — the minimal UI.
  - `RfmeshApiClient.java` — HTTP + GeoJSON (platform-only deps).
  - `GeofenceBuilder.java` — GeoJSON contour → `DrawingShape` + `GeoFence`.

Build + install: see [BUILDING.md](BUILDING.md). Not buildable in the rfmesh
Python dev env — needs the tak.gov ATAK-CIV 5.x SDK, Android tooling, and a device.
