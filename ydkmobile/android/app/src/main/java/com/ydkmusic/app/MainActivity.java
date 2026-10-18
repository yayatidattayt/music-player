package com.ydkmusic.app;

import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    public MainActivity() {
        registerPlugin(NativePlaybackPlugin.class);
    }
}
