package com.ydkmusic.app;

import android.content.Intent;
import android.webkit.CookieManager;

import androidx.annotation.Nullable;
import androidx.media3.common.AudioAttributes;
import androidx.media3.common.C;
import androidx.media3.exoplayer.ExoPlayer;
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory;
import androidx.media3.datasource.DefaultDataSource;
import androidx.media3.datasource.okhttp.OkHttpDataSource;
import androidx.media3.session.MediaSession;
import androidx.media3.session.MediaSessionService;
import okhttp3.Cookie;
import okhttp3.CookieJar;
import okhttp3.HttpUrl;
import okhttp3.OkHttpClient;
import java.util.ArrayList;
import java.util.List;

/**
 * Owns the single native player used for Android background playback.
 * The Capacitor bridge hands playback to this service when the app is backgrounded.
 */
public final class PlaybackService extends MediaSessionService {
    private ExoPlayer player;
    private MediaSession mediaSession;

    @Override
    public void onCreate() {
        super.onCreate();

        AudioAttributes audioAttributes = new AudioAttributes.Builder()
                .setUsage(C.USAGE_MEDIA)
                .setContentType(C.AUDIO_CONTENT_TYPE_MUSIC)
                .build();

        // Read the WebView's current session for every request. OkHttp asks
        // again on redirects, keeping app cookies off third-party media hosts.
        OkHttpClient client = new OkHttpClient.Builder().cookieJar(new CookieJar() {
            @Override public List<Cookie> loadForRequest(HttpUrl url) {
                List<Cookie> result = new ArrayList<>();
                if (!"ydkmusic.onrender.com".equals(url.host()) || !url.isHttps()) return result;
                String saved = CookieManager.getInstance().getCookie(url.toString());
                if (saved != null) {
                    for (String value : saved.split(";")) {
                        Cookie cookie = Cookie.parse(url, value.trim());
                        if (cookie != null) result.add(cookie);
                    }
                }
                return result;
            }
            @Override public void saveFromResponse(HttpUrl url, List<Cookie> cookies) {}
        }).build();
        DefaultDataSource.Factory sources = new DefaultDataSource.Factory(this, new OkHttpDataSource.Factory(client));
        player = new ExoPlayer.Builder(this)
                .setMediaSourceFactory(new DefaultMediaSourceFactory(sources))
                .setAudioAttributes(audioAttributes, true)
                .setHandleAudioBecomingNoisy(true)
                .build();
        mediaSession = new MediaSession.Builder(this, player).build();
    }

    @Override
    public MediaSession onGetSession(MediaSession.ControllerInfo controllerInfo) {
        return mediaSession;
    }

    @Override
    public void onTaskRemoved(@Nullable Intent rootIntent) {
        if (player == null || !player.isPlaying()) {
            stopSelf();
        }
    }

    @Override
    public void onDestroy() {
        if (mediaSession != null) {
            mediaSession.release();
            mediaSession = null;
        }
        if (player != null) {
            player.release();
            player = null;
        }
        super.onDestroy();
    }
}
