/* Shared interaction layer for Android and the web. */
const compactScreen = window.matchMedia("(max-width: 700px)");
let importTimer = null;
let importChecking = false;
let importSubmitting = false;

const mobileNav = document.createElement("nav");
mobileNav.className = "mobile-navigation";
mobileNav.setAttribute("aria-label", "Main navigation");
mobileNav.innerHTML = `
  <button type="button" data-library-view="home"><span aria-hidden="true">⌂</span>Home</button>
  <button type="button" data-library-view="liked"><span aria-hidden="true">♡</span>Liked</button>
  <button type="button" data-mobile-library><span aria-hidden="true">▤</span>Library</button>
  <button type="button" data-profile-nav="profile"><span aria-hidden="true">◉</span>Profile</button>`;
document.body.append(mobileNav);

const drawerShade = document.createElement("button");
drawerShade.className = "library-shade";
drawerShade.setAttribute("aria-label", "Close library");
drawerShade.addEventListener("click", () => setSidebarCollapsed(true));
document.body.append(drawerShade);

const connectionBanner = document.createElement("div");
connectionBanner.className = "connection-banner";
connectionBanner.setAttribute("role", "status");
connectionBanner.hidden = true;
connectionBanner.textContent = "You’re offline. Your library will reconnect when you’re back.";
document.body.append(connectionBanner);

const importNotice = document.createElement("section");
importNotice.className = "import-notice";
importNotice.setAttribute("aria-label", "Song import");
importNotice.setAttribute("aria-live", "polite");
importNotice.hidden = true;
importNotice.innerHTML = '<div><strong></strong><p></p></div><button type="button" aria-label="Dismiss import status">×</button>';
importNotice.querySelector("button").addEventListener("click", () => { importNotice.hidden = true; });
document.body.append(importNotice);

function showImportNotice(title, message, failed = false) {
  importNotice.hidden = false;
  importNotice.classList.toggle("is-error", failed);
  importNotice.querySelector("strong").textContent = title;
  importNotice.querySelector("p").textContent = message;
  const inlineStatus = byId("yt-import-status");
  if (inlineStatus) inlineStatus.textContent = message;
}

function setImportBusy(busy) {
  for (const id of ["importYtBtn", "searchYtBtn"]) {
    const button = byId(id);
    if (button) button.disabled = busy;
  }
  const label = byId("importYtBtn")?.querySelector("span");
  if (label) label.textContent = busy ? "Importing…" : "Import";
}

async function queueYoutubeImport() {
  if (importSubmitting || storedJSON("pending-import", null)) {
    showImportNotice("Import in progress", "You can keep browsing. We’ll let you know when it’s ready.");
    return;
  }
  const query = byId("ytUrlInput").value.trim();
  if (!state.activeId) return showToast("Open a playlist first.", true);
  if (query.length < 2) return showToast("Enter a song name and artist, or a YouTube link.", true);
  if (!navigator.onLine) return showToast("Connect to the internet to import a song.", true);
  const requestId = globalThis.crypto?.randomUUID?.()
    || `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
  const pending = { request_id: requestId, query, playlist_id: state.activeId };
  localStorage.setItem("pending-import", JSON.stringify(pending));
  importSubmitting = true;
  setImportBusy(true);
  showImportNotice("Adding your music", "Searching for the best available audio. You can keep browsing.");
  try { await checkPendingImport(); }
  finally { importSubmitting = false; }
}

async function checkPendingImport() {
  clearTimeout(importTimer);
  if (importChecking || !state.authStarted) return;
  const pending = storedJSON("pending-import", null);
  if (!pending) return;
  if (!navigator.onLine) {
    showImportNotice("Import status paused", "Reconnect to check the result. The server may still be importing.");
    return;
  }
  importChecking = true;
  setImportBusy(true);
  try {
    const job = pending.id
      ? await api(`/api/imports/${encodeURIComponent(pending.id)}`)
      : await api("/api/imports/youtube", { method: "POST", body: JSON.stringify(pending) });
    pending.id = job.id;
    localStorage.setItem("pending-import", JSON.stringify(pending));
    if (job.status === "complete" || job.status === "failed") {
      localStorage.removeItem("pending-import");
      setImportBusy(false);
      showImportNotice(job.status === "complete" ? "Added to your playlist" : "Couldn’t import this song", job.message, job.status === "failed");
      if (job.status === "complete") {
        if (byId("ytUrlInput").value.trim() === pending.query) byId("ytUrlInput").value = "";
        // Refresh the current view without pulling the user out of another playlist.
        if (state.currentView === "playlist" && state.activeId === pending.playlist_id) await loadPlaylist(pending.playlist_id);
        else if (state.currentView === "home") await loadHome();
      }
      return;
    }
    showImportNotice("Adding your music", job.message);
    importTimer = setTimeout(checkPendingImport, 2500);
  } catch (error) {
    if ([400, 404, 409, 422].includes(error.status)) {
      localStorage.removeItem("pending-import");
      setImportBusy(false);
      showImportNotice("Import needs attention", error.message, true);
    } else {
      showImportNotice("Waiting for your library", "Your request is saved. We’ll check again when the server responds.");
      importTimer = setTimeout(checkPendingImport, 8000);
    }
  } finally { importChecking = false; }
}

function updateNavigation() {
  const selected = document.body.classList.contains("profile-open") ? "profile" : state.currentView;
  mobileNav.querySelectorAll("button").forEach((button) => {
    const key = button.dataset.libraryView || button.dataset.profileNav || "playlist";
    const active = key === selected;
    if (button.getAttribute("aria-current") !== (active ? "page" : "false")) {
      button.setAttribute("aria-current", active ? "page" : "false");
    }
  });
}

document.addEventListener("click", async (event) => {
  if (!compactScreen.matches) return;
  if (event.target.closest("[data-mobile-library]")) {
    if (document.body.classList.contains("profile-open")) await loadHome();
    setSidebarCollapsed(!document.body.classList.contains("sidebar-collapsed"));
  } else if (event.target.closest("[data-library-view], [data-playlist-id], [data-profile-nav], #profile-button, #song-lab-button, #room-button")) {
    setSidebarCollapsed(true);
  }
});
new MutationObserver(updateNavigation).observe(byId("playlist-view"), { childList: true });
new MutationObserver(updateNavigation).observe(byId("profile-view"), { childList: true });

function afterAuthentication() {
  if (compactScreen.matches) setSidebarCollapsed(true);
  updateNavigation();
  void checkPendingImport();
}
window.addEventListener("ydkmusic:authenticated", afterAuthentication);
if (state.authStarted) afterAuthentication();
compactScreen.addEventListener("change", () => {
  setSidebarCollapsed(compactScreen.matches);
});
window.addEventListener("offline", () => {
  connectionBanner.hidden = false;
  setConnection(false);
});
window.addEventListener("online", () => {
  connectionBanner.hidden = true;
  if (state.authStarted) {
    void checkPendingImport();
    void loadPlaylists(state.currentView === "playlist" ? state.activeId : null);
  } else void initializeAuth();
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && state.authStarted) void checkPendingImport();
});
connectionBanner.hidden = navigator.onLine;

// The compact dock exposes next-track without opening the full player.
const nextTrack = document.createElement("button");
nextTrack.className = "player-button dock-next";
nextTrack.type = "button";
nextTrack.setAttribute("aria-label", "Next song");
nextTrack.textContent = "›";
nextTrack.addEventListener("click", () => byId("now-playing-next").click());
document.querySelector(".player-options").prepend(nextTrack);
