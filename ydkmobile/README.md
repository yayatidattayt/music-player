# YDKMusic Mobile

This is the separate Android app workspace for YDKMusic. The current web UI is
copied into `www/` and will be packaged by Capacitor without modifying the
original FastAPI project.

## Planned build flow

1. Set the mobile API base URL in `www/index.html` after the backend is deployed.
2. Install the npm dependencies.
3. Generate the Android project with Capacitor.
4. Add native Android Media3 playback and media-session integration.
5. Build and test a debug APK on a physical phone.

The existing `ydk-music` backend remains the source of truth for authentication,
playlists, media, and listening rooms.
