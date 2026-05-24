package com.atakmap.android.rfgeofence;

import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.preference.PreferenceManager;
import android.view.View;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.Toast;

import com.atak.plugins.impl.PluginLayoutInflater;
import com.atakmap.android.dropdown.DropDown;
import com.atakmap.android.dropdown.DropDownReceiver;
import com.atakmap.android.maps.MapView;
import com.atakmap.android.rfgeofence.plugin.R;
import com.atakmap.coremap.log.Log;

import org.json.JSONObject;

import java.util.List;

/**
 * Minimal v1 UI: set the backend URL, load fixes, pick one, set a cutoff
 * (% of peak), and create a native geofence from the RF-plausibility contour.
 * Network runs off the UI thread; map mutation is marshalled back onto it.
 */
public class RfGeofenceDropDownReceiver extends DropDownReceiver
        implements DropDown.OnStateListener {

    private static final String TAG = "RfGeofence";
    private static final String PREF_URL = "rfgeofence.backend.url";
    private static final String DEFAULT_URL = "http://10.0.0.1:8088";
    private static final String DEFAULT_CUTOFF = "10";

    private final Context pluginContext;
    private final View view;
    private final SharedPreferences prefs;
    private int labelSeq = 0;

    public RfGeofenceDropDownReceiver(MapView mapView, Context pluginContext) {
        super(mapView);
        this.pluginContext = pluginContext;
        this.prefs = PreferenceManager.getDefaultSharedPreferences(mapView.getContext());
        this.view = PluginLayoutInflater.inflate(pluginContext, R.layout.main_layout, null);
        wire();
    }

    private void wire() {
        final EditText url = view.findViewById(R.id.rf_url);
        final EditText cutoff = view.findViewById(R.id.rf_cutoff);
        final Spinner fixes = view.findViewById(R.id.rf_fixes);
        final Button load = view.findViewById(R.id.rf_load);
        final Button create = view.findViewById(R.id.rf_create);
        final TextView status = view.findViewById(R.id.rf_status);

        url.setText(prefs.getString(PREF_URL, DEFAULT_URL));
        cutoff.setText(DEFAULT_CUTOFF);

        load.setOnClickListener(v -> {
            final String base = url.getText().toString().trim();
            prefs.edit().putString(PREF_URL, base).apply();
            setStatus(status, "loading fixes…");
            new Thread(() -> {
                try {
                    final List<RfmeshApiClient.Fix> list =
                            new RfmeshApiClient(base).listFixes();
                    post(() -> {
                        ArrayAdapter<RfmeshApiClient.Fix> a = new ArrayAdapter<>(
                                getMapView().getContext(),
                                android.R.layout.simple_spinner_item, list);
                        a.setDropDownViewResource(
                                android.R.layout.simple_spinner_dropdown_item);
                        fixes.setAdapter(a);
                        setStatus(status, list.size() + " fix(es) loaded");
                    });
                } catch (Exception e) {
                    Log.w(TAG, "listFixes failed", e);
                    post(() -> {
                        setStatus(status, "load failed: " + e.getMessage());
                        toast("Load failed: " + e.getMessage());
                    });
                }
            }).start();
        });

        create.setOnClickListener(v -> {
            final Object sel = fixes.getSelectedItem();
            if (!(sel instanceof RfmeshApiClient.Fix)) {
                toast("Load and pick a fix first");
                return;
            }
            final RfmeshApiClient.Fix fix = (RfmeshApiClient.Fix) sel;
            final double cut = parseCutoff(cutoff.getText().toString()) / 100.0;
            final String base = url.getText().toString().trim();
            final String label = "J" + (++labelSeq);
            setStatus(status, "computing " + label + " @ ≥"
                    + Math.round(cut * 100) + "% of peak…");
            new Thread(() -> {
                try {
                    final JSONObject fc =
                            new RfmeshApiClient(base).getContour(fix.id, cut, null);
                    post(() -> {
                        int n = GeofenceBuilder.build(getMapView(), fc, label);
                        if (n > 0) {
                            setStatus(status, label + " created (" + n + " shape(s))");
                            toast("Geofence " + label + " drawn (" + n + " shape(s))");
                        } else {
                            labelSeq--; // reuse the label next time
                            setStatus(status, "no area at this cutoff — lower it");
                            toast("No area at this cutoff");
                        }
                    });
                } catch (Exception e) {
                    Log.w(TAG, "getContour failed", e);
                    post(() -> {
                        labelSeq--;
                        setStatus(status, "failed: " + e.getMessage());
                        toast("Geofence failed: " + e.getMessage());
                    });
                }
            }).start();
        });
    }

    private static int parseCutoff(String s) {
        try {
            int v = Integer.parseInt(s.trim());
            return Math.max(1, Math.min(100, v));
        } catch (NumberFormatException e) {
            return 10;
        }
    }

    private void setStatus(final TextView tv, final String msg) {
        if (tv != null) {
            tv.setText(msg);
        }
    }

    private void post(Runnable r) {
        getMapView().post(r);
    }

    private void toast(final String msg) {
        Toast.makeText(getMapView().getContext(), msg, Toast.LENGTH_SHORT).show();
    }

    @Override
    public void onReceive(Context context, Intent intent) {
        if (com.atakmap.android.rfgeofence.plugin.RfGeofenceTool.SHOW
                .equals(intent.getAction())) {
            showDropDown(view, HALF_WIDTH, FULL_HEIGHT, FULL_WIDTH, HALF_HEIGHT, this);
        }
    }

    @Override
    protected void disposeImpl() {
    }

    // ---- DropDown.OnStateListener (no-ops for v1) ----
    @Override
    public void onDropDownSelectionRemoved() {
    }

    @Override
    public void onDropDownClose() {
    }

    @Override
    public void onDropDownSizeChanged(double width, double height) {
    }

    @Override
    public void onDropDownVisible(boolean v) {
    }
}
