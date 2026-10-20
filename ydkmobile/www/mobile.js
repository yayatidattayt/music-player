/* Android presentation. Existing playback and library handlers remain authoritative. */
function renderMobileHome() {
  const playlists = state.homePlaylists;
  const tracks = playlists.flatMap((playlist) => (playlist.tracks || []).map((track) => ({
    ...track, shelfCover: track.cover_url || playlist.cover_url,
  })));
  const favourites = [...tracks].sort((a, b) => (b.play_count || 0) - (a.play_count || 0)).slice(0, 8);
  const hour = new Date().getHours();
  const greeting = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  playlistView.innerHTML = `
    <header class="mobile-home-heading"><span class="eyebrow">Your music, your space</span><h1>${greeting}.</h1><p>A little closer to the music you love.</p></header>
    <div class="mobile-home-filters" aria-label="Library shortcuts"><button type="button" data-library-view="liked">♡ Liked songs</button><button type="button" data-mobile-library>Your library</button><button type="button" data-mobile-create>＋ New playlist</button></div>
    <section class="mobile-home-section"><div class="mobile-section-heading"><h2>Jump back in</h2><button type="button" data-mobile-library>View all</button></div>
      <div class="mobile-quick-grid">${playlists.slice(0, 6).map((playlist, index) => `<button type="button" data-playlist-id="${playlist.id}"><span class="mobile-quick-art cover-${index % 4}"${coverStyle(playlist.cover_url)} aria-hidden="true">♫</span><span><strong>${escapeHtml(playlist.name)}</strong><small>${playlist.tracks?.length || 0} songs</small></span></button>`).join("") || '<div class="mobile-empty"><strong>Your next favourite starts here.</strong><p>Create a playlist, then add your songs.</p><button type="button" data-mobile-create>Create a playlist</button></div>'}</div>
    </section>
    ${favourites.length ? `<section class="mobile-home-section"><div class="mobile-section-heading"><h2>Made for your rotation</h2></div><div class="mobile-music-shelf" aria-label="Songs from your library">${favourites.map((track, index) => `<button class="mobile-song-card" type="button" data-action="play-track" data-track-id="${track.id}"><span class="mobile-song-art cover-${index % 4}"${coverStyle(track.shelfCover)} aria-hidden="true"><span>▶</span></span><strong>${escapeHtml(track.title)}</strong><small>${escapeHtml(track.artist || "Unknown artist")}</small></button>`).join("")}</div></section>` : ""}
    ${tracks.length ? `<section class="mobile-home-section"><div class="mobile-section-heading"><h2>Find your mood</h2></div><div class="mood-grid">${renderMoodGroups(tracks)}</div></section>` : ""}
    <section class="mobile-library-summary" aria-label="Your library at a glance"><span><strong>${playlists.length}</strong> playlists</span><span><strong>${tracks.length}</strong> songs</span><span><strong data-minutes-heard>${formatMinutesHeard(state.stats?.total_listened_seconds)}</strong> minutes heard</span></section>`;
}

const mobileIcons = {
  home: '<path d="m3 10 9-7 9 7v10H15v-6H9v6H3Z"/>',
  liked: '<path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8Z"/>',
  playlist: '<path d="M4 4v16M9 4v16m5-16 5 16M3 4h3M8 4h3"/>',
  profile: '<circle cx="12" cy="8" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2"/>',
};
mobileNav.querySelectorAll("button").forEach((button) => {
  const key = button.dataset.libraryView || button.dataset.profileNav || "playlist";
  button.querySelector("span").innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${mobileIcons[key]}</svg>`;
});
document.addEventListener("click", (event) => {
  if (event.target.closest("[data-mobile-create]")) openPlaylistDialog();
  if (event.target.closest(".player-song") && playerTrack) updateNowPlayingDisplay();
});
byId("now-playing-close").textContent = "⌄";
const mobileControls = document.querySelector(".now-playing-controls");
mobileControls.prepend(byId("now-playing-shuffle"));
mobileControls.append(byId("now-playing-repeat"));

const mobilePlayerTools = document.createElement("div");
mobilePlayerTools.className = "mobile-player-tools";
mobilePlayerTools.append(byId("lyrics-toggle"));
const mobileEqualizer = document.createElement("button");
mobileEqualizer.type = "button";
mobileEqualizer.textContent = "Equalizer";
mobileEqualizer.addEventListener("click", () => byId("equalizer-toggle").click());
mobilePlayerTools.append(mobileEqualizer);
document.querySelector(".now-playing-volume").before(mobilePlayerTools);

// Advanced lyric adjustments stay available without crowding the reading area.
const timingDetails = document.createElement("details");
timingDetails.className = "mobile-lyrics-tools";
timingDetails.innerHTML = '<summary>Timing & editing</summary>';
document.querySelector(".lyrics-timing").before(timingDetails);
timingDetails.append(document.querySelector(".lyrics-timing"));
const timingActions = document.createElement("div");
timingActions.className = "mobile-lyrics-actions";
timingActions.append(byId("lyrics-sync"), byId("lyrics-edit"));
timingDetails.append(timingActions);
byId("lyrics-fetch").textContent = "Find lyrics";
if (state.authStarted && state.currentView === "home") renderHome();
