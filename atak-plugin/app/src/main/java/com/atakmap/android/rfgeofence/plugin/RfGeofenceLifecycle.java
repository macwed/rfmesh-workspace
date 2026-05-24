package com.atakmap.android.rfgeofence.plugin;

import android.content.Context;

import com.atak.plugins.impl.AbstractPluginLifecycle;
import com.atakmap.android.rfgeofence.RfGeofenceMapComponent;

/**
 * Plugin entry point. ATAK instantiates this (declared in assets/plugin.xml as a
 * {@code transapps.maps.plugin.lifecycle.Lifecycle}) and it brings up the
 * {@link RfGeofenceMapComponent}.
 */
public class RfGeofenceLifecycle extends AbstractPluginLifecycle {
    public RfGeofenceLifecycle(Context ctx) {
        super(ctx, new RfGeofenceMapComponent());
    }
}
