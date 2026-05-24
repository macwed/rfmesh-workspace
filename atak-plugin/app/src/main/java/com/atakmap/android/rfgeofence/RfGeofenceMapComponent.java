package com.atakmap.android.rfgeofence;

import android.content.Context;
import android.content.Intent;

import com.atakmap.android.dropdown.DropDownMapComponent;
import com.atakmap.android.ipc.AtakBroadcast;
import com.atakmap.android.maps.MapView;
import com.atakmap.android.rfgeofence.plugin.RfGeofenceTool;

/**
 * Wires the plugin into ATAK: registers the drop-down receiver that turns an
 * rfmesh RF-plausibility contour into a native ATAK geofence on this device.
 */
public class RfGeofenceMapComponent extends DropDownMapComponent {

    private RfGeofenceDropDownReceiver ddr;

    @Override
    public void onCreate(Context pluginContext, Intent intent, MapView mapView) {
        super.onCreate(pluginContext, intent, mapView);
        ddr = new RfGeofenceDropDownReceiver(mapView, pluginContext);
        AtakBroadcast.DocumentedIntentFilter f =
                new AtakBroadcast.DocumentedIntentFilter();
        f.addAction(RfGeofenceTool.SHOW);
        // DropDownMapComponent auto-unregisters this on destroy.
        registerDropDownReceiver(ddr, f);
    }

    @Override
    protected void onDestroyImpl(Context context, MapView mapView) {
        super.onDestroyImpl(context, mapView);
    }
}
