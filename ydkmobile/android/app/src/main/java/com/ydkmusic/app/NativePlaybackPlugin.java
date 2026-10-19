package com.ydkmusic.app;

import android.content.ComponentName;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.media3.common.MediaItem;
import androidx.media3.common.MediaMetadata;
import androidx.media3.common.Player;
import androidx.media3.session.MediaController;
import androidx.media3.session.SessionToken;

import com.getcapacitor.JSObject;
import com.getcapacitor.JSArray;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.google.common.util.concurrent.FutureCallback;
import com.google.common.util.concurrent.Futures;
import com.google.common.util.concurrent.ListenableFuture;
import com.google.common.util.concurrent.MoreExecutors;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

@CapacitorPlugin(name = "NativePlayback")
public final class NativePlaybackPlugin extends Plugin {
    private MediaController controller;
    private ListenableFuture<MediaController> controllerFuture;

    private interface ControllerAction {
        void run(MediaController mediaController);
    }

    private void withController(final PluginCall call, final ControllerAction action) {
        if (controller != null) {
            action.run(controller);
            return;
        }

        if (controllerFuture == null) {
            SessionToken token = new SessionToken(
                    getContext(),
                    new ComponentName(getContext(), PlaybackService.class)
            );
            controllerFuture = new MediaController.Builder(getContext(), token).buildAsync();
            Futures.addCallback(controllerFuture, new FutureCallback<MediaController>() {
                @Override
                public void onSuccess(@Nullable MediaController result) {
                    controller = result;
                    if (controller == null) {
                        call.reject("Could not connect to native playback service.");
                        return;
                    }
                    controller.addListener(new Player.Listener() {
                        @Override
                        public void onIsPlayingChanged(boolean isPlaying) {
                            notifyPlaybackState();
                        }

                        @Override
                        public void onMediaItemTransition(@Nullable MediaItem mediaItem, int reason) {
                            notifyPlaybackState();
                        }

                        @Override
                        public void onPlaybackStateChanged(int playbackState) {
                            notifyPlaybackState();
                        }
                    });
                    action.run(controller);
                }

                @Override
                public void onFailure(@NonNull Throwable throwable) {
                    controllerFuture = null;
                    call.reject("Could not connect to native playback service.", throwable.getMessage());
                }
            }, MoreExecutors.directExecutor());
        } else {
            Futures.addCallback(controllerFuture, new FutureCallback<MediaController>() {
                @Override
                public void onSuccess(@Nullable MediaController result) {
                    if (result == null) call.reject("Native playback is unavailable.");
                    else action.run(result);
                }

                @Override
                public void onFailure(@NonNull Throwable throwable) {
                    call.reject("Native playback is unavailable.", throwable.getMessage());
                }
            }, MoreExecutors.directExecutor());
        }
    }

    @PluginMethod
    public void play(PluginCall call) {
        String url = call.getString("url");
        if (url == null || url.trim().isEmpty()) {
            call.reject("A playable audio URL is required.");
            return;
        }

        withController(call, mediaController -> {
            JSArray queue = call.getArray("queue");
            if (queue != null && queue.length() > 0) {
                List<MediaItem> items = mediaItemsFromArray(queue);
                if (!items.isEmpty()) {
                    int queueIndex = Math.max(0, Math.min(call.getInt("queueIndex", 0), items.size() - 1));
                    mediaController.setMediaItems(items, queueIndex, 0);
                } else {
                    mediaController.setMediaItem(mediaItemFromValues(
                            url,
                            call.getString("title", "Untitled song"),
                            call.getString("artist", "Unknown artist"),
                            call.getString("album", "YDKMusic"),
                            call.getString("artworkUrl"),
                            ""
                    ));
                }
            } else {
                mediaController.setMediaItem(mediaItemFromValues(
                        url,
                        call.getString("title", "Untitled song"),
                        call.getString("artist", "Unknown artist"),
                        call.getString("album", "YDKMusic"),
                        call.getString("artworkUrl"),
                        ""
                ));
            }
            mediaController.prepare();
            Long positionMs = call.getLong("positionMs");
            if (positionMs != null && positionMs > 0) mediaController.seekTo(positionMs);
            mediaController.play();
            call.resolve();
        });
    }

    @PluginMethod
    public void setRepeatMode(PluginCall call) {
        int repeatMode = call.getInt("repeatMode", Player.REPEAT_MODE_OFF);
        withController(call, mediaController -> {
            mediaController.setRepeatMode(repeatMode);
            call.resolve();
        });
    }

    @PluginMethod
    public void setShuffle(PluginCall call) {
        boolean enabled = call.getBoolean("enabled", false);
        withController(call, mediaController -> {
            mediaController.setShuffleModeEnabled(enabled);
            call.resolve();
        });
    }

    @PluginMethod
    public void pause(PluginCall call) {
        withController(call, mediaController -> {
            mediaController.pause();
            call.resolve();
        });
    }

    @PluginMethod
    public void resume(PluginCall call) {
        withController(call, mediaController -> {
            mediaController.play();
            call.resolve();
        });
    }

    @PluginMethod
    public void seek(PluginCall call) {
        Long positionMs = call.getLong("positionMs");
        if (positionMs == null || positionMs < 0) {
            call.reject("A non-negative positionMs value is required.");
            return;
        }
        withController(call, mediaController -> {
            mediaController.seekTo(positionMs);
            call.resolve();
        });
    }

    @PluginMethod
    public void stop(PluginCall call) {
        withController(call, mediaController -> {
            mediaController.stop();
            call.resolve();
        });
    }

    @PluginMethod
    public void getState(PluginCall call) {
        withController(call, mediaController -> {
            JSObject payload = new JSObject();
            MediaItem item = mediaController.getCurrentMediaItem();
            payload.put("isPlaying", mediaController.isPlaying());
            payload.put("positionMs", mediaController.getCurrentPosition());
            payload.put("durationMs", mediaController.getDuration());
            payload.put("mediaId", item == null ? "" : item.mediaId);
            call.resolve(payload);
        });
    }

    @Override
    protected void handleOnDestroy() {
        if (controller != null) {
            controller.release();
            controller = null;
        }
        controllerFuture = null;
        super.handleOnDestroy();
    }

    private void notifyPlaybackState() {
        if (controller == null) return;
        JSObject payload = new JSObject();
        payload.put("isPlaying", controller.isPlaying());
        payload.put("positionMs", controller.getCurrentPosition());
        payload.put("durationMs", controller.getDuration());
        payload.put("mediaId", controller.getCurrentMediaItem() == null
                ? ""
                : controller.getCurrentMediaItem().mediaId);
        payload.put("title", controller.getCurrentMediaItem() == null
                ? ""
                : controller.getCurrentMediaItem().mediaMetadata.title);
        notifyListeners("playbackState", payload);
    }

    @Nullable
    private android.net.Uri toUri(@Nullable String value) {
        return value == null || value.trim().isEmpty() ? null : android.net.Uri.parse(value);
    }

    private MediaItem mediaItemFromValues(
            String url,
            String title,
            String artist,
            String album,
            @Nullable String artworkUrl,
            String mediaId
    ) {
        MediaMetadata metadata = new MediaMetadata.Builder()
                .setTitle(title)
                .setArtist(artist)
                .setAlbumTitle(album)
                .setArtworkUri(toUri(artworkUrl))
                .build();
        return new MediaItem.Builder()
                .setMediaId(mediaId == null ? "" : mediaId)
                .setUri(url)
                .setMediaMetadata(metadata)
                .build();
    }

    private List<MediaItem> mediaItemsFromArray(JSArray queue) {
        List<MediaItem> items = new ArrayList<>();
        for (int index = 0; index < queue.length(); index++) {
            try {
                JSONObject item = queue.getJSONObject(index);
                String url = item.optString("url", "");
                if (url.isEmpty()) continue;
                items.add(mediaItemFromValues(
                        url,
                        item.optString("title", "Untitled song"),
                        item.optString("artist", "Unknown artist"),
                        item.optString("album", "YDKMusic"),
                        item.optString("artworkUrl", ""),
                        item.optString("id", "")
                ));
            } catch (JSONException ignored) {
                // Ignore malformed queue entries; the web queue remains authoritative.
            }
        }
        return items;
    }
}
