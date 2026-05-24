# Put the built plugin APK here

Drop the signed plugin APK in **this folder**, named exactly:

    rfgeofence.apk

Then it is downloadable at:

    http://<backend-host>:8088/downloads/rfgeofence.apk

and linked from the ops home page (index.html → "Install ATAK plugin").

## How the APK gets here
1. Build it from `atak-plugin/` on an Android machine (see `atak-plugin/BUILDING.md`).
   Output: `atak-plugin/app/build/outputs/apk/civ/debug/ATAK-Plugin-rfgeofence-*.apk`.
2. Copy + rename it into this folder as `rfgeofence.apk`.
   - Local dev: `deployment/frontend/` is bind-mounted into the backend container,
     so the file is served immediately — no rebuild/restart needed.
   - Prod image: rebuild the image (the frontend is baked in) or mount this folder.

## Install on the tablet
1. On the tablet browser, open `http://<backend-host>:8088/downloads/rfgeofence.apk`.
2. Allow "install from unknown sources" if prompted; install.
3. ATAK → Settings → Tool Preferences → Manage Plugins → enable **RF Geofence**.

The APK itself is git-ignored (build artifact, and signed material shouldn't be committed).
