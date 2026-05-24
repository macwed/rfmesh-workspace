package com.atakmap.android.rfgeofence;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.util.ArrayList;
import java.util.List;

/**
 * Thin blocking HTTP client for the rfmesh deployment backend. Call from a
 * background thread (never the UI thread). Uses only platform classes
 * (HttpURLConnection + org.json) so the plugin pulls in no extra dependencies.
 *
 * Endpoints (no backend change needed):
 *   GET {base}/fixes
 *   GET {base}/fixes/{id}/posterior/contour?cutoff={0..1}[&emitter_h=]
 * GeoJSON is WGS-84 [lon, lat].
 */
public class RfmeshApiClient {

    private static final int TIMEOUT_MS = 8000;

    /** A fix the operator can pick (from the /fixes "center" features). */
    public static final class Fix {
        public final String id;
        public final String label;
        public final double lat;
        public final double lon;

        Fix(String id, String label, double lat, double lon) {
            this.id = id;
            this.label = label;
            this.lat = lat;
            this.lon = lon;
        }

        @Override
        public String toString() {
            return label;
        }
    }

    private final String baseUrl;

    public RfmeshApiClient(String baseUrl) {
        // strip trailing slashes so we can append paths cleanly
        this.baseUrl = baseUrl == null ? "" : baseUrl.trim().replaceAll("/+$", "");
    }

    private String httpGet(String url) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        try {
            c.setRequestMethod("GET");
            c.setConnectTimeout(TIMEOUT_MS);
            c.setReadTimeout(TIMEOUT_MS);
            int code = c.getResponseCode();
            if (code != 200) {
                throw new Exception("HTTP " + code + " for " + url);
            }
            StringBuilder sb = new StringBuilder();
            BufferedReader r = new BufferedReader(
                    new InputStreamReader(c.getInputStream(), "UTF-8"));
            try {
                String line;
                while ((line = r.readLine()) != null) {
                    sb.append(line);
                }
            } finally {
                r.close();
            }
            return sb.toString();
        } finally {
            c.disconnect();
        }
    }

    /** GET /fixes — returns the "center" features as pickable fixes. */
    public List<Fix> listFixes() throws Exception {
        JSONObject fc = new JSONObject(httpGet(baseUrl + "/fixes"));
        JSONArray feats = fc.optJSONArray("features");
        List<Fix> out = new ArrayList<>();
        if (feats == null) {
            return out;
        }
        for (int i = 0; i < feats.length(); i++) {
            JSONObject ft = feats.optJSONObject(i);
            if (ft == null) {
                continue;
            }
            JSONObject props = ft.optJSONObject("properties");
            if (props == null || !"center".equals(props.optString("feature_kind"))) {
                continue;
            }
            String id = props.optString("fix_id", "");
            if (id.isEmpty()) {
                continue;
            }
            String ec = props.optString("emitter_class", "");
            String conf = props.optString("confidence_level", "");
            double lat = props.optDouble("lat", Double.NaN);
            double lon = props.optDouble("lon", Double.NaN);
            String shortId = id.length() >= 8 ? id.substring(0, 8) : id;
            String label = (ec.isEmpty() ? "fix" : ec)
                    + (conf.isEmpty() ? "" : " · " + conf)
                    + " · " + shortId;
            out.add(new Fix(id, label, lat, lon));
        }
        return out;
    }

    /**
     * GET /fixes/{id}/posterior/contour — the RF-plausibility level-set at the
     * given cutoff (fraction of peak, 0..1). Returns the FeatureCollection.
     */
    public JSONObject getContour(String fixId, double cutoff, Double emitterH) throws Exception {
        StringBuilder u = new StringBuilder(baseUrl);
        u.append("/fixes/").append(URLEncoder.encode(fixId, "UTF-8"))
                .append("/posterior/contour?cutoff=").append(cutoff);
        if (emitterH != null) {
            u.append("&emitter_h=").append(emitterH);
        }
        return new JSONObject(httpGet(u.toString()));
    }
}
