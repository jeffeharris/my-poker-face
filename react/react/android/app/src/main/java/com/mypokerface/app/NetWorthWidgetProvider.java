package com.mypokerface.app;

import android.app.PendingIntent;
import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Path;
import android.widget.RemoteViews;

import org.json.JSONArray;
import org.json.JSONObject;

import java.text.NumberFormat;
import java.util.Locale;

/**
 * Net Worth home-screen widget — the Android counterpart to the iOS
 * NetWorthWidget (react/react/ios/App/NetWorthWidget). Renders the same snapshot
 * the web app publishes via the WidgetBridge plugin: net-worth headline, a
 * green/red trend sparkline, the player's status, and renown/regard.
 *
 * The snapshot JSON is written to SharedPreferences by {@link WidgetBridgePlugin}
 * (same app package, so no shared container is needed). We re-render on the normal
 * APPWIDGET_UPDATE cadence and whenever the bridge calls {@link #requestUpdate}.
 */
public class NetWorthWidgetProvider extends AppWidgetProvider {

    private static final int UP_GREEN = Color.parseColor("#22c55e");
    private static final int DOWN_RED = Color.parseColor("#ef4444");
    // Off-screen sparkline render size (px); the ImageView scales it to fit.
    private static final int SPARK_W = 480;
    private static final int SPARK_H = 96;

    /** Ask the framework to re-run onUpdate for every placed instance of this widget. */
    static void requestUpdate(Context context) {
        AppWidgetManager mgr = AppWidgetManager.getInstance(context);
        int[] ids = mgr.getAppWidgetIds(new android.content.ComponentName(context, NetWorthWidgetProvider.class));
        if (ids != null && ids.length > 0) {
            Intent intent = new Intent(context, NetWorthWidgetProvider.class);
            intent.setAction(AppWidgetManager.ACTION_APPWIDGET_UPDATE);
            intent.putExtra(AppWidgetManager.EXTRA_APPWIDGET_IDS, ids);
            context.sendBroadcast(intent);
        }
    }

    @Override
    public void onUpdate(Context context, AppWidgetManager mgr, int[] appWidgetIds) {
        for (int id : appWidgetIds) {
            mgr.updateAppWidget(id, buildViews(context));
        }
    }

    private RemoteViews buildViews(Context context) {
        RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_net_worth);

        // Tapping the widget opens the app.
        Intent launch = context.getPackageManager().getLaunchIntentForPackage(context.getPackageName());
        if (launch != null) {
            PendingIntent pi = PendingIntent.getActivity(
                context, 0, launch,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
            views.setOnClickPendingIntent(R.id.widget_root, pi);
        }

        WidgetSnapshot snap = loadSnapshot(context);
        if (snap == null) {
            // Empty state — mirrors the iOS "Open the app to sync your stats." card.
            views.setViewVisibility(R.id.widget_data, android.view.View.GONE);
            views.setViewVisibility(R.id.widget_empty, android.view.View.VISIBLE);
            return views;
        }

        views.setViewVisibility(R.id.widget_empty, android.view.View.GONE);
        views.setViewVisibility(R.id.widget_data, android.view.View.VISIBLE);

        views.setTextViewText(R.id.widget_net_worth, currency(snap.netWorth));
        views.setTextViewText(R.id.widget_status, snap.status.isEmpty() ? "Unranked" : snap.status);
        views.setTextViewText(R.id.widget_renown, "★ " + Math.round(snap.renown * 100));
        views.setTextViewText(R.id.widget_regard, "♥ " + regardText(snap.regard));

        if (snap.series.length >= 2) {
            views.setImageViewBitmap(R.id.widget_sparkline, drawSparkline(snap.series));
            views.setViewVisibility(R.id.widget_sparkline, android.view.View.VISIBLE);
        } else {
            views.setViewVisibility(R.id.widget_sparkline, android.view.View.INVISIBLE);
        }
        return views;
    }

    /** Minimal line sparkline; green when up over the window, red when down (matches iOS). */
    private Bitmap drawSparkline(double[] values) {
        Bitmap bmp = Bitmap.createBitmap(SPARK_W, SPARK_H, Bitmap.Config.ARGB_8888);
        Canvas canvas = new Canvas(bmp);

        double min = values[0], max = values[0];
        for (double v : values) {
            if (v < min) min = v;
            if (v > max) max = v;
        }
        double range = Math.max(max - min, 1);

        Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
        paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeWidth(6f);
        paint.setStrokeCap(Paint.Cap.ROUND);
        paint.setStrokeJoin(Paint.Join.ROUND);
        paint.setColor(values[values.length - 1] >= values[0] ? UP_GREEN : DOWN_RED);

        float pad = paint.getStrokeWidth();
        float usableH = SPARK_H - 2 * pad;
        float stepX = (float) SPARK_W / (values.length - 1);

        Path path = new Path();
        for (int i = 0; i < values.length; i++) {
            float x = i * stepX;
            float y = pad + (float) (usableH * (1 - (values[i] - min) / range));
            if (i == 0) path.moveTo(x, y);
            else path.lineTo(x, y);
        }
        canvas.drawPath(path, paint);
        return bmp;
    }

    private static String currency(double v) {
        NumberFormat f = NumberFormat.getCurrencyInstance(Locale.US);
        f.setMaximumFractionDigits(0);
        return f.format(v);
    }

    private static String regardText(double r) {
        int pct = (int) Math.round(r * 100);
        return pct > 0 ? "+" + pct : String.valueOf(pct);
    }

    private WidgetSnapshot loadSnapshot(Context context) {
        SharedPreferences prefs = context.getSharedPreferences(
            WidgetBridgePlugin.PREFS_NAME, Context.MODE_PRIVATE);
        String raw = prefs.getString(WidgetBridgePlugin.SNAPSHOT_KEY, null);
        if (raw == null) return null;
        try {
            JSONObject o = new JSONObject(raw);
            JSONArray arr = o.optJSONArray("series");
            double[] series = new double[arr == null ? 0 : arr.length()];
            for (int i = 0; i < series.length; i++) series[i] = arr.optDouble(i, 0);
            WidgetSnapshot s = new WidgetSnapshot();
            s.netWorth = o.optDouble("netWorth", 0);
            s.series = series;
            s.renown = o.optDouble("renown", 0);
            s.regard = o.optDouble("regard", 0);
            s.status = o.optString("status", "");
            return s;
        } catch (Exception e) {
            return null;
        }
    }

    /** Mirrors the JSON written by src/utils/widgetData.ts → WidgetBridge.publish. */
    private static class WidgetSnapshot {
        double netWorth;
        double[] series;
        double renown;
        double regard;
        String status = "";
    }
}
