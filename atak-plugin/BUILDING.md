# Building the RF Geofence ATAK plugin

This plugin runs on an ATAK-CIV tablet, pulls an RF-plausibility contour from the
rfmesh backend over HTTP, and draws it as a **native ATAK geofence** (a closed
`DrawingShape` + a `GeoFence` monitored on entry). It bypasses FreeTAKServer,
which drops drawing-shape detail when relaying — so the geofence renders on the
device regardless of the server.

> This repo holds the **plugin source**. The APK is built, signed, and installed
> by you on an Android machine — it cannot be built in the rfmesh Python/Windows
> dev environment (no ATAK SDK, Android SDK/NDK, keystore, or device there).

## Prerequisites

1. **ATAK-CIV 5.x SDK** from <https://tak.gov> (account required). You need the
   `atak-gradle-takdev` plugin + the ATAK API artifact (`main.jar` / `pluginsdk`)
   for your exact ATAK version. The SDK download includes a sample plugin
   ("plugintemplate"/"helloworld") already wired for that ATAK version.
2. **JDK** matching the SDK (JDK 11 for recent 5.x SDKs; check the SDK README).
3. **Android SDK** + build-tools, and **Android NDK** if your SDK requires it.
4. A **signing keystore**. The SDK ships a debug keystore (`andropenstreetmap`/
   `android` style); ATAK loads only signed plugins.
5. A tablet running **ATAK-CIV 5.x** — the plugin's `civ` flavor and `ATAK_VERSION`
   must match the installed app, or ATAK silently refuses to load the plugin.

## Version reconciliation (important)

`build.gradle` / `app/build.gradle` here mirror the repo's 4.6 `helloworld`
(AGP 4.2.2, `compileSdkVersion 34`, takdev `2.+`). The **5.x SDK may need newer
AGP/Gradle/compileSdk**. Two options:

- **Recommended:** copy `app/src/main/` (Java + `assets/plugin.xml` + `res/` +
  `AndroidManifest.xml`) into the **SDK's bundled plugin template** (which already
  has the correct AGP/takdev/compileSdk for your ATAK version), set its package to
  `com.atakmap.android.rfgeofence.plugin`, and build there. The Java is the
  deliverable; the template provides a known-good build harness.
- **Or** keep this project and bump `com.android.tools.build:gradle`, the Gradle
  wrapper, and `compileSdkVersion` to what the 5.x SDK README specifies.

## Configure

1. Set your ATAK version in `app/build.gradle`:
   ```gradle
   ext.ATAK_VERSION = "5.4.0.0"   // <-- your tablet's ATAK-CIV version (Settings > About)
   ```
2. Create `local.properties` from `local.properties.example` and fill in:
   - `sdk.dir` — Android SDK path.
   - Either `takdev.plugin` (path to `atak-gradle-takdev.jar` from the SDK) **or**
     `takrepo.url` / `takrepo.user` / `takrepo.password` for the Maven dev kit.
   - `takDebugKeyFile` / `...Password` / `...Alias` and the release equivalents.

## Build + install

```bash
cd atak-plugin
./gradlew assembleCivDebug          # -> app/build/outputs/apk/civ/debug/*.apk
./gradlew installCivDebug           # build + adb install to a connected device
# or: adb install -r app/build/outputs/apk/civ/debug/ATAK-Plugin-rfgeofence-*.apk
```

## Run on the tablet

1. ATAK → **Settings → Tool Preferences → Manage Plugins** (or the Plugins menu) →
   enable **RF Geofence**. (You may need to re-launch ATAK.)
2. Open the **RF Geofence** tool from the toolbar.
3. Set **Backend URL** to your reachable backend, e.g. `http://<backend-ip>:8088`
   (the tablet must be on the same network; backend CORS is already `*`).
4. **Load fixes** → pick a fix → set **Cutoff** (e.g. `10` = ≥10% of peak) →
   **Create geofence (Jx)**.
5. A red **J1** polygon draws on the map and ATAK monitors it — move a friendly
   marker inside to fire the geofence entry alert. The shape's remarks carry the
   "cue, not a target / no dBm" caveat.

## Verify against the SDK (the few API touch-points)

These are confirmed against the public ATAK source but pin to the SDK you
download — adjust if a signature differs in your 5.x SDK:

- `com.atakmap.android.drawing.mapItems.DrawingShape(MapView, uid)`,
  `setPoints(GeoPointMetaData[])`, `setClosed(true)`, `setStrokeColor(int)`,
  `setStrokeWeight(double)`, `setFillColor(int)`, `setTitle(String)`.
- `com.atakmap.android.drawing.DrawingToolsMapComponent.getGroup()`.
- `com.atakmap.android.geofence.data.GeoFence(MapItem, boolean tracking,
  GeoFence.Trigger, GeoFence.MonitoredTypes, int rangeKm)`.
- `com.atakmap.android.geofence.component.GeoFenceComponent.getInstance()
  .dispatch(GeoFence, MapItem)`.

## Out of scope (v1)

Live cutoff slider + on-map preview (the web UI has it), backend changes (none),
auth/TLS to the backend, multi-fix batch.
