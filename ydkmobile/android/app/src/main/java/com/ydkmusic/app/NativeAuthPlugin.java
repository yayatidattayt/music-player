package com.ydkmusic.app;

import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.util.Base64;
import android.webkit.CookieManager;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

import org.json.JSONObject;

import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;

/** System-browser OAuth with a verifier-bound, one-time return to this app. */
@CapacitorPlugin(name = "NativeAuth")
public final class NativeAuthPlugin extends Plugin {
    private static final String API = "https://ydkmusic.onrender.com";
    private final AtomicBoolean exchanging = new AtomicBoolean(false);

    private SharedPreferences preferences() {
        return getContext().getSharedPreferences("pending_google_login", 0);
    }

    private String encode(byte[] value) {
        return Base64.encodeToString(value, Base64.URL_SAFE | Base64.NO_PADDING | Base64.NO_WRAP);
    }

    @Override
    public void load() {
        getActivity().runOnUiThread(() -> {
            CookieManager cookies = CookieManager.getInstance();
            cookies.setAcceptCookie(true);
            cookies.setAcceptThirdPartyCookies(getBridge().getWebView(), true);
            receive(getActivity().getIntent());
        });
    }

    @PluginMethod
    public void signIn(PluginCall call) {
        try {
            byte[] random = new byte[32];
            new SecureRandom().nextBytes(random);
            String verifier = encode(random);
            String challenge = encode(MessageDigest.getInstance("SHA-256")
                    .digest(verifier.getBytes(StandardCharsets.US_ASCII)));
            preferences().edit().putString("verifier", verifier)
                    .putLong("started", System.currentTimeMillis()).commit();
            Uri uri = Uri.parse(API + "/api/auth/google/sign-in").buildUpon()
                    .appendQueryParameter("mobile_challenge", challenge).build();
            getActivity().startActivity(new Intent(Intent.ACTION_VIEW, uri));
            call.resolve();
        } catch (Exception error) {
            call.reject("Could not open Google sign-in. Check that a browser is installed.");
        }
    }

    @Override
    protected void handleOnNewIntent(Intent intent) {
        receive(intent);
    }

    private void receive(Intent intent) {
        Uri uri = intent == null ? null : intent.getData();
        if (uri == null || !"ydkmusic".equals(uri.getScheme()) || !"auth".equals(uri.getHost())) return;
        String code = uri.getQueryParameter("code");
        String verifier = preferences().getString("verifier", null);
        long started = preferences().getLong("started", 0);
        if (code == null || !code.matches("[A-Za-z0-9_-]{43}") || verifier == null) return;
        if (System.currentTimeMillis() - started > 15 * 60 * 1000) {
            preferences().edit().clear().apply();
            report(false, "Google sign-in expired. Please try again.");
            return;
        }
        if (!exchanging.compareAndSet(false, true)) return;
        // Preserve pending data on network failures so reopening the return
        // link can retry. Clear it only after a successful exchange.
        execute(() -> {
            HttpURLConnection connection = null;
            try {
                connection = (HttpURLConnection) new URL(API + "/api/auth/google/mobile/complete").openConnection();
                connection.setRequestMethod("POST");
                connection.setInstanceFollowRedirects(false);
                connection.setConnectTimeout(20000);
                connection.setReadTimeout(30000);
                connection.setDoOutput(true);
                connection.setRequestProperty("Content-Type", "application/json");
                byte[] body = new JSONObject().put("code", code).put("verifier", verifier)
                        .toString().getBytes(StandardCharsets.UTF_8);
                try (java.io.OutputStream output = connection.getOutputStream()) {
                    output.write(body);
                }
                if (connection.getResponseCode() != 200) {
                    throw new IllegalStateException("Login could not finish. Please sign in again.");
                }
                String sessionCookie = null;
                for (Map.Entry<String, List<String>> header : connection.getHeaderFields().entrySet()) {
                    if ("Set-Cookie".equalsIgnoreCase(header.getKey())) {
                        for (String cookie : header.getValue()) {
                            if (cookie.startsWith("ydkmusic_session=")) sessionCookie = cookie;
                        }
                    }
                }
                if (sessionCookie == null) throw new IllegalStateException("No app session was returned.");
                final String cookie = sessionCookie;
                getActivity().runOnUiThread(() -> CookieManager.getInstance().setCookie(API, cookie, accepted -> {
                    CookieManager.getInstance().flush();
                    exchanging.set(false);
                    if (accepted) {
                        preferences().edit().clear().apply();
                        report(true, "");
                    } else {
                        report(false, "Could not save your login. Please sign in again.");
                    }
                }));
            } catch (Exception error) {
                exchanging.set(false);
                report(false, "Could not finish login. Check your connection and try signing in again.");
            } finally {
                if (connection != null) connection.disconnect();
            }
        });
    }

    private void report(boolean success, String message) {
        JSObject result = new JSObject();
        result.put("success", success);
        result.put("message", message);
        notifyListeners("loginComplete", result, true);
    }
}
