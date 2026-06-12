package com.mypokerface.app;

import android.content.Context;
import android.content.SharedPreferences;

import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

/**
 * Android side of the web app's home-screen widget bridge — the counterpart to
 * iOS's WidgetBridgePlugin.swift. The JS side
 * (src/utils/widgetData.ts → registerPlugin('WidgetBridge').publish({ payload }))
 * sends the same JSON snapshot on both platforms.
 *
 * Unlike iOS (where the widget is a separate process and needs an App Group
 * container), an Android app-widget runs in this same app package, so it can read
 * our own SharedPreferences directly — no shared container needed. We write the
 * payload, then poke AppWidgetManager so {@link NetWorthWidgetProvider} re-renders.
 *
 * Registered in MainActivity.onCreate (Capacitor 6 only auto-registers plugins
 * that ship as npm packages; app-local ones must be registered explicitly).
 */
@CapacitorPlugin(name = "WidgetBridge")
public class WidgetBridgePlugin extends Plugin {

    /** Shared with NetWorthWidgetProvider — both must agree on these names. */
    static final String PREFS_NAME = "net_worth_widget";
    static final String SNAPSHOT_KEY = "widgetSnapshot";

    @PluginMethod
    public void publish(PluginCall call) {
        String payload = call.getString("payload");
        if (payload == null) {
            call.reject("payload is required");
            return;
        }

        Context ctx = getContext().getApplicationContext();
        SharedPreferences prefs = ctx.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
        prefs.edit().putString(SNAPSHOT_KEY, payload).apply();

        NetWorthWidgetProvider.requestUpdate(ctx);
        call.resolve();
    }
}
