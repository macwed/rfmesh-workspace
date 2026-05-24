package com.atakmap.android.rfgeofence;

import com.atakmap.android.drawing.DrawingToolsMapComponent;
import com.atakmap.android.drawing.mapItems.DrawingShape;
import com.atakmap.android.geofence.component.GeoFenceComponent;
import com.atakmap.android.geofence.data.GeoFence;
import com.atakmap.android.maps.MapGroup;
import com.atakmap.android.maps.MapView;
import com.atakmap.coremap.maps.coords.GeoPoint;
import com.atakmap.coremap.maps.coords.GeoPointMetaData;
import com.atakmap.coremap.log.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

/**
 * Converts an rfmesh RF-plausibility contour (GeoJSON Polygon/MultiPolygon,
 * [lon,lat]) into native ATAK drawing shapes, and attaches an entry-monitored
 * {@link GeoFence} to each — so this device renders + alerts locally, with no
 * dependency on a TAK server relaying drawing-shape detail.
 *
 * Honesty (ADR-016/017): red styling, "Jx" title, and remarks that say the
 * outline is a cue (not a confirmed location) and that power is not modelled.
 *
 * All map mutation must run on the UI thread — call {@link #build} inside
 * {@code mapView.post(...)}.
 */
public final class GeofenceBuilder {

    private static final String TAG = "RfGeofence";
    private static final int STROKE = 0xFFFF0000;   // opaque red
    private static final int FILL = 0x40FF0000;     // ~25% red
    private static final int MIN_RING_VERTS = 3;

    private static final String CAVEAT =
            "RF-plausibility geofence — cue, not a confirmed location: "
                    + "terrain-diffraction likelihood of the emitter ground. "
                    + "Confirm PID + a 2nd sensor before acting. "
                    + "Power not measured (no dBm).";

    private GeofenceBuilder() {
    }

    /**
     * Build + register geofence(s) from a contour FeatureCollection.
     *
     * @return number of geofence shapes created (a MultiPolygon yields several)
     */
    public static int build(MapView mapView, JSONObject contourFc, String label) {
        JSONArray feats = contourFc.optJSONArray("features");
        if (feats == null || feats.length() == 0) {
            return 0;
        }
        MapGroup group = DrawingToolsMapComponent.getGroup();
        int made = 0;
        for (int i = 0; i < feats.length(); i++) {
            JSONObject ft = feats.optJSONObject(i);
            if (ft == null) {
                continue;
            }
            JSONObject geom = ft.optJSONObject("geometry");
            if (geom == null) {
                continue;
            }
            for (GeoPoint[] ring : outerRings(geom)) {
                DrawingShape s = shapeFromRing(mapView, ring, label, made);
                if (s == null) {
                    continue;
                }
                group.addItem(s);
                try {
                    s.persist(mapView.getMapEventDispatcher(), null, GeofenceBuilder.class);
                } catch (Exception e) {
                    Log.w(TAG, "persist failed for " + s.getUID(), e);
                }
                attachGeoFence(s, ring);
                made++;
            }
        }
        return made;
    }

    private static DrawingShape shapeFromRing(MapView mapView, GeoPoint[] pts,
            String label, int idx) {
        if (pts == null || pts.length < MIN_RING_VERTS) {
            return null;
        }
        String uid = "rfgeofence." + label + "." + idx + "." + UUID.randomUUID();
        DrawingShape s = new DrawingShape(mapView, uid);
        s.setPoints(GeoPointMetaData.wrap(pts));
        s.setClosed(true);
        s.setStrokeColor(STROKE);
        s.setStrokeWeight(3.0d);
        s.setFillColor(FILL);
        s.setTitle(label);
        s.setMetaString("remarks", CAVEAT);
        return s;
    }

    private static void attachGeoFence(DrawingShape s, GeoPoint[] pts) {
        int rangeKm = Math.max(1, (int) Math.ceil(boundingRadiusM(pts) / 1000.0));
        // The closed-shape monitor uses the polygon geometry itself for the
        // point-in-polygon test; rangeKm is only a nominal bounding value.
        GeoFence fence = new GeoFence(s, true, GeoFence.Trigger.Entry,
                GeoFence.MonitoredTypes.All, rangeKm);
        GeoFenceComponent.getInstance().dispatch(fence, s);
    }

    // ---- GeoJSON parsing ([lon,lat] -> GeoPoint(lat,lon)) --------------------

    private static List<GeoPoint[]> outerRings(JSONObject geom) {
        List<GeoPoint[]> rings = new ArrayList<>();
        String type = geom.optString("type", "");
        JSONArray coords = geom.optJSONArray("coordinates");
        if (coords == null) {
            return rings;
        }
        try {
            if ("Polygon".equals(type)) {
                GeoPoint[] r = ringToPoints(coords.optJSONArray(0));
                if (r != null) {
                    rings.add(r);
                }
            } else if ("MultiPolygon".equals(type)) {
                for (int i = 0; i < coords.length(); i++) {
                    JSONArray poly = coords.optJSONArray(i);
                    if (poly == null || poly.length() == 0) {
                        continue;
                    }
                    GeoPoint[] r = ringToPoints(poly.optJSONArray(0));
                    if (r != null) {
                        rings.add(r);
                    }
                }
            } else {
                Log.w(TAG, "unsupported geometry type: " + type);
            }
        } catch (Exception e) {
            Log.w(TAG, "ring parse failed", e);
        }
        return rings;
    }

    private static GeoPoint[] ringToPoints(JSONArray ring) {
        if (ring == null) {
            return null;
        }
        List<GeoPoint> pts = new ArrayList<>();
        for (int i = 0; i < ring.length(); i++) {
            JSONArray p = ring.optJSONArray(i);
            if (p == null || p.length() < 2) {
                continue;
            }
            double lon = p.optDouble(0, Double.NaN);
            double lat = p.optDouble(1, Double.NaN);
            if (Double.isNaN(lat) || Double.isNaN(lon)) {
                continue;
            }
            pts.add(new GeoPoint(lat, lon));
        }
        // drop the trailing closing vertex; DrawingShape.setClosed re-closes it
        int n = pts.size();
        if (n >= 2) {
            GeoPoint a = pts.get(0);
            GeoPoint b = pts.get(n - 1);
            if (a.getLatitude() == b.getLatitude() && a.getLongitude() == b.getLongitude()) {
                pts.remove(n - 1);
            }
        }
        return pts.size() >= MIN_RING_VERTS ? pts.toArray(new GeoPoint[0]) : null;
    }

    private static double boundingRadiusM(GeoPoint[] pts) {
        double clat = 0, clon = 0;
        for (GeoPoint p : pts) {
            clat += p.getLatitude();
            clon += p.getLongitude();
        }
        clat /= pts.length;
        clon /= pts.length;
        double mLat = 111320.0;
        double mLon = 111320.0 * Math.cos(Math.toRadians(clat));
        double far = 0;
        for (GeoPoint p : pts) {
            double dn = (p.getLatitude() - clat) * mLat;
            double de = (p.getLongitude() - clon) * mLon;
            far = Math.max(far, Math.hypot(dn, de));
        }
        return far;
    }
}
