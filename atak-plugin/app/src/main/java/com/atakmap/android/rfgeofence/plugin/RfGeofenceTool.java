package com.atakmap.android.rfgeofence.plugin;

import android.content.Context;

import com.atak.plugins.impl.AbstractPluginTool;

/**
 * The toolbar button (declared in assets/plugin.xml as a
 * {@code transapps.maps.plugin.tool.ToolDescriptor}). Tapping it broadcasts the
 * SHOW intent that {@link com.atakmap.android.rfgeofence.RfGeofenceMapComponent}
 * registers, opening the drop-down.
 */
public class RfGeofenceTool extends AbstractPluginTool {

    public static final String SHOW = "com.atakmap.android.rfgeofence.SHOW_RFGEOFENCE";

    public RfGeofenceTool(final Context context) {
        super(context,
                context.getString(R.string.app_name),
                context.getString(R.string.app_name),
                context.getResources().getDrawable(R.drawable.ic_launcher),
                SHOW);
    }
}
