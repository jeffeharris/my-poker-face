package com.mypokerface.app;

import android.os.Bundle;

import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        // App-local plugins must be registered before super.onCreate (Capacitor 6
        // auto-registers only npm-package plugins). iOS does the equivalent in
        // WidgetBridgePlugin.swift's capacitorDidLoad().
        registerPlugin(WidgetBridgePlugin.class);
        super.onCreate(savedInstanceState);
    }
}
