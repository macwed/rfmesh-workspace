# RF Geofence ATAK plugin — runbook

How the plugin was built, where it lives, how it's served, and the **signature
gotcha** that decides whether it loads. Written 2026-05-24 for ATAK-CIV 5.6.0.

## What it is / how it works (pull, not push)
A native ATAK plugin that draws rfmesh RF-plausibility outlines as on-device
geofences, bypassing FreeTAKServer (which strips drawing shapes when relaying).

- It is **pull-based**: the operator opens the plugin on the tablet, it **GETs**
  data from the rfmesh backend, and it **draws** the geofence locally with ATAK's
  `GeoFence` API. The backend never pushes to the tablet.
- It is **per-device**: the geofence is drawn on the one tablet that pulled it. It
  does NOT propagate to other tablets (no server relay). Each tablet runs the
  plugin to get its own copy.
- Endpoints used: `GET /fixes`, `GET /fixes/{id}/posterior/contour?cutoff=`.

## Where things live
- **Plugin source (canonical build):** `ATAK-CIV-5.6.0-SDK/samples/rfgeofence/`
  — cloned from the SDK's `samples/PluginTemplateLegacy`, with our logic injected
  (`RfmeshApiClient.java`, `GeofenceBuilder.java`, rewritten
  `PluginTemplateDropDownReceiver.java`, `res/layout/main_layout.xml`).
  The hand-written `atak-plugin/` tree (AGP 4.2.2, legacy transapps API) is
  **superseded** by this — keep the SDK-samples build.
- **Built APK:** `ATAK-CIV-5.6.0-SDK/samples/rfgeofence/app/build/outputs/apk/civ/debug/ATAK-Plugin-rfgeofence-1.0--5.6.0-civ-debug.apk`
- **Hosted at:** `deployment/frontend/downloads/rfgeofence.apk`
  → `GET /downloads/rfgeofence.apk` (MIME `application/vnd.android.package-archive`)
  → public via Cloudflare tunnel: `https://both3.huplo.cc/downloads/rfgeofence.apk`

## Build environment (what worked)
- **ATAK-CIV 5.6.0 SDK** unzipped at `F:\code\belgia\ATAK-CIV-5.6.0-SDK\`
  (`main.jar`, `android_keystore`, `atak-gradle-takdev.jar` at root; `atak.apk` =
  the matching ATAK build).
- **JDK 21** (Android Studio JBR): `C:/Program Files/Android/Android Studio/jbr`
- **Gradle 8.14.3** (wrapper), **AGP 8.13**, compileSdk 36, Java 17 target, takdev 3.+.
- **Android SDK:** `C:/Users/b/AppData/Local/Android/Sdk`

## Reproduce the build
```bash
# from the SDK samples dir so takdev finds main.jar/keystore offline via ../../
cd ATAK-CIV-5.6.0-SDK/samples/rfgeofence
# local.properties: only `sdk.dir=C:/Users/b/AppData/Local/Android/Sdk` (no takrepo.*)
JAVA_HOME="/c/Program Files/Android/Android Studio/jbr" sh gradlew assembleCivDebug --no-daemon
# -> app/build/outputs/apk/civ/debug/ATAK-Plugin-rfgeofence-*.apk
cp app/build/outputs/apk/civ/debug/ATAK-Plugin-rfgeofence-*.apk \
   ../../../rfmesh-workspace/deployment/frontend/downloads/rfgeofence.apk
```
Signing is automatic: takdev stages the SDK's `android_keystore` into `buildDir`
(key alias `wintec_mapping`, store/key password `tnttnt`).

## ⚠ THE SIGNATURE GOTCHA (why "signature invalid" happens)
ATAK loads a plugin **only if the plugin is signed with the same key as the ATAK
app on the device.**

- Our plugin is signed with **`O=WinTec Arrowmaker`** (the SDK key,
  SHA256 `CC:B0:99:4D:EF:1E:51:28:77:A0:2B:F8:4E:F1:09:9E:BA:E3:D1:56:DD:78:E6:38:B7:7F:5C:31:C9:AC:72:34`).
- The SDK's `atak.apk` is signed with the **same** key → plugin loads.
- The **Play-Store ATAK-CIV** is signed with TAK's release key → **different** →
  `signature invalid`. A self-built plugin **cannot** load into the Play-Store
  ATAK (only TAK can sign plugins for it).

### Fix: run the SDK-signed ATAK that matches the plugin (USB sideload)
```bash
adb uninstall com.atakmap.app.civ          # remove the mismatched ATAK first
adb install F:\code\belgia\ATAK-CIV-5.6.0-SDK\atak.apk   # SDK ATAK (WinTec Arrowmaker)
adb install F:\code\belgia\rfmesh-workspace\deployment\frontend\downloads\rfgeofence.apk
```
Then ATAK → Settings → Manage Plugins → enable **RF Geofence**.

> Do NOT publicly host `atak.apk` (it's the controlled ATAK-CIV binary). Sideload
> it over USB. The plugin APK is fine to host.

### Verify a signature
```bash
keytool -printcert -jarfile <file>.apk | grep -E "Owner|SHA256"
# plugin and atak.apk must show the SAME SHA256.
```

## Use it on the tablet
1. Tablet runs the **SDK ATAK-CIV 5.6.0** (matching key), plugin enabled.
2. Open the RF Geofence tool → Backend URL = `https://both3.huplo.cc` (works over
   internet via the tunnel; no LAN needed).
3. Load fixes → pick one → cutoff `10` → Create geofence → red `Jx` zone draws +
   ATAK alerts on entry. All on-device; FreeTAKServer not involved.

## Notes / limits
- Built for **5.6.0 civ**; tablet ATAK must match version + flavor.
- Per-device, pull-only (see top). For team-wide shapes you'd need a TAK Server
  that preserves drawing-shape detail (FreeTAKServer does not).
- Honesty (ADR-016/017): red `Jx`, remarks say "cue, not a target; no dBm".
