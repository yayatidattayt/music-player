const state = {
  playlists: [],
  likedTracks: [],
  currentView: "playlist",
  shuffle: false,
  repeat: false,
  activeId: null,
  activePlaylist: null,
  trackQuery: "",
  trackSort: "custom",
  selectedTrackIds: new Set(),
  editingPlaylistId: null,
  editingTrackId: null,
  detailRequest: 0,
  toastTimer: null,
  queue: JSON.parse(localStorage.getItem("ydkmusic-queue") || "[]"),
  draggingQueueIndex: null,
  homePlaylists: [],
  stats: null,
  listenProgress: 0,
  lastPlaybackTime: null,
  countedListenTrackId: null,
  nowPlayingClosing: false,
  syncedLyricLine: null,
  lyricScrollFrame: null,
  lyricRenderKey: "",
  lyricOffset: 0,
  lyricManualOffset: 0,
  lyricClockFrame: null,
  lyricUsesEstimates: false,
  lyricAnalysisTrackId: null,
  lyricAnalyzing: false,
  lyricRefresh: Number(localStorage.getItem("ydkmusic-lyric-refresh") || 0),
};

const byId = (id) => document.getElementById(id);
const playlistList = byId("playlist-list");
const playlistView = byId("playlist-view");
const playlistSearch = byId("playlist-search");
const connectionLabel = byId("connection-label");
const statusLight = document.querySelector(".status-light");
const audioEngine = byId("audio-engine");
const visualizer = byId("now-playing-visualizer");
const topbarVisualizer = byId("topbar-visualizer");
const savedSidebarState = localStorage.getItem("ydkmusic-sidebar-collapsed") === "true";
let playerTrack = null;
let audioContext = null;
let analyser = null;
let audioSource = null;
let frequencyData = null;
let bassEnergy = 0;
let peakLevels = [];
let artThemeRequest = 0;
let artThemeUrl = null;

function crossfadeArtwork(previous) {
  if (!previous?.a || !previous?.b) return;
  const layer = document.createElement("div");
  layer.className = "art-crossfade";
  layer.style.setProperty("--crossfade-a", previous.a);
  layer.style.setProperty("--crossfade-b", previous.b);
  layer.style.setProperty("--crossfade-accent", previous.accent || previous.a);
  document.body.appendChild(layer);
  layer.addEventListener("animationend", () => layer.remove(), { once: true });
}

function setSidebarCollapsed(collapsed) {
  document.body.classList.toggle("sidebar-collapsed", collapsed);
  localStorage.setItem("ydkmusic-sidebar-collapsed", String(collapsed));
  byId("sidebar-toggle").setAttribute("aria-label", collapsed ? "Open sidebar" : "Collapse sidebar");
}

function setupAudioAnalyzer() {
  if (audioContext) return;
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextClass) return;

  audioContext = new AudioContextClass();
  analyser = audioContext.createAnalyser();
  analyser.fftSize = 128;
  analyser.smoothingTimeConstant = 0.82;
  audioSource = audioContext.createMediaElementSource(audioEngine);
  audioSource.connect(analyser);
  analyser.connect(audioContext.destination);
  frequencyData = new Uint8Array(analyser.frequencyBinCount);
}

function drawVisualizer() {
  drawTopbarVisualizer();
  if (!visualizer) {
    requestAnimationFrame(drawVisualizer);
    return;
  }
  const bounds = visualizer.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  visualizer.width = Math.max(1, bounds.width * ratio);
  visualizer.height = Math.max(1, bounds.height * ratio);
  const context = visualizer.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, bounds.width, bounds.height);
  if (visualizer.hidden) {
    requestAnimationFrame(drawVisualizer);
    return;
  }

  const bars = Math.max(56, Math.floor(bounds.width / 11));
  const gap = 4;
  const width = Math.max(2, (bounds.width - gap * (bars - 1)) / bars);
  context.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--art-accent").trim() || "#c5d77b";
  if (analyser && frequencyData && !audioEngine.paused) analyser.getByteFrequencyData(frequencyData);
  const bassBins = Math.max(1, Math.floor((frequencyData?.length || 1) * 0.16));
  const bassAverage = frequencyData
    ? frequencyData.slice(0, bassBins).reduce((sum, value) => sum + value, 0) / bassBins / 255
    : 0;
  bassEnergy = bassEnergy * 0.84 + bassAverage * 0.16;
  document.documentElement.style.setProperty("--bass-hit", String(Math.min(1, bassEnergy * 1.8)));
  const accent = getComputedStyle(document.documentElement).getPropertyValue("--art-accent").trim() || "#c5d77b";
  const gradient = context.createLinearGradient(0, bounds.height, 0, 0);
  gradient.addColorStop(0, accent);
  gradient.addColorStop(.55, "rgba(255, 255, 255, .9)");
  gradient.addColorStop(1, accent);
  context.fillStyle = gradient;
  context.globalAlpha = audioEngine.paused ? 0.16 : 0.86;
  if (peakLevels.length !== bars) peakLevels = new Array(bars).fill(0);

  const centerY = bounds.height - 12;
  for (let index = 0; index < bars; index += 1) {
    const sourceIndex = Math.floor((index / bars) * (frequencyData?.length || 1));
    const level = frequencyData ? (frequencyData[sourceIndex] || 0) / 255 : 0.08;
    const height = 4 + level * (bounds.height * 0.72) + bassEnergy * 10;
    peakLevels[index] = Math.max(height, peakLevels[index] - 0.7);
    context.beginPath();
    context.roundRect(index * (width + gap), centerY - height, width, height, width / 2);
    context.fill();

    context.globalAlpha = audioEngine.paused ? 0.04 : 0.2;
    context.beginPath();
    context.roundRect(index * (width + gap), centerY + 3, width, height * .28, width / 2);
    context.fill();
    context.globalAlpha = audioEngine.paused ? 0.16 : 0.86;

    context.fillStyle = "rgba(255, 255, 255, .82)";
    context.fillRect(index * (width + gap), centerY - peakLevels[index] - 2, width, 2);
    context.fillStyle = gradient;
  }

  const glow = context.createRadialGradient(bounds.width / 2, centerY, 0, bounds.width / 2, centerY, bounds.width * .32);
  glow.addColorStop(0, `rgba(255, 255, 255, ${Math.min(.18, bassEnergy * .22)})`);
  glow.addColorStop(1, "rgba(255, 255, 255, 0)");
  context.fillStyle = glow;
  context.fillRect(0, 0, bounds.width, bounds.height);
  requestAnimationFrame(drawVisualizer);
}

function drawTopbarVisualizer() {
  if (!topbarVisualizer) return;
  const bounds = topbarVisualizer.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  topbarVisualizer.width = Math.max(1, bounds.width * ratio);
  topbarVisualizer.height = Math.max(1, bounds.height * ratio);
  const context = topbarVisualizer.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, bounds.width, bounds.height);
  if (!bounds.width) return;

  if (analyser && frequencyData && !audioEngine.paused) analyser.getByteFrequencyData(frequencyData);
  const bassBins = Math.max(1, Math.floor((frequencyData?.length || 1) * .16));
  const bassAverage = frequencyData
    ? frequencyData.slice(0, bassBins).reduce((sum, value) => sum + value, 0) / bassBins / 255
    : 0;
  bassEnergy = bassEnergy * .84 + bassAverage * .16;
  document.documentElement.style.setProperty("--bass-hit", String(Math.min(1, bassEnergy * 1.8)));
  const artA = getComputedStyle(document.documentElement).getPropertyValue("--art-a").trim() || "#c5d77b";
  const artB = getComputedStyle(document.documentElement).getPropertyValue("--art-b").trim() || "#7e8cc7";
  const accent = getComputedStyle(document.documentElement).getPropertyValue("--art-accent").trim() || "#c5d77b";
  const energy = audioEngine.paused ? .45 : .8 + bassEnergy * .6;
  const time = performance.now() * .001;
  const drawWave = (color, phase, amplitude, thickness, alpha) => {
    context.beginPath();
    for (let x = -24; x <= bounds.width + 24; x += 8) {
      const progress = x / Math.max(1, bounds.width);
      const wave = Math.sin(progress * Math.PI * 4.2 + phase + time * .8) * amplitude
        + Math.sin(progress * Math.PI * 8.5 - phase * .65 - time * .55) * amplitude * .28;
      const y = bounds.height / 2 + wave * energy;
      if (x === -24) context.moveTo(x, y);
      else context.lineTo(x, y);
    }
    context.strokeStyle = color;
    context.globalAlpha = alpha;
    context.lineWidth = thickness;
    context.lineCap = "round";
    context.lineJoin = "round";
    context.shadowColor = color;
    context.shadowBlur = 10;
    context.stroke();
    context.shadowBlur = 0;
  };
  drawWave(artB, bassEnergy * 1.8, bounds.height * .29, 10, .9);
  drawWave(artA, 1.7 - bassEnergy, bounds.height * .24, 9, .86);
  drawWave(accent, 3.5 + bassEnergy, bounds.height * .16, 5, .98);
  drawWave("rgba(255,255,255,.8)", 2.4, bounds.height * .08, 2, .72);
  context.globalAlpha = 1;
}

function updateArtTheme(url) {
  const nextUrl = url || null;
  if (nextUrl === artThemeUrl) return;
  artThemeUrl = nextUrl;
  const requestId = ++artThemeRequest;
  if (!nextUrl) {
    resetArtTheme();
    return;
  }

  const image = new Image();
  image.crossOrigin = "anonymous";
  image.onload = () => {
    if (requestId !== artThemeRequest || artThemeUrl !== nextUrl) return;
    try {
      const canvas = document.createElement("canvas");
      const context = canvas.getContext("2d", { willReadFrequently: true });
      canvas.width = 40;
      canvas.height = 40;
      context.drawImage(image, 0, 0, 40, 40);
      const pixels = context.getImageData(0, 0, 40, 40).data;
      const colors = [];
      for (let index = 0; index < pixels.length; index += 16) {
        const red = pixels[index];
        const green = pixels[index + 1];
        const blue = pixels[index + 2];
        const brightness = (red + green + blue) / 3;
        if (brightness > 18 && brightness < 235) colors.push({ red, green, blue, brightness });
      }
      if (!colors.length) return;
      colors.sort((first, second) => second.brightness - first.brightness);
      const mainColor = colors[Math.floor(colors.length * 0.35)] || colors[0];
      const secondColor = colors[Math.floor(colors.length * 0.75)] || colors[colors.length - 1];
      const colorA = `rgb(${mainColor.red}, ${mainColor.green}, ${mainColor.blue})`;
      const colorB = `rgb(${secondColor.red}, ${secondColor.green}, ${secondColor.blue})`;
      const previous = {
        a: getComputedStyle(document.documentElement).getPropertyValue("--art-a").trim(),
        b: getComputedStyle(document.documentElement).getPropertyValue("--art-b").trim(),
        accent: getComputedStyle(document.documentElement).getPropertyValue("--art-accent").trim(),
      };
      crossfadeArtwork(previous);
      document.documentElement.style.setProperty("--art-a", colorA);
      document.documentElement.style.setProperty("--art-b", colorB);
      document.documentElement.style.setProperty("--art-glow", `rgba(${mainColor.red}, ${mainColor.green}, ${mainColor.blue}, .2)`);
      document.documentElement.style.setProperty("--art-accent", colorA);
    } catch {
      if (requestId === artThemeRequest) resetArtTheme();
    }
  };
  image.onerror = () => { if (requestId === artThemeRequest) resetArtTheme(); };
  image.src = nextUrl;
}

function updateMediaSession() {
  if (!playerTrack || !('mediaSession' in navigator) || !('MediaMetadata' in window)) return;

  const cover = playerTrack.cover_url || state.activePlaylist?.cover_url;
  navigator.mediaSession.metadata = new MediaMetadata({
    title: playerTrack.title || "Untitled song",
    artist: playerTrack.artist || "Unknown artist",
    album: playerTrack.album || state.activePlaylist?.name || "ydkmusic",
    artwork: cover ? [{ src: cover }] : [],
  });
}

function updateMediaSessionState() {
  if (!('mediaSession' in navigator)) return;
  navigator.mediaSession.playbackState = playerTrack && !audioEngine.paused ? "playing" : "paused";
}

function configureMediaSession() {
  if (!('mediaSession' in navigator)) return;

  const handlers = {
    play: async () => { if (playerTrack) await audioEngine.play(); },
    pause: () => audioEngine.pause(),
    seekbackward: (details) => {
      audioEngine.currentTime = Math.max(0, audioEngine.currentTime - (details.seekOffset || 10));
    },
    seekforward: (details) => {
      audioEngine.currentTime = Math.min(audioEngine.duration || Infinity, audioEngine.currentTime + (details.seekOffset || 10));
    },
    previoustrack: () => playPreviousTrack(),
    nexttrack: () => playNextTrack(),
  };

  Object.entries(handlers).forEach(([action, handler]) => {
    try { navigator.mediaSession.setActionHandler(action, handler); } catch { /* Browser does not support this action. */ }
  });
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

function coverStyle(url) {
  return url ? ` style="background-image: url('${escapeHtml(url)}')"` : "";
}

function coverClass(url, fallbackClass = "") {
  return url ? "has-image" : fallbackClass;
}
function resetArtTheme() {
  const previous = {
    a: getComputedStyle(document.documentElement).getPropertyValue("--art-a").trim(),
    b: getComputedStyle(document.documentElement).getPropertyValue("--art-b").trim(),
    accent: getComputedStyle(document.documentElement).getPropertyValue("--art-accent").trim(),
  };
  crossfadeArtwork(previous);
  document.documentElement.style.setProperty("--art-a", "#171916");
  document.documentElement.style.setProperty("--art-b", "#20231e");
  document.documentElement.style.setProperty("--art-glow", "rgba(255, 255, 255, .04)");
  document.documentElement.style.setProperty("--art-accent", "#c5d77b");
}

function showToast(message, isError = false) {
  const toast = byId("toast");
  toast.textContent = message;
  toast.classList.toggle("is-error", isError);
  toast.classList.add("is-visible");
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => toast.classList.remove("is-visible"), 2800);
}

function renderSongLabResult(result) {
  const resultBox = byId("song-lab-result");
  const value = (item, fallback = "Not available") => item === null || item === undefined || item === "" ? fallback : escapeHtml(item);
  resultBox.hidden = false;
  resultBox.innerHTML = `
    <div class="song-lab-result-heading">
      <div>
        <span class="eyebrow">${escapeHtml(result.source || "Local audio")}</span>
        <h3>${value(result.title, "Untitled song")}</h3>
        <p>${value(result.artist, "Unknown artist")}${result.album ? ` · ${escapeHtml(result.album)}` : ""}</p>
      </div>
      ${result.cover_url ? `<div class="song-lab-vinyl" aria-label="Cover artwork"><span class="song-lab-record"><img src="${escapeHtml(result.cover_url)}" alt="" /></span><span class="song-lab-label" aria-hidden="true"></span></div>` : `<div class="song-lab-vinyl song-lab-vinyl-empty" aria-hidden="true"><span class="song-lab-record">♪</span></div>`}
    </div>
    <div class="song-lab-metrics">
      <div><strong>${value(result.bpm, "—")}</strong><span>BPM</span></div>
      <div><strong>${value(result.key, "—")}</strong><span>Key</span></div>
      <div><strong>${formatDuration(result.duration_seconds)}</strong><span>Length</span></div>
      <div><strong>${value(result.genre, "—")}</strong><span>Genre</span></div>
    </div>
    <p class="song-lab-note">${escapeHtml(result.note || "Analysis complete.")}</p>
    ${result.preview_url ? `<a class="song-lab-preview" href="${escapeHtml(result.preview_url)}" target="_blank" rel="noreferrer">Open preview ↗</a>` : ""}
    ${result.source_url ? `<a class="song-lab-preview" href="${escapeHtml(result.source_url)}" target="_blank" rel="noreferrer">View source ↗</a>` : ""}`;
}

async function analyzeSongFromLab() {
  const query = byId("song-lab-query").value.trim();
  const artist = byId("song-lab-artist").value.trim();
  const file = byId("song-lab-file").files[0];
  const submit = byId("song-lab-submit");
  const resultBox = byId("song-lab-result");
  if (!query) return;
  submit.disabled = true;
  submit.textContent = "Reading…";
  resultBox.hidden = false;
  resultBox.innerHTML = '<p class="song-lab-loading">Listening for the details…</p>';
  try {
    let result;
    if (file) {
      const form = new FormData();
      form.append("file", file);
      const metadata = await api("/api/tracks/metadata", { method: "POST", body: form });
      result = {
        ...metadata,
        title: metadata.title || query,
        artist: metadata.artist || "Unknown artist",
        source: "Local audio file",
        bpm: metadata.bpm || null,
        key: metadata.key || null,
        note: metadata.bpm || metadata.key
          ? "BPM/key read or estimated from the local audio file."
          : "File metadata was read locally. No BPM or musical-key data was available.",
      };
    } else {
      result = await api(`/api/song-analysis?song=${encodeURIComponent(query)}${artist ? `&artist=${encodeURIComponent(artist)}` : ""}`);
    }
    renderSongLabResult(result);
  } catch (error) {
    resultBox.innerHTML = `<p class="song-lab-error">${escapeHtml(error.message || "Song analysis failed.")}</p>`;
  } finally {
    submit.disabled = false;
    submit.textContent = "Analyze song";
  }
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (
    options.body &&
    !(options.body instanceof FormData) &&
    !headers.has("Content-Type")
  ) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(path, { ...options, headers });
  if (response.status === 204) return null;

  const raw = await response.text();
  let data = null;
  try { data = raw ? JSON.parse(raw) : null; } catch { data = raw; }

  if (!response.ok) {
    const detail = data?.detail;
    const message = Array.isArray(detail)
      ? detail.map((item) => item.msg).join(" ")
      : (detail || `Request failed (${response.status})`);
    throw new Error(message);
  }
  return data;
}

function setConnection(online) {
  connectionLabel.textContent = online ? "Library is up to date" : "Can't reach the library";
  statusLight.classList.toggle("is-online", online);
  statusLight.classList.toggle("is-offline", !online);
}

function renderSidebar() {
  if (!state.playlists.length) {
    const searching = playlistSearch.value.trim().length > 0;
    playlistList.innerHTML = `<p class="sidebar-empty">${searching ? "No playlists with that name." : "No playlists yet. Make one when you're ready."}</p>`;
    return;
  }

  playlistList.innerHTML = state.playlists.map((playlist, index) => `
    <button class="playlist-link ${playlist.id === state.activeId ? "is-active" : ""}"
      type="button" data-playlist-id="${playlist.id}" aria-current="${playlist.id === state.activeId ? "page" : "false"}">
      <span class="mini-cover ${coverClass(playlist.cover_url, `cover-${index % 4}`)}"${coverStyle(playlist.cover_url)} aria-hidden="true"></span>
      <span class="playlist-link-copy">
        <span class="playlist-link-name">${escapeHtml(playlist.name)}</span>
        <span class="playlist-link-count">${playlist.track_count} ${playlist.track_count === 1 ? "song" : "songs"}</span>
      </span>
    </button>`).join("");
}

function formatDuration(seconds) {
  if (!Number.isFinite(Number(seconds)) || Number(seconds) <= 0) return "—";
  const value = Math.floor(Number(seconds));
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, "0")}`;
}
function formatClock(seconds) {
  if (!Number.isFinite(Number(seconds))) return "0:00";

  const value = Math.max(0, Math.floor(Number(seconds)));
  const minutes = Math.floor(value / 60);
  const remainingSeconds = String(value % 60).padStart(2, "0");

  return `${minutes}:${remainingSeconds}`;
}

function updateNowPlayingDisplay() {
  if (!playerTrack) return;

  const nowPlaying = byId("now-playing");
  state.nowPlayingClosing = false;
  nowPlaying.classList.remove("is-closing");
  nowPlaying.hidden = false;
  requestAnimationFrame(() => nowPlaying.classList.add("is-open"));
  const nowPlayingArt = byId("now-playing-art");
  const nowPlayingBackdrop = byId("now-playing-backdrop");
  const cover = playerTrack.cover_url || state.activePlaylist?.cover_url || "";

  byId("now-playing-title").textContent = playerTrack.title || "Untitled song";
  byId("now-playing-artist").textContent = playerTrack.artist || "Unknown artist";
  byId("now-playing-album").textContent = playerTrack.album || "Single";
  renderLyrics();

  nowPlayingArt.textContent = cover ? "" : "♪";
  nowPlayingArt.style.backgroundImage = cover ? `url("${cover}")` : "";
  nowPlayingBackdrop.style.backgroundImage = cover ? `url("${cover}")` : "";

  updateNowPlayingControls();
  renderQueue();
  visualizer.hidden = false;
}

function renderLyrics() {
  const view = byId("lyrics-view");
  if (!view || !playerTrack) return;
  const lyrics = String(playerTrack.lyrics || "").trim();
  const duration = Number(audioEngine.duration) || Number(playerTrack.duration_seconds) || 0;
  const renderKey = `${playerTrack.id}:${lyrics}:${Math.round(duration)}`;
  if (state.lyricRenderKey === renderKey) return;
  state.lyricRenderKey = renderKey;
  state.syncedLyricLine = null;

  if (!lyrics) {
    view.innerHTML = '<p class="lyrics-empty">No lyrics added yet.<br><span>Use Edit to add them for this song.</span></p>';
    return;
  }

  const rawLines = lyrics.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const parsedLines = rawLines.flatMap((line) => {
    const timestamps = [...line.matchAll(/\[(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)\]/g)];
    const text = line.replace(/\[(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)\]/g, "").trim();
    if (!timestamps.length) return [{ text: line, time: null }];
    return timestamps.map((match) => ({
      text,
      time: Number(match[1] || 0) * 3600 + Number(match[2]) * 60 + Number(match[3]),
    }));
  });
  const hasTimestamps = parsedLines.some((line) => line.time !== null);
  state.lyricUsesEstimates = !hasTimestamps;
  const allLines = hasTimestamps
    ? parsedLines
    : parsedLines.map((line, index) => ({
      ...line,
      time: duration ? Math.max(0, duration * .06 + (index / Math.max(1, parsedLines.length - 1)) * duration * .84) : null,
      estimated: true,
    }));

  view.innerHTML = allLines.map((line) => {
    const timed = line.time !== null;
    const classes = timed ? (line.estimated ? "lyric-timed lyric-estimated" : "lyric-timed") : "lyric-untimed";
    return `<p class="${classes}"${timed ? ` data-lyric-time="${line.time}"` : ""}>${escapeHtml(line.text) || "&nbsp;"}</p>`;
  }).join("");
}

async function analyzeAudioLyricsOffset(track = playerTrack) {
  if (!track?.audio_url || !window.OfflineAudioContext && !window.webkitOfflineAudioContext) return 0;
  if (state.lyricAnalyzing && state.lyricAnalysisTrackId === track.id) return state.lyricOffset;

  const cached = sessionStorage.getItem(`sideb-lyrics-offset-${track.id}`);
  if (cached !== null) {
    state.lyricOffset = Number(cached) || 0;
    state.lyricAnalysisTrackId = track.id;
    return state.lyricOffset;
  }

  state.lyricAnalyzing = true;
  state.lyricAnalysisTrackId = track.id;
  const syncButton = byId("lyrics-sync");
  if (syncButton) {
    syncButton.disabled = true;
    syncButton.textContent = "Analyzing…";
  }

  try {
    const response = await fetch(track.audio_url);
    if (!response.ok) throw new Error("Audio could not be analyzed.");
    const buffer = await response.arrayBuffer();
    const Context = window.OfflineAudioContext || window.webkitOfflineAudioContext;
    const decoder = new Context(1, 1, 44100);
    const decoded = await decoder.decodeAudioData(buffer);
    const sampleRate = decoded.sampleRate;
    const channelCount = decoded.numberOfChannels;
    const sampleCount = decoded.length;
    const windowSize = Math.max(512, Math.floor(sampleRate * .035));
    const threshold = .012;
    let firstAudibleSample = 0;

    for (let start = 0; start < sampleCount; start += windowSize) {
      let energy = 0;
      const end = Math.min(sampleCount, start + windowSize);
      const length = end - start;
      for (let channel = 0; channel < channelCount; channel += 1) {
        const samples = decoded.getChannelData(channel);
        for (let index = start; index < end; index += 1) energy += Math.abs(samples[index]);
      }
      const average = energy / Math.max(1, length * channelCount);
      if (average >= threshold) {
        firstAudibleSample = start;
        break;
      }
    }

    state.lyricOffset = Math.min(8, firstAudibleSample / sampleRate);
    sessionStorage.setItem(`sideb-lyrics-offset-${track.id}`, String(state.lyricOffset));
    syncLyrics();
    showToast(state.lyricOffset > .15
      ? `Lyrics aligned with a ${state.lyricOffset.toFixed(1)}s audio lead-in.`
      : "Lyrics are already aligned with the audio.");
  } catch (error) {
    state.lyricOffset = 0;
    showToast(error.message || "Audio analysis was unavailable.", true);
  } finally {
    state.lyricAnalyzing = false;
    if (syncButton) {
      syncButton.disabled = false;
      syncButton.textContent = "Sync audio";
    }
  }
  return state.lyricOffset;
}

function syncLyrics() {
  const lines = [...document.querySelectorAll("#lyrics-view p[data-lyric-time]")]
    .sort((a, b) => Number(a.dataset.lyricTime) - Number(b.dataset.lyricTime));
  if (!lines.length || !playerTrack) return;
  // LRC timestamps are already expressed on the audio file's timeline. Only
  // estimated plain lyrics need the detected leading-audio offset.
  const lyricTime = Math.max(0, audioEngine.currentTime
    - (state.lyricUsesEstimates ? state.lyricOffset : 0)
    + state.lyricManualOffset);
  const current = [...lines].reverse().find((line) => Number(line.dataset.lyricTime) <= lyricTime) || null;
  const changed = state.syncedLyricLine !== current;
  lines.forEach((line) => line.classList.toggle("is-current", line === current));
  state.syncedLyricLine = current;
  if (changed && current && !byId("lyrics-panel").hidden) {
    const view = byId("lyrics-view");
    const lineTop = current.offsetTop;
    const lineBottom = lineTop + current.offsetHeight;
    const visibleTop = view.scrollTop + view.clientHeight * 0.2;
    const visibleBottom = view.scrollTop + view.clientHeight * 0.8;
    if (lineTop < visibleTop || lineBottom > visibleBottom) {
      const start = view.scrollTop;
      const target = Math.max(0, lineTop - view.clientHeight * 0.42);
      const distance = target - start;
      if (Math.abs(distance) > 2) {
        if (state.lyricScrollFrame) cancelAnimationFrame(state.lyricScrollFrame);
        const startedAt = performance.now();
        const duration = 520;
        const animateScroll = (now) => {
          const progress = Math.min(1, (now - startedAt) / duration);
          const eased = progress < .5
            ? 2 * progress * progress
            : 1 - Math.pow(-2 * progress + 2, 2) / 2;
          view.scrollTop = start + distance * eased;
          if (progress < 1) state.lyricScrollFrame = requestAnimationFrame(animateScroll);
          else state.lyricScrollFrame = null;
        };
        state.lyricScrollFrame = requestAnimationFrame(animateScroll);
      }
    }
  }
}

function updateLyricsTimingDisplay() {
  const value = byId("lyrics-offset-value");
  if (value) value.textContent = `${state.lyricManualOffset >= 0 ? "+" : ""}${state.lyricManualOffset.toFixed(1)}s`;
}

function startLyricsClock() {
  if (state.lyricClockFrame) return;
  const tick = () => {
    state.lyricClockFrame = null;
    if (!audioEngine.paused && playerTrack) {
      syncLyrics();
      state.lyricClockFrame = requestAnimationFrame(tick);
    }
  };
  state.lyricClockFrame = requestAnimationFrame(tick);
}

function stopLyricsClock() {
  if (state.lyricClockFrame) cancelAnimationFrame(state.lyricClockFrame);
  state.lyricClockFrame = null;
}

function renderQueue() {
  const queue = byId("playback-queue");
  if (!queue) return;
  const queueTitle = byId("queue-title");
  if (queueTitle) queueTitle.textContent = state.queue.length ? `Up next · ${state.queue.length}` : "Up next";
  queue.innerHTML = state.queue.length
    ? state.queue.map((track, index) => `
        <li class="queue-item" draggable="true" data-queue-index="${index}">
          <input class="queue-select" type="checkbox" data-queue-select="${index}" aria-label="Select ${escapeHtml(track.title)}">
          <span class="queue-index">${index + 1}</span>
          <span class="queue-swatch ${coverClass(track.cover_url, `tone-${index % 4}`)}"${coverStyle(track.cover_url)}>${track.cover_url ? "" : escapeHtml((track.title || "♪").slice(0, 1).toUpperCase())}</span>
          <span class="queue-copy"><strong>${escapeHtml(track.title)}</strong><small>${escapeHtml(track.artist || "Unknown artist")}</small></span>
          <span class="queue-item-actions"><button class="queue-play" type="button" data-action="play-queued-track" data-queue-index="${index}" aria-label="Play ${escapeHtml(track.title)} now" title="Play now">▶</button><button class="queue-save" type="button" data-action="save-queue-track" data-queue-index="${index}" aria-label="Save ${escapeHtml(track.title)} to a playlist" title="Save to playlist">＋</button><button class="queue-remove" type="button" data-action="remove-queue" data-queue-index="${index}" aria-label="Remove ${escapeHtml(track.title)} from queue" title="Remove from queue">×</button></span>
        </li>`).join("")
    : '<li class="queue-empty">Your queue is empty. Add songs from a playlist.</li>';
}

function openQueueSaveDialog(index) {
  const track = state.queue[index];
  if (!track) return;
  byId("queue-save-caption").textContent = `Choose where to save “${track.title}”.`;
  byId("queue-save-playlists").innerHTML = state.playlists.length
    ? state.playlists.map((playlist) => `<button class="queue-save-playlist" type="button" data-action="save-queue-to-playlist" data-queue-index="${index}" data-playlist-id="${playlist.id}"><span>${escapeHtml(playlist.name)}</span><small>${playlist.track_count ?? ""} songs</small></button>`).join("")
    : '<p class="queue-empty">Create a playlist first.</p>';
  byId("queue-save-dialog").showModal();
}

function addToQueue(track, playNext = false) {
  if (!track?.audio_url) return showToast("This song does not have an uploaded audio file.", true);
  if (state.queue.some((queued) => queued.id === track.id)) return showToast("That song is already in the queue.", true);
  if (playNext) state.queue.unshift(track);
  else state.queue.push(track);
  localStorage.setItem("ydkmusic-queue", JSON.stringify(state.queue));
  renderQueue();
  showToast(playNext ? "Added to play next." : "Added to queue.");
}

function buildSmartQueue() {
  if (!playerTrack) return showToast("Start a song first to build a smart queue.", true);
  const library = state.homePlaylists.flatMap((playlist) => playlist.tracks || []);
  const candidates = library.filter((track) => track.audio_url && track.id !== playerTrack.id && !state.queue.some((queued) => queued.id === track.id));
  const ranked = candidates.sort((a, b) => {
    const score = (track) => (track.artist === playerTrack.artist ? 5 : 0) + (track.genre && track.genre === playerTrack.genre ? 4 : 0) + (trackMood(track) === trackMood(playerTrack) ? 3 : 0) + (track.play_count || 0) * .01;
    return score(b) - score(a);
  });
  state.queue.push(...ranked.slice(0, 8));
  localStorage.setItem("ydkmusic-queue", JSON.stringify(state.queue));
  renderQueue();
  showToast("Smart flow added to your queue.");
}

function closeNowPlaying() {
  const nowPlaying = byId("now-playing");
  state.nowPlayingClosing = true;
  nowPlaying.classList.remove("is-open");
  nowPlaying.classList.add("is-closing");
  setTimeout(() => {
    if (!nowPlaying.classList.contains("is-open")) {
      nowPlaying.hidden = true;
      nowPlaying.classList.remove("is-closing");
      state.nowPlayingClosing = false;
    }
  }, 280);
  visualizer.hidden = true;
  byId("lyrics-panel").hidden = true;
  byId("lyrics-toggle").setAttribute("aria-expanded", "false");
}

function closeLyricsPanel() {
  const panel = byId("lyrics-panel");
  panel.hidden = true;
  byId("lyrics-toggle").setAttribute("aria-expanded", "false");
}

function updateNowPlayingControls() {
  if (!playerTrack) return;

  const duration = Number.isFinite(audioEngine.duration)
    ? audioEngine.duration
    : Number(playerTrack.duration_seconds) || 0;
  const progress = byId("now-playing-progress");
  const toggle = byId("now-playing-toggle");
  const likeButton = byId("now-playing-like");
  const shuffleButton = byId("now-playing-shuffle");
  const repeatButton = byId("now-playing-repeat");
  const muteButton = byId("now-playing-mute");
  const volumeInput = byId("now-playing-volume");
  const volumeValue = byId("now-playing-volume-value");
  const visibleVolume = audioEngine.muted ? 0 : audioEngine.volume;

  progress.max = duration || 100;
  progress.value = audioEngine.currentTime || 0;
  byId("now-playing-current").textContent = formatClock(audioEngine.currentTime);
  byId("now-playing-total").textContent = formatClock(duration);
  byId("now-playing-status").textContent = audioEngine.paused ? "Paused" : "Playing now";

  toggle.textContent = audioEngine.paused ? "▶" : "Ⅱ";
  toggle.setAttribute("aria-label", audioEngine.paused ? "Play" : "Pause");
  likeButton.textContent = playerTrack.is_liked ? "♥ Liked" : "♡ Like";
  likeButton.classList.toggle("is-liked", Boolean(playerTrack.is_liked));
  likeButton.setAttribute("aria-label", playerTrack.is_liked ? "Unlike song" : "Like song");
  shuffleButton.classList.toggle("is-active", state.shuffle);
  repeatButton.classList.toggle("is-active", state.repeat);
  shuffleButton.setAttribute("aria-label", state.shuffle ? "Shuffle on" : "Shuffle off");
  repeatButton.setAttribute("aria-label", state.repeat ? "Repeat on" : "Repeat off");
  muteButton.textContent = audioEngine.muted || audioEngine.volume === 0 ? "🔇" : "🔊";
  muteButton.setAttribute("aria-label", audioEngine.muted || audioEngine.volume === 0 ? "Unmute" : "Mute");
  volumeInput.value = audioEngine.volume;
  volumeValue.textContent = `${Math.round(visibleVolume * 100)}%`;
}


function updatePlayerDisplay() {
  const playerBar = byId("player-bar");
  const playButton = byId("player-play");
  const progress = byId("player-progress");
  const currentTime = byId("player-current-time");
  const totalTime = byId("player-total-time");
  const volumeValue = byId("player-volume-value");
  const muteButton = byId("player-mute");
  document.body.classList.toggle("is-audio-playing", Boolean(playerTrack && !audioEngine.paused));

  const visibleVolume = audioEngine.muted ? 0 : audioEngine.volume;
  volumeValue.textContent = `${Math.round(visibleVolume * 100)}%`;
  muteButton.textContent = audioEngine.muted || audioEngine.volume === 0 ? "🔇" : "🔊";
  muteButton.setAttribute("aria-label", audioEngine.muted || audioEngine.volume === 0 ? "Unmute" : "Mute");

  const shuffleButton = byId("player-shuffle");
  const repeatButton = byId("player-repeat");
  shuffleButton.classList.toggle("is-active", state.shuffle);
  repeatButton.classList.toggle("is-active", state.repeat);
  shuffleButton.setAttribute("aria-label", state.shuffle ? "Shuffle on" : "Shuffle off");
  repeatButton.setAttribute("aria-label", state.repeat ? "Repeat on" : "Repeat off");

  if (!playerTrack) {
    playerBar.hidden = true;
    return;
  }

  playerBar.hidden = false;

  byId("player-title").textContent = playerTrack.title;
  byId("player-artist").textContent = playerTrack.artist;
  const playerArt = byId("player-art");
  const playerCover = playerTrack.cover_url || state.activePlaylist?.cover_url;
  playerArt.textContent = playerCover ? "" : (playerTrack.title || "♪").slice(0, 1).toUpperCase();
  playerArt.classList.toggle("has-image", Boolean(playerCover));
  playerArt.style.backgroundImage = playerCover ? `url("${playerCover}")` : "";

  playButton.textContent = audioEngine.paused ? "▶" : "Ⅱ";
  playButton.setAttribute(
    "aria-label",
    audioEngine.paused ? "Play" : "Pause",
  );

  const duration = Number.isFinite(audioEngine.duration)
    ? audioEngine.duration
    : Number(playerTrack.duration_seconds) || 0;

  progress.max = duration || 100;
  progress.value = audioEngine.currentTime || 0;
  currentTime.textContent = formatClock(audioEngine.currentTime);
  totalTime.textContent = formatClock(duration);

  if (!byId("now-playing").hidden && !state.nowPlayingClosing) updateNowPlayingDisplay();
}

async function playTrack(track) {
  setupAudioAnalyzer();
  if (audioContext?.state === "suspended") await audioContext.resume();

  if (!track.audio_url) {
    showToast("This song does not have an uploaded audio file.", true);
    return;
  }

  if (playerTrack?.id === track.id) {
    if (audioEngine.paused) {
      await audioEngine.play();
    } else {
      audioEngine.pause();
    }

    updatePlayerDisplay();
    renderPlaylist();
    return;
  }

  playerTrack = track;
  state.lyricOffset = 0;
  state.lyricManualOffset = Number(localStorage.getItem(`sideb-lyrics-manual-offset-${track.id}`)) || 0;
  state.lyricAnalysisTrackId = null;
  updateLyricsTimingDisplay();
  updateMediaSession();
  updateArtTheme(track.cover_url || state.activePlaylist?.cover_url);
  audioEngine.src = track.audio_url;
  state.listenProgress = 0;
  state.lastPlaybackTime = null;
  state.countedListenTrackId = null;
  audioEngine.volume = Number(byId("player-volume").value) || 0.8;
  audioEngine.load();

  updatePlayerDisplay();
  analyzeAudioLyricsOffset(track);

  try {
    await audioEngine.play();
  } catch {
    showToast("The audio file could not be played.", true);
  }

  updatePlayerDisplay();
  renderPlaylist();
}

function playbackTracks() {
  if (state.currentView === "liked") return state.likedTracks || [];
  return state.activePlaylist?.tracks || [];
}

async function playNextTrack() {
  if (state.queue.length) {
    const nextQueued = state.queue.shift();
    localStorage.setItem("ydkmusic-queue", JSON.stringify(state.queue));
    renderQueue();
    await playTrack(nextQueued);
    return;
  }
  const tracks = playbackTracks().filter((track) => track.audio_url);
  if (!tracks.length || !playerTrack) return;

  if (state.repeat) {
    audioEngine.currentTime = 0;
    await audioEngine.play();
    return;
  }

  if (state.shuffle) {
    const choices = tracks.filter((track) => track.id !== playerTrack.id);
    const next = choices[Math.floor(Math.random() * choices.length)] || tracks[0];
    await playTrack(next);
    return;
  }

  const currentIndex = tracks.findIndex((track) => track.id === playerTrack.id);
  const next = tracks[currentIndex + 1];
  if (next) {
    await playTrack(next);
  } else {
    audioEngine.currentTime = 0;
    updatePlayerDisplay();
    renderPlaylist();
  }
}
function formatTotalTime(seconds) {
  if (!seconds) return "under a minute";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} hr ${rest} min` : `${hours} hr`;
}

function renderEmptyLibrary() {
  state.activeId = null;
  state.activePlaylist = null;
  playlistView.innerHTML = `
    <div class="empty-library">
      <span class="empty-mark" aria-hidden="true">b.</span>
      <span class="eyebrow">A little room for the good stuff</span>
      <h1>Your next favorite collection starts here.</h1>
      <p>Make a playlist for the long way home, the slow mornings, or whatever the week calls for.</p>
      <button class="button button-accent" type="button" data-action="new-playlist"><span class="button-plus" aria-hidden="true">+</span> Make a playlist</button>
    </div>`;
}

function renderLikedSongs() {
  state.currentView = "liked";
  state.activeId = null;
  state.activePlaylist = null;

  const tracks = state.likedTracks;

  if (!tracks.length) {
    playlistView.innerHTML = `
      <div class="empty-library">
        <span class="empty-mark" aria-hidden="true">♡</span>
        <span class="eyebrow">Your favorites</span>
        <h1>No liked songs yet.</h1>
        <p>Press the heart beside any song to keep it here.</p>
      </div>`;
    return;
  }

  playlistView.innerHTML = `
    <section class="liked-view">
      <div class="liked-heading">
        <span class="eyebrow">Your favorites</span>
        <h1>Liked songs</h1>
        <p>${tracks.length} ${tracks.length === 1 ? "song" : "songs"} you want to keep close.</p>
      </div>

      <div class="liked-track-list">
        ${tracks.map((track, index) => `
          <article
            class="liked-track-row"
            data-track-id="${track.id}"
            data-drag-scope="liked"
            draggable="true"
          >
            <span class="track-number">${String(index + 1).padStart(2, "0")}</span>

            <div class="track-main">
              <button
                class="track-play"
                type="button"
                data-action="play-liked-track"
                data-track-id="${track.id}"
                aria-label="Play ${escapeHtml(track.title)}"
              >
                ▶
              </button>

              <span
                class="track-swatch ${coverClass(track.cover_url, `tone-${index % 4}`)}"
                ${coverStyle(track.cover_url)}
              >
                ${track.cover_url ? "" : escapeHtml((track.title || "♪").slice(0, 1).toUpperCase())}
              </span>

              <span class="track-copy">
                <span class="track-title">${escapeHtml(track.title)}</span>
                <span class="track-artist">${escapeHtml(track.artist)}</span>
              </span>
            </div>

            <span class="track-album">${escapeHtml(track.album || "—")}</span>
            <span class="track-duration">${formatDuration(track.duration_seconds)}</span>

            <button
              class="row-action like-button is-liked"
              type="button"
              data-action="toggle-liked-track"
              data-track-id="${track.id}"
              aria-label="Unlike ${escapeHtml(track.title)}"
              title="Remove from liked songs"
            >
              ♥
            </button>
          </article>
        `).join("")}
      </div>
    </section>`;
}

async function loadLikedSongs() {
  try {
    state.likedTracks = await api("/api/tracks/liked");
    const refreshedTrack = state.likedTracks.find((track) => track.id === playerTrack?.id);
    if (refreshedTrack) {
      playerTrack = refreshedTrack;
      updateMediaSession();
      if (!byId("now-playing").hidden) updateNowPlayingDisplay();
    }
    renderLikedSongs();
  } catch (error) {
    showToast(error.message, true);
  }
}

function renderError(message) {
  playlistView.innerHTML = `
    <div class="error-view">
      <span class="eyebrow">The shelf is out of reach</span>
      <h1>Couldn't load your playlists.</h1>
      <p>${escapeHtml(message)}<br />Start the app in PowerShell with <code>python -m uvicorn app.main:app --reload</code>, then try again.</p>
      <button class="button button-accent" type="button" data-action="retry">Try again</button>
    </div>`;
}

function filteredTracks() {
  const tracks = state.activePlaylist?.tracks || [];
  const term = state.trackQuery.trim().toLocaleLowerCase();
  const filtered = term
    ? tracks.filter((track) => [track.title, track.artist, track.album]
      .some((part) => String(part || "").toLocaleLowerCase().includes(term)))
    : tracks;
  return [...filtered].sort((a, b) => {
    if (state.trackSort === "title") return a.title.localeCompare(b.title);
    if (state.trackSort === "artist") return a.artist.localeCompare(b.artist) || a.title.localeCompare(b.title);
    if (state.trackSort === "plays") return (b.play_count || 0) - (a.play_count || 0);
    return (a.position || 0) - (b.position || 0);
  });
}

function trackMood(track) {
  const text = `${track.genre || ""} ${track.title || ""}`.toLowerCase();
  if (/rock|metal|punk|hardcore/.test(text)) return "Energized";
  if (/jazz|blues|soul|acoustic/.test(text)) return "Soulful";
  if (/electro|house|dance|techno|disco/.test(text)) return "Electric";
  if (/classical|ambient|piano|instrumental|study/.test(text)) return "Focused";
  if (/chill|lofi|calm|sleep|relax/.test(text)) return "Unwind";
  return track.genre || "Unsorted";
}

function renderMoodGroups(tracks) {
  const groups = tracks.reduce((result, track) => {
    const mood = trackMood(track);
    (result[mood] ||= []).push(track);
    return result;
  }, {});
  return Object.entries(groups).map(([mood, items]) => `
    <article class="mood-card" role="button" tabindex="0" data-mood="${escapeHtml(mood)}">
      <div class="mood-card-heading"><span>${escapeHtml(mood)}</span><small>${items.length} ${items.length === 1 ? "song" : "songs"}</small></div>
      <div class="mood-card-tracks">${items.slice(0, 4).map((track) => `<button type="button" data-action="play-track" data-track-id="${track.id}">${escapeHtml(track.title)}</button>`).join("")}</div>
    </article>`).join("");
}

function lyricOfTheDay(tracks) {
  const candidates = tracks.filter((track) => String(track.lyrics || "").trim());
  if (!candidates.length) return null;
  const today = new Date().toISOString().slice(0, 10);
  const seed = [...today].reduce((total, character) => total * 31 + character.charCodeAt(0), 7);
  const track = candidates[Math.abs(seed + state.lyricRefresh) % candidates.length];
  const lines = track.lyrics.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const repeated = lines.findIndex((line, index) => lines.slice(index + 1).some((other) => other.toLowerCase() === line.toLowerCase()));
  const start = repeated >= 0 ? repeated : Math.max(0, Math.floor(lines.length / 2) - 1);
  return { track, excerpt: lines.slice(start, start + 4) };
}

function renderHome() {
  const playlists = state.homePlaylists;
  const allTracks = playlists.flatMap((playlist) => playlist.tracks || []);
  const totalListens = state.stats?.total_listens ?? allTracks.reduce((sum, track) => sum + (track.play_count || 0), 0);
  const topTracks = [...allTracks].sort((a, b) => (b.play_count || 0) - (a.play_count || 0)).slice(0, 5);
  const moodTracks = [...allTracks].sort((a, b) => (b.play_count || 0) - (a.play_count || 0));
  const lyric = lyricOfTheDay(allTracks);

  playlistView.innerHTML = `
    <section class="home-hero"><span class="eyebrow">Your listening room</span><h1>Good music,<br><em>kept close.</em></h1><p>Everything you have made, saved, and returned to.</p></section>
    <section class="home-stats"><article><strong>${playlists.length}</strong><span>playlists</span></article><article><strong>${allTracks.length}</strong><span>songs</span></article><article><strong>${totalListens}</strong><span>listens</span></article><article><strong>${state.stats?.total_minutes || 0}</strong><span>minutes heard</span></article></section>
    <section class="home-section"><div class="home-section-heading"><div><span class="eyebrow">Your collections</span><h2>Playlists</h2></div></div><div class="home-playlist-grid">
      ${playlists.map((playlist, index) => `<button class="home-playlist-card" type="button" data-playlist-id="${playlist.id}"><span class="home-playlist-art cover-${index % 4}"${coverStyle(playlist.cover_url)}></span><strong>${escapeHtml(playlist.name)}</strong><small>${playlist.tracks.length} songs · ${playlist.tracks.reduce((sum, track) => sum + (track.play_count || 0), 0)} listens</small></button>`).join("") || '<p class="home-empty">Create your first playlist to give your library a home.</p>'}
    </div></section>
    <section class="home-columns"><div class="home-section"><div class="home-section-heading"><div><span class="eyebrow">Most returned to</span><h2>Top songs</h2></div></div><div class="home-top-list">${topTracks.map((track, index) => `<button type="button" data-action="play-track" data-track-id="${track.id}"><span>${String(index + 1).padStart(2, "0")}</span><strong>${escapeHtml(track.title)}</strong><small>${track.play_count || 0} listens</small></button>`).join("") || '<p class="home-empty">Your listening history will appear here.</p>'}</div>
      ${lyric ? `<article class="lyric-of-day"><div class="lyric-of-day-heading"><div><span class="eyebrow">Lyric of the day</span><span class="lyric-song-name">${escapeHtml(lyric.track.title)}</span></div><button class="lyric-refresh" type="button" data-action="refresh-lyric" aria-label="Show another lyric" title="Show another lyric">↻</button></div><blockquote>${lyric.excerpt.map((line) => `<p>${escapeHtml(line)}</p>`).join("")}</blockquote><button type="button" data-action="play-track" data-track-id="${lyric.track.id}">Play this song <span>↗</span></button></article>` : '<div class="lyric-of-day lyric-empty-card"><span class="eyebrow">Lyric of the day</span><p>Add lyrics to a song and a daily line will appear here.</p></div>'}
    </div>
      <div class="home-section"><div class="home-section-heading"><div><span class="eyebrow">A different way in</span><h2>Moods & genres</h2></div></div><div class="mood-grid">${renderMoodGroups(moodTracks)}</div></div></section>`;
}

async function loadHome() {
  state.currentView = "home";
  try {
    state.homePlaylists = await Promise.all(state.playlists.map((playlist) => api(`/api/playlists/${playlist.id}`)));
    state.stats = await api("/api/listening/stats");
    const missingGenreTracks = state.homePlaylists.flatMap((playlist) => playlist.tracks || []).filter((track) => !track.genre);
    if (missingGenreTracks.length) {
      await Promise.all(missingGenreTracks.map((track) => api(`/api/tracks/${track.id}/catalog-metadata`, { method: "POST" }).catch(() => null)));
      state.homePlaylists = await Promise.all(state.playlists.map((playlist) => api(`/api/playlists/${playlist.id}`)));
    }
    renderHome();
  } catch (error) {
    showToast(error.message, true);
  }
}

function renderTrackRows() {
  const tracks = filteredTracks();
  if (!tracks.length && !state.activePlaylist.tracks.length) {
    return `
      <div class="empty-songs">
        <span class="empty-glyph" aria-hidden="true">♪</span>
        <strong>Nothing on this side yet.</strong>
        <p>Add the first song and get this collection going.</p>
        <button class="button button-quiet" type="button" data-action="new-track"><span class="button-plus" aria-hidden="true">+</span> Add a song</button>
      </div>`;
  }
  if (!tracks.length) return `<div class="filter-empty">No songs match “${escapeHtml(state.trackQuery)}”.</div>`;

  return tracks.map((track, index) => `
    <article
      class="track-row ${playerTrack?.id === track.id ? "is-playing" : ""} ${state.selectedTrackIds.has(track.id) ? "is-selected" : ""}"
      data-track-id="${track.id}"
      data-drag-scope="playlist"
      draggable="true"
    >
      <span class="track-number"><input class="track-select" type="checkbox" data-track-select="${track.id}" ${state.selectedTrackIds.has(track.id) ? "checked" : ""} aria-label="Select ${escapeHtml(track.title)}" />${String(index + 1).padStart(2, "0")}</span>
      <div class="track-main">
        ${
          track.audio_url
            ? `<button
                class="track-play"
                type="button"
                data-action="play-track"
                data-track-id="${track.id}"
                aria-label="Play ${escapeHtml(track.title)}"
                title="Play song"
              >▶</button>`
            : `<span class="track-play" aria-hidden="true">·</span>`
        }
        <span class="track-swatch ${coverClass(track.cover_url || state.activePlaylist?.cover_url, `tone-${(track.id + index) % 4}`)}"${coverStyle(track.cover_url || state.activePlaylist?.cover_url)} aria-hidden="true">${track.cover_url || state.activePlaylist?.cover_url ? "" : escapeHtml((track.title || "♪").slice(0, 1).toLocaleUpperCase())}</span>
        <span class="track-copy">
          <span class="track-title">${escapeHtml(track.title)}</span>
          <span class="track-artist">${escapeHtml(track.artist)}</span>
        </span>
      </div>
      <span class="track-album" title="${escapeHtml(track.album || "")}">${escapeHtml(track.album || "—")}</span>
  <span class="track-duration">${formatDuration(track.duration_seconds)}<small class="track-listens">${track.play_count || 0} plays</small></span>
<span class="track-actions">
  ${track.audio_url ? `<button class="row-action" type="button" data-action="play-next" data-track-id="${track.id}" aria-label="Play ${escapeHtml(track.title)} next" title="Play next">⏭</button>` : ""}
  <button
    class="row-action"
    type="button"
    data-action="add-queue"
    data-track-id="${track.id}"
    aria-label="Add ${escapeHtml(track.title)} to queue"
    title="Add to queue"
  >
    +
  </button>

  <button
    class="row-action like-button ${track.is_liked ? "is-liked" : ""}"
    type="button"
    data-action="toggle-like"
    data-track-id="${track.id}"
    aria-label="${track.is_liked ? "Unlike" : "Like"} ${escapeHtml(track.title)}"
    title="${track.is_liked ? "Remove from liked songs" : "Like song"}"
  >
    ${track.is_liked ? "♥" : "♡"}
  </button>

  <button
    class="row-action"
    type="button"
    data-action="edit-track"
    data-track-id="${track.id}"
    aria-label="Edit ${escapeHtml(track.title)}"
    title="Edit song"
  >
    Edit
  </button>

  <button
    class="row-action remove"
    type="button"
    data-action="delete-track"
    data-track-id="${track.id}"
    aria-label="Remove ${escapeHtml(track.title)}"
    title="Remove song"
  >
    ×
  </button>
</span>
    </article>`).join("");
}

function renderPlaylist() {
  const playlist = state.activePlaylist;
  const activeCover = playerTrack?.cover_url || playlist?.cover_url;
  updateArtTheme(activeCover);
  if (!playlist && state.currentView === "liked") {
    renderLikedSongs();
    return;
  }
  if (!playlist) return renderEmptyLibrary();

  const tracks = playlist.tracks || [];
  state.selectedTrackIds = new Set([...state.selectedTrackIds].filter((id) => tracks.some((track) => track.id === id)));
  const totalSeconds = tracks.reduce((total, track) => total + (Number(track.duration_seconds) || 0), 0);
  const art = (playlist.id - 1) % 4;
  const description = playlist.description
    ? `<p class="hero-description">${escapeHtml(playlist.description)}</p>`
    : `<p class="hero-description">A collection of songs worth keeping close.</p>`;

  playlistView.innerHTML = `
    <section class="playlist-hero" aria-labelledby="playlist-title">
      <div class="hero-cover ${coverClass(playlist.cover_url, `cover-${art}`)}"${coverStyle(playlist.cover_url)} aria-hidden="true"></div>
      <div class="hero-copy">
        <p class="hero-kicker">Playlist · ${String(tracks.length).padStart(2, "0")} ${tracks.length === 1 ? "song" : "songs"}</p>
        <h1 class="hero-title" id="playlist-title">${escapeHtml(playlist.name)}</h1>
        ${description}
        <div class="hero-meta"><span>${tracks.length} ${tracks.length === 1 ? "song" : "songs"}</span><span class="meta-dot" aria-hidden="true"></span><span>${formatTotalTime(totalSeconds)} total</span></div>
      </div>
      <div class="hero-actions">
        <button class="button button-accent" type="button" data-action="new-track"><span class="button-plus" aria-hidden="true">+</span> Add a song</button>
        <button class="button button-quiet" type="button" data-action="edit-playlist">Edit details</button>
        <button class="button button-quiet button-danger" type="button" data-action="delete-playlist">Delete</button>
      </div>
    </section>
    <section class="songs-section" aria-labelledby="songs-heading">
      <div class="songs-heading">
        <div><h2 class="section-title" id="songs-heading">The songs</h2><p class="section-caption">In the order you put them here.</p></div>
        <div class="songs-tools"><button class="button button-quiet bulk-remove-button" type="button" data-action="remove-selected-tracks" ${state.selectedTrackIds.size ? "" : "disabled"}>Remove ${state.selectedTrackIds.size || "selected"}</button><select class="track-sort" id="track-sort" aria-label="Sort songs"><option value="custom">Custom order</option><option value="title">Title</option><option value="artist">Artist</option><option value="plays">Most played</option></select>
        <input class="track-filter" id="track-search" type="search" placeholder="Filter songs" aria-label="Filter songs in this playlist" autocomplete="off" />
        </div>
      </div>
      <div class="track-table">
        <div class="track-labels" aria-hidden="true"><span><input class="track-select" type="checkbox" data-action="select-visible-tracks" ${filteredTracks().length && filteredTracks().every((track) => state.selectedTrackIds.has(track.id)) ? "checked" : ""} aria-label="Select all visible songs" /> #</span><span>Title</span><span class="label-album">Album</span><span>Time</span><span></span></div>
        <div id="track-rows">${renderTrackRows()}</div>
      </div>
    </section>`;

  byId("track-search").value = state.trackQuery;
  byId("track-sort").value = state.trackSort;
  byId("track-sort").addEventListener("change", (event) => { state.trackSort = event.target.value; renderPlaylist(); });
  byId("track-search").addEventListener("input", (event) => {
    state.trackQuery = event.target.value;
    const rows = byId("track-rows");
    if (rows) rows.innerHTML = renderTrackRows();
  });
}

async function loadPlaylist(id) {
  state.currentView = "playlist";
  state.activeId = id;
  state.activePlaylist = null;
  const requestId = ++state.detailRequest;
  renderSidebar();
  playlistView.innerHTML = '<div class="loading-view"><span class="loading-mark">b.</span><p>Turning it over…</p></div>';
  try {
    const playlist = await api(`/api/playlists/${id}`);
    if (requestId !== state.detailRequest) return;
    state.activePlaylist = playlist;
    const refreshedTrack = playlist.tracks?.find((track) => track.id === playerTrack?.id);
    if (refreshedTrack) {
      playerTrack = refreshedTrack;
      updateMediaSession();
    }
    state.trackQuery = "";
    setConnection(true);
    renderSidebar();
    renderPlaylist();
  } catch (error) {
    if (requestId !== state.detailRequest) return;
    setConnection(false);
    renderError(error.message);
  }
}

async function loadPlaylists(selectId = state.activeId) {
  const params = new URLSearchParams({ limit: "100" });
  const term = playlistSearch.value.trim();
  if (term) params.set("search", term);
  try {
    const result = await api(`/api/playlists?${params}`);
    state.playlists = result.items;
    setConnection(true);
    renderSidebar();

    if (selectId !== null && selectId !== undefined) {
      await loadPlaylist(selectId);
    } else if (state.playlists.length) {
      await loadHome();
    } else {
      state.homePlaylists = [];
      renderHome();
    }
  } catch (error) {
    setConnection(false);
    renderError(error.message);
  }
}

function openPlaylistDialog(editing = false) {
  state.editingPlaylistId = editing ? state.activeId : null;
  const playlist = editing ? state.activePlaylist : null;
  byId("playlist-dialog-kicker").textContent = editing ? "Tidy up this collection" : "A new collection";
  byId("playlist-dialog-title").textContent = editing ? "Edit playlist" : "Make a playlist";
  byId("playlist-submit").textContent = editing ? "Save changes" : "Create playlist";
  byId("playlist-name").value = playlist?.name || "";
  byId("playlist-description").value = playlist?.description || "";
  byId("playlist-cover-file").value = "";
  byId("playlist-cover-help").textContent = playlist?.cover_url
    ? "Choose a new image to replace the current artwork."
    : "Add your own artwork. It will appear across the library.";
  byId("playlist-dialog").showModal();
  byId("playlist-name").focus();
}

function openTrackDialog(track = null) {
  if (!state.activePlaylist) return;
  state.editingTrackId = track?.id ?? null;
  byId("track-dialog-kicker").textContent = track ? "Make a small change" : "Add to the side";
  byId("track-dialog-title").textContent = track ? "Edit song" : "Add a song";
  byId("track-submit").textContent = track ? "Save changes" : "Add song";
  byId("track-title").value = track?.title || "";
  byId("track-artist").value = track?.artist || "";
  byId("track-album").value = track?.album || "";
  byId("track-duration").value = track?.duration_seconds || "";
  byId("track-file").value = "";
  byId("track-cover-file").value = "";
  byId("track-file").disabled = Boolean(track);
  byId("track-cover-help").textContent = track?.cover_url
    ? "Choose a new image to replace this song's artwork."
    : "Embedded artwork is picked up automatically when the file has it.";
  byId("track-dialog").showModal();
  byId("track-title").focus();
}

document.addEventListener("click", async (event) => {
    const libraryViewButton = event.target.closest("[data-library-view]");

  if (libraryViewButton?.dataset.libraryView === "home") {
    await loadHome();
    return;
  }
  if (libraryViewButton?.dataset.libraryView === "liked") {
    await loadLikedSongs();
    return;
  }
  const playlistButton = event.target.closest("[data-playlist-id]");
  if (playlistButton) {
    const id = Number(playlistButton.dataset.playlistId);
    if (id !== state.activeId) await loadPlaylist(id);
    return;
  }

  const moodCard = event.target.closest("[data-mood]");
  if (moodCard && !event.target.closest("[data-action]")) {
    const mood = moodCard.dataset.mood;
    const tracks = state.homePlaylists.flatMap((playlist) => playlist.tracks || []).filter((track) => trackMood(track) === mood);
    byId("mood-dialog-title").textContent = mood;
    byId("mood-dialog-caption").textContent = `${tracks.length} ${tracks.length === 1 ? "song" : "songs"} in this part of your library.`;
    byId("mood-dialog-tracks").innerHTML = tracks.map((track) => `
      <button class="mood-dialog-track" type="button" data-action="play-track" data-track-id="${track.id}">
        <span class="mood-dialog-track-number">${String(tracks.indexOf(track) + 1).padStart(2, "0")}</span>
        <span><strong>${escapeHtml(track.title)}</strong><small>${escapeHtml(track.artist || "Unknown artist")}</small></span>
        <small>${track.play_count || 0} listens</small>
      </button>`).join("");
    byId("mood-dialog").showModal();
    return;
  }

  const closeButton = event.target.closest("[data-close-dialog]");
  if (closeButton) {
    byId(closeButton.dataset.closeDialog).close();
    return;
  }

  const queueItem = event.target.closest(".queue-item");
  if (queueItem && !event.target.closest("button, input, a")) {
    const index = Number(queueItem.dataset.queueIndex);
    const track = state.queue[index];
    if (track) {
      state.queue.splice(index, 1);
      localStorage.setItem("ydkmusic-queue", JSON.stringify(state.queue));
      renderQueue();
      await playTrack(track);
    }
    return;
  }

  const actionButton = event.target.closest("[data-action]");
  if (!actionButton) return;
  const { action } = actionButton.dataset;

  if (action === "new-playlist") openPlaylistDialog();
  if (action === "edit-playlist") openPlaylistDialog(true);
  if (action === "new-track") openTrackDialog();
  if (action === "retry") await loadPlaylists(null);
  if (action === "refresh-lyric") {
    state.lyricRefresh += 1;
    localStorage.setItem("ydkmusic-lyric-refresh", String(state.lyricRefresh));
    renderHome();
    showToast("Showing another lyric.");
    return;
  }

  if (action === "play-track") {
    const track = [...(state.activePlaylist?.tracks || []), ...state.homePlaylists.flatMap((playlist) => playlist.tracks || [])].find(
      (item) => item.id === Number(actionButton.dataset.trackId),
    );

    if (track) {
      byId("mood-dialog")?.close();
      const sourcePlaylist = state.homePlaylists.find((playlist) => playlist.tracks?.some((item) => item.id === track.id));
      if (sourcePlaylist) {
        state.activeId = sourcePlaylist.id;
        state.activePlaylist = sourcePlaylist;
      }
      await playTrack(track);
    }
  }
  if (action === "add-queue" || action === "play-next") {
    const track = [...(state.activePlaylist?.tracks || []), ...(state.likedTracks || [])]
      .find((item) => item.id === Number(actionButton.dataset.trackId));
    if (track) addToQueue(track, action === "play-next");
  }
  if (action === "remove-queue") {
    state.queue.splice(Number(actionButton.dataset.queueIndex), 1);
    localStorage.setItem("ydkmusic-queue", JSON.stringify(state.queue));
    renderQueue();
  }
  if (action === "play-queued-track") {
    const index = Number(actionButton.dataset.queueIndex);
    const track = state.queue[index];
    if (track) {
      state.queue.splice(index, 1);
      localStorage.setItem("ydkmusic-queue", JSON.stringify(state.queue));
      renderQueue();
      await playTrack(track);
    }
  }
  if (action === "save-queue-track") openQueueSaveDialog(Number(actionButton.dataset.queueIndex));
  if (action === "save-queue-to-playlist") {
    const track = state.queue[Number(actionButton.dataset.queueIndex)];
    const playlistId = Number(actionButton.dataset.playlistId);
    if (!track || !playlistId) return;
    try {
      await api(`/api/playlists/${playlistId}/tracks/from-queue/${track.id}`, { method: "POST" });
      byId("queue-save-dialog").close();
      showToast(`Saved “${track.title}” to your playlist.`);
      if (state.activeId === playlistId) await loadPlaylist(playlistId);
      else await loadPlaylists(state.activeId);
    } catch (error) {
      showToast(error.message.includes("already") ? "That song is already in this playlist." : error.message, true);
    }
  }
  if (action === "clear-queue") {
    state.queue = [];
    localStorage.removeItem("ydkmusic-queue");
    renderQueue();
    showToast("Queue cleared.");
  }
  if (action === "smart-queue") buildSmartQueue();
  if (action === "remove-selected-queue") {
    const selected = new Set([...document.querySelectorAll("[data-queue-select]:checked")].map((input) => Number(input.dataset.queueSelect)));
    state.queue = state.queue.filter((_, index) => !selected.has(index));
    localStorage.setItem("ydkmusic-queue", JSON.stringify(state.queue));
    renderQueue();
    showToast(selected.size ? `${selected.size} songs removed from queue.` : "Select songs to remove first.");
  }
  if (action === "select-visible-tracks") {
    const visible = filteredTracks();
    const shouldSelect = visible.length > 0 && !visible.every((track) => state.selectedTrackIds.has(track.id));
    visible.forEach((track) => shouldSelect ? state.selectedTrackIds.add(track.id) : state.selectedTrackIds.delete(track.id));
    renderPlaylist();
  }
  if (action === "remove-selected-tracks") {
    const selected = [...state.selectedTrackIds];
    if (!selected.length) return;
    if (!window.confirm(`Remove ${selected.length} ${selected.length === 1 ? "song" : "songs"} from this playlist?`)) return;
    try {
      await api(`/api/playlists/${state.activeId}/tracks/bulk-delete`, {
        method: "DELETE",
        body: JSON.stringify(selected),
      });
      state.selectedTrackIds.clear();
      showToast(`${selected.length} ${selected.length === 1 ? "song" : "songs"} removed.`);
      await loadPlaylists(state.activeId);
    } catch (error) {
      showToast(error.message, true);
    }
  }
    if (action === "play-liked-track") {
    const track = state.likedTracks.find(
      (item) => item.id === Number(actionButton.dataset.trackId),
    );

    if (track) {
      await playTrack(track);
    }
  }

  if (action === "toggle-liked-track") {
    const track = state.likedTracks.find(
      (item) => item.id === Number(actionButton.dataset.trackId),
    );

    if (!track) return;

    try {
      await api(`/api/tracks/${track.id}/like`, {
        method: "PATCH",
      });

      state.likedTracks = state.likedTracks.filter(
        (item) => item.id !== track.id,
      );

      renderLikedSongs();
      showToast("Removed from liked songs.");
    } catch (error) {
      showToast(error.message, true);
    }
  }
  if (action === "toggle-like") {
    const track = state.activePlaylist?.tracks.find(
      (item) => item.id === Number(actionButton.dataset.trackId),
    );

    if (!track) return;

    try {
      const updatedTrack = await api(`/api/tracks/${track.id}/like`, {
        method: "PATCH",
      });

      track.is_liked = updatedTrack.is_liked;
      showToast(track.is_liked ? "Added to liked songs." : "Removed from liked songs.");
      renderPlaylist();
    } catch (error) {
      showToast(error.message, true);
    }
  }
  if (action === "edit-track") {
    const track = state.activePlaylist?.tracks.find((item) => item.id === Number(actionButton.dataset.trackId));
    if (track) openTrackDialog(track);
  }

  if (action === "delete-track") {
    const track = state.activePlaylist?.tracks.find((item) => item.id === Number(actionButton.dataset.trackId));
    if (!track || !window.confirm(`Remove “${track.title}” from this playlist?`)) return;
    try {
      await api(`/api/tracks/${track.id}`, { method: "DELETE" });
      showToast("Song removed.");
      await loadPlaylists(state.activeId);
    } catch (error) { showToast(error.message, true); }
  }

  if (action === "delete-playlist" && state.activePlaylist) {
    if (!window.confirm(`Delete “${state.activePlaylist.name}” and all its songs?`)) return;
    try {
      await api(`/api/playlists/${state.activeId}`, { method: "DELETE" });
      const oldName = state.activePlaylist.name;
      state.activeId = null;
      state.activePlaylist = null;
      showToast(`“${oldName}” was deleted.`);
      await loadPlaylists(null);
    } catch (error) { showToast(error.message, true); }
  }
});

document.addEventListener("change", (event) => {
  const checkbox = event.target.closest("[data-track-select]");
  if (!checkbox) return;
  const trackId = Number(checkbox.dataset.trackSelect);
  if (checkbox.checked) state.selectedTrackIds.add(trackId);
  else state.selectedTrackIds.delete(trackId);
  renderPlaylist();
});

byId("new-playlist-button").addEventListener("click", () => openPlaylistDialog());

byId("song-lab-button").addEventListener("click", () => {
  byId("song-lab-result").hidden = true;
  byId("song-lab-dialog").showModal();
  byId("song-lab-query").focus();
});
byId("song-lab-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  await analyzeSongFromLab();
});

let searchTimer;
playlistSearch.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => loadPlaylists(state.activeId), 220);
});

byId("playlist-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const name = byId("playlist-name").value.trim();
  if (!name) return;
  const editing = state.editingPlaylistId !== null;
  const body = {
    name,
    description: byId("playlist-description").value.trim() || null,
  };
  const selectedCover = byId("playlist-cover-file").files[0];
  const submit = byId("playlist-submit");
  submit.disabled = true;
  try {
    const result = await api(editing ? `/api/playlists/${state.editingPlaylistId}` : "/api/playlists", {
      method: editing ? "PATCH" : "POST",
      body: JSON.stringify(body),
    });
    if (selectedCover) {
      const coverData = new FormData();
      coverData.append("file", selectedCover);
      await api(`/api/playlists/${result.id}/cover`, {
        method: "POST",
        body: coverData,
      });
    }
    byId("playlist-dialog").close();
    showToast(selectedCover ? "Playlist saved with new artwork." : (editing ? "Playlist details saved." : "Playlist made."));
    await loadPlaylists(result.id);
  } catch (error) { showToast(error.message, true); }
  finally { submit.disabled = false; }
});

byId("track-form").addEventListener("submit", async (event) => {
  event.preventDefault();

  if (!state.activePlaylist) return;

  const title = byId("track-title").value.trim();
  const artist = byId("track-artist").value.trim();
  const album = byId("track-album").value.trim();
  const durationValue = byId("track-duration").value.trim();
  const selectedFiles = Array.from(byId("track-file").files);
  const selectedFile = selectedFiles[0];
  const selectedCover = byId("track-cover-file").files[0];
  const editing = state.editingTrackId !== null;

  if (!editing && selectedFiles.length) {
    const submit = byId("track-submit");
    submit.disabled = true;
    submit.textContent = `Importing 0/${selectedFiles.length}…`;

    try {
      let imported = 0;
      const failures = [];
      for (const file of selectedFiles) {
        const formData = new FormData();
        formData.append("file", file);
        formData.append("position", String(state.activePlaylist.tracks.length + imported));
        let metadata = null;
        try {
          const metadataForm = new FormData();
          metadataForm.append("file", file);
          metadata = await api("/api/tracks/metadata", { method: "POST", body: metadataForm });
        } catch {
          metadata = {};
        }

        if (selectedFiles.length === 1) {
          if (title) formData.append("title", title);
          if (artist) formData.append("artist", artist);
          if (album) formData.append("album", album);
          if (durationValue) formData.append("duration_seconds", durationValue);
          if (selectedCover) formData.append("cover", selectedCover);
        }
        if (selectedFiles.length > 1) {
          if (metadata?.title) formData.append("title", metadata.title);
          if (metadata?.artist) formData.append("artist", metadata.artist);
          if (metadata?.album) formData.append("album", metadata.album);
          if (metadata?.duration_seconds) formData.append("duration_seconds", String(metadata.duration_seconds));
        }
        try {
          await api(`/api/playlists/${state.activeId}/tracks/upload`, { method: "POST", body: formData });
          imported += 1;
        } catch (error) {
          failures.push(`${file.name}: ${error.message}`);
        }
        submit.textContent = `Importing ${imported}/${selectedFiles.length}…`;
      }

      byId("track-dialog").close();
      state.trackQuery = "";
      showToast(failures.length ? `${imported} imported, ${failures.length} failed.` : `${imported} songs imported.`);
      await loadPlaylists(state.activeId);
    } catch (error) {
      showToast(error.message, true);
    } finally {
      submit.disabled = false;
      submit.textContent = "Add song";
    }

    return;
  }

  if (!title || !artist) {
    showToast("Add a song title and artist.", true);
    return;
  }

  const body = {
    title,
    artist,
    album: album || null,
    duration_seconds: durationValue ? Number(durationValue) : null,
  };

  if (!editing) {
    body.position = state.activePlaylist.tracks.length;
  }

  const submit = byId("track-submit");
  submit.disabled = true;

  try {
    const result = await api(
      editing
        ? `/api/tracks/${state.editingTrackId}`
        : `/api/playlists/${state.activeId}/tracks`,
      {
        method: editing ? "PATCH" : "POST",
        body: JSON.stringify(body),
      },
    );

    if (selectedCover) {
      const coverData = new FormData();
      coverData.append("file", selectedCover);
      await api(`/api/tracks/${result.id}/cover`, {
        method: "POST",
        body: coverData,
      });
    }

    byId("track-dialog").close();
    state.trackQuery = "";
    showToast(selectedCover ? "Song saved with new artwork." : (editing ? "Song details saved." : "Song added to the playlist."));
    await loadPlaylists(state.activeId);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    submit.disabled = false;
  }
});

byId("track-file").addEventListener("change", async (event) => {
  const selectedFile = event.target.files[0];
  if (!selectedFile) return;

  const formData = new FormData();
  formData.append("file", selectedFile);

  const fallbackTitle = selectedFile.name.replace(/\.[^/.]+$/, "");
  const titleField = byId("track-title");
  const artistField = byId("track-artist");
  const albumField = byId("track-album");
  const durationField = byId("track-duration");

  showToast("Reading the audio details…");

  try {
    const metadata = await api("/api/tracks/metadata", {
      method: "POST",
      body: formData,
    });

    titleField.value = metadata.title || fallbackTitle;
    artistField.value = metadata.artist || "Unknown artist";
    albumField.value = metadata.album || "";
    durationField.value = metadata.duration_seconds || "";

    if (!metadata.duration_seconds) {
      const browserAudio = document.createElement("audio");
      const objectUrl = URL.createObjectURL(selectedFile);

      browserAudio.preload = "metadata";
      browserAudio.src = objectUrl;
      browserAudio.addEventListener("loadedmetadata", () => {
        if (Number.isFinite(browserAudio.duration)) {
          durationField.value = Math.round(browserAudio.duration);
        }
        URL.revokeObjectURL(objectUrl);
      }, { once: true });
    }

    showToast(
      metadata.title
        ? "Song details imported. You can edit them before saving."
        : "File ready. Add or edit any missing details before saving.",
    );
  } catch (error) {
    titleField.value = fallbackTitle;
    artistField.value = "Unknown artist";
    albumField.value = "";
    showToast(error.message, true);
  }
});

document.addEventListener("dragstart", (event) => {
  const item = event.target.closest(".queue-item");
  if (!item) return;
  state.draggingQueueIndex = Number(item.dataset.queueIndex);
  item.classList.add("is-dragging");
});

document.addEventListener("dragend", (event) => {
  const item = event.target.closest(".queue-item");
  if (item) item.classList.remove("is-dragging");
  state.draggingQueueIndex = null;
});

document.addEventListener("dragover", (event) => {
  if (event.target.closest(".queue-item")) event.preventDefault();
});

document.addEventListener("drop", (event) => {
  const target = event.target.closest(".queue-item");
  if (!target || state.draggingQueueIndex === null) return;
  event.preventDefault();
  const targetIndex = Number(target.dataset.queueIndex);
  const [moved] = state.queue.splice(state.draggingQueueIndex, 1);
  state.queue.splice(targetIndex, 0, moved);
  localStorage.setItem("ydkmusic-queue", JSON.stringify(state.queue));
  renderQueue();
});

const savedVolume = Number(localStorage.getItem("sideb-volume"));
audioEngine.volume = Number.isFinite(savedVolume) && savedVolume >= 0 && savedVolume <= 1 ? savedVolume : 0.8;
byId("player-volume").value = audioEngine.volume;
updatePlayerDisplay();

audioEngine.addEventListener("timeupdate", () => {
  if (!audioEngine.paused && playerTrack) {
    const currentTime = audioEngine.currentTime;
    const previousTime = state.lastPlaybackTime;
    if (previousTime !== null && currentTime >= previousTime && currentTime - previousTime <= 1.5) {
      state.listenProgress += currentTime - previousTime;
    }
    state.lastPlaybackTime = currentTime;

    if (state.listenProgress >= 30 && state.countedListenTrackId !== playerTrack.id) {
      state.countedListenTrackId = playerTrack.id;
      api(`/api/tracks/${playerTrack.id}/played`, { method: "POST" })
        .then((playedTrack) => {
          playerTrack = playedTrack;
          if (state.activePlaylist?.tracks) state.activePlaylist.tracks = state.activePlaylist.tracks.map((item) => item.id === playedTrack.id ? playedTrack : item);
          state.likedTracks = (state.likedTracks || []).map((item) => item.id === playedTrack.id ? playedTrack : item);
          renderPlaylist();
        })
        .catch(() => { state.countedListenTrackId = null; });
    }
  }
  updatePlayerDisplay();
  syncLyrics();
});

audioEngine.addEventListener("loadedmetadata", () => {
  updatePlayerDisplay();
});

audioEngine.addEventListener("play", () => {
  startLyricsClock();
  updateMediaSessionState();
  updatePlayerDisplay();
  renderPlaylist();
});

audioEngine.addEventListener("pause", () => {
  stopLyricsClock();
  updateMediaSessionState();
  updatePlayerDisplay();
  renderPlaylist();
});

audioEngine.addEventListener("ended", () => {
  stopLyricsClock();
  playNextTrack();
});

configureMediaSession();
visualizer.hidden = true;
drawVisualizer();

byId("player-play").addEventListener("click", async () => {
  if (!playerTrack) return;

  if (audioEngine.paused) {
    await audioEngine.play();
  } else {
    audioEngine.pause();
  }

  updatePlayerDisplay();
  renderPlaylist();
});

byId("player-shuffle").addEventListener("click", () => {
  state.shuffle = !state.shuffle;
  updatePlayerDisplay();
  showToast(state.shuffle ? "Shuffle on." : "Shuffle off.");
});

byId("player-repeat").addEventListener("click", () => {
  state.repeat = !state.repeat;
  updatePlayerDisplay();
  showToast(state.repeat ? "Repeat on." : "Repeat off.");
});

byId("player-progress").addEventListener("input", (event) => {
  audioEngine.currentTime = Number(event.target.value);
  updatePlayerDisplay();
});

byId("player-volume").addEventListener("input", (event) => {
  audioEngine.volume = Number(event.target.value);
  audioEngine.muted = false;
  localStorage.setItem("sideb-volume", String(audioEngine.volume));
  updatePlayerDisplay();
});

byId("player-mute").addEventListener("click", () => {
  if (audioEngine.muted || audioEngine.volume === 0) {
    audioEngine.muted = false;
    if (audioEngine.volume === 0) {
      audioEngine.volume = Number(localStorage.getItem("sideb-last-volume")) || 0.8;
      byId("player-volume").value = audioEngine.volume;
    }
  } else {
    localStorage.setItem("sideb-last-volume", String(audioEngine.volume));
    audioEngine.muted = true;
  }
  updatePlayerDisplay();
});

byId("player-close").addEventListener("click", () => {
  audioEngine.pause();
  audioEngine.removeAttribute("src");
  audioEngine.load();
  playerTrack = null;
  byId("player-bar").hidden = true;
  renderPlaylist();
});

byId("player-art").addEventListener("click", () => {
  if (playerTrack) updateNowPlayingDisplay();
});

byId("now-playing-close").addEventListener("click", () => {
  closeNowPlaying();
});

byId("lyrics-edit").addEventListener("click", () => {
  if (!playerTrack) return;
  byId("lyrics-input").value = playerTrack.lyrics || "";
  byId("lyrics-view").hidden = true;
  byId("lyrics-editor").hidden = false;
  byId("lyrics-input").focus();
});

byId("lyrics-toggle").addEventListener("click", () => {
  const panel = byId("lyrics-panel");
  const isOpen = !panel.hidden;
  panel.hidden = isOpen;
  byId("lyrics-toggle").setAttribute("aria-expanded", String(!isOpen));
  if (!isOpen) syncLyrics();
});

byId("lyrics-close").addEventListener("click", closeLyricsPanel);

byId("lyrics-mark-start").addEventListener("click", () => {
  if (!playerTrack) return;
  const firstLine = [...document.querySelectorAll("#lyrics-view p[data-lyric-time]")]
    .sort((a, b) => Number(a.dataset.lyricTime) - Number(b.dataset.lyricTime))[0];
  if (!firstLine) {
    showToast("These lyrics do not have a line to sync yet.", true);
    return;
  }

  const firstLineTime = Number(firstLine.dataset.lyricTime);
  const automaticOffset = state.lyricUsesEstimates ? state.lyricOffset : 0;
  state.lyricManualOffset = Math.round((firstLineTime - audioEngine.currentTime + automaticOffset) * 10) / 10;
  state.lyricManualOffset = Math.max(-60, Math.min(60, state.lyricManualOffset));
  localStorage.setItem(`sideb-lyrics-manual-offset-${playerTrack.id}`, String(state.lyricManualOffset));
  updateLyricsTimingDisplay();
  syncLyrics();
  const lyricLines = [...document.querySelectorAll("#lyrics-view p[data-lyric-time]")]
    .sort((a, b) => Number(a.dataset.lyricTime) - Number(b.dataset.lyricTime));
  lyricLines.forEach((line) => line.classList.toggle("is-current", line === firstLine));
  state.syncedLyricLine = firstLine;
  firstLine.scrollIntoView({ behavior: "smooth", block: "center" });
  showToast("First line anchored and saved for this song.");
});

byId("lyrics-fetch").addEventListener("click", async () => {
  if (!playerTrack) return;
  const fetchButton = byId("lyrics-fetch");
  fetchButton.disabled = true;
  fetchButton.textContent = "Searching…";
  try {
    const result = await api(`/api/tracks/${playerTrack.id}/lyrics`);
    const updatedTrack = await api(`/api/tracks/${playerTrack.id}`, {
      method: "PATCH",
      body: JSON.stringify({ lyrics: result.lyrics }),
    });
    playerTrack = updatedTrack;
    renderLyrics();
    updateNowPlayingDisplay();
    await loadPlaylists(state.activeId);
    showToast(`Lyrics found${result.source ? ` via ${result.source}` : ""} and saved.`);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    fetchButton.disabled = false;
    fetchButton.textContent = "Find lyrics";
  }
});

byId("lyrics-sync").addEventListener("click", async () => {
  if (!playerTrack) return;
  sessionStorage.removeItem(`sideb-lyrics-offset-${playerTrack.id}`);
  state.lyricOffset = 0;
  await analyzeAudioLyricsOffset(playerTrack);
});

byId("lyrics-offset-down").addEventListener("click", () => {
  if (!playerTrack) return;
  state.lyricManualOffset = Math.max(-60, Math.min(60, Math.round((state.lyricManualOffset + .1) * 10) / 10));
  localStorage.setItem(`sideb-lyrics-manual-offset-${playerTrack.id}`, String(state.lyricManualOffset));
  updateLyricsTimingDisplay();
  syncLyrics();
});

byId("lyrics-offset-up").addEventListener("click", () => {
  if (!playerTrack) return;
  state.lyricManualOffset = Math.max(-60, Math.min(60, Math.round((state.lyricManualOffset - .1) * 10) / 10));
  localStorage.setItem(`sideb-lyrics-manual-offset-${playerTrack.id}`, String(state.lyricManualOffset));
  updateLyricsTimingDisplay();
  syncLyrics();
});

byId("lyrics-offset-reset").addEventListener("click", () => {
  if (!playerTrack) return;
  state.lyricManualOffset = 0;
  localStorage.removeItem(`sideb-lyrics-manual-offset-${playerTrack.id}`);
  updateLyricsTimingDisplay();
  syncLyrics();
});

byId("lyrics-cancel").addEventListener("click", () => {
  byId("lyrics-editor").hidden = true;
  byId("lyrics-view").hidden = false;
});

byId("lyrics-save").addEventListener("click", async () => {
  if (!playerTrack) return;
  const saveButton = byId("lyrics-save");
  saveButton.disabled = true;
  try {
    const updatedTrack = await api(`/api/tracks/${playerTrack.id}`, {
      method: "PATCH",
      body: JSON.stringify({ lyrics: byId("lyrics-input").value.trim() || null }),
    });
    playerTrack = updatedTrack;
    byId("lyrics-editor").hidden = true;
    byId("lyrics-view").hidden = false;
    renderLyrics();
    updateNowPlayingDisplay();
    await loadPlaylists(state.activeId);
    showToast("Lyrics saved.");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    saveButton.disabled = false;
  }
});

byId("now-playing-backdrop").addEventListener("click", () => {
  closeNowPlaying();
});

byId("now-playing-toggle").addEventListener("click", async () => {
  if (!playerTrack) return;

  if (audioEngine.paused) await audioEngine.play();
  else audioEngine.pause();

  updateNowPlayingControls();
});

byId("now-playing-progress").addEventListener("input", (event) => {
  audioEngine.currentTime = Number(event.target.value);
  updateNowPlayingControls();
});

byId("now-playing-shuffle").addEventListener("click", () => {
  state.shuffle = !state.shuffle;
  updatePlayerDisplay();
  updateNowPlayingControls();
  showToast(state.shuffle ? "Shuffle on." : "Shuffle off.");
});

byId("now-playing-repeat").addEventListener("click", () => {
  state.repeat = !state.repeat;
  updatePlayerDisplay();
  updateNowPlayingControls();
  showToast(state.repeat ? "Repeat on." : "Repeat off.");
});

byId("now-playing-next").addEventListener("click", async () => {
  await playNextTrack();
  updateNowPlayingControls();
});

byId("now-playing-previous").addEventListener("click", async () => {
  if (!playerTrack) return;

  if (audioEngine.currentTime > 3) {
    audioEngine.currentTime = 0;
    updateNowPlayingControls();
    return;
  }

  const tracks = playbackTracks().filter((track) => track.audio_url);
  const currentIndex = tracks.findIndex((track) => track.id === playerTrack.id);
  const previousTrack = tracks[currentIndex - 1];

  if (previousTrack) await playTrack(previousTrack);
  else audioEngine.currentTime = 0;

  updateNowPlayingControls();
});

byId("now-playing-like").addEventListener("click", async () => {
  if (!playerTrack) return;

  try {
    const updatedTrack = await api(`/api/tracks/${playerTrack.id}/like`, {
      method: "PATCH",
    });

    playerTrack.is_liked = updatedTrack.is_liked;

    const activeTrack = state.activePlaylist?.tracks?.find(
      (track) => track.id === playerTrack.id,
    );
    if (activeTrack) activeTrack.is_liked = playerTrack.is_liked;

    for (const playlist of state.playlists) {
      const matchingTrack = playlist.tracks?.find((track) => track.id === playerTrack.id);
      if (matchingTrack) matchingTrack.is_liked = playerTrack.is_liked;
    }

    const likedTrack = state.likedTracks.find((track) => track.id === playerTrack.id);
    if (likedTrack) likedTrack.is_liked = playerTrack.is_liked;

    updateNowPlayingControls();
    renderPlaylist();
    showToast(playerTrack.is_liked ? "Added to liked songs." : "Removed from liked songs.");
  } catch (error) {
    showToast(error.message, true);
  }
});

byId("now-playing-volume").addEventListener("input", (event) => {
  audioEngine.volume = Number(event.target.value);
  audioEngine.muted = false;
  byId("player-volume").value = audioEngine.volume;
  localStorage.setItem("sideb-volume", String(audioEngine.volume));
  updatePlayerDisplay();
  updateNowPlayingControls();
});

byId("now-playing-mute").addEventListener("click", () => {
  byId("player-mute").click();
  updateNowPlayingControls();
});

document.addEventListener("keydown", async (event) => {
  if (event.key === "Escape" && !byId("lyrics-panel").hidden) {
    closeLyricsPanel();
    return;
  }

  if (event.key === "Escape" && !byId("now-playing").hidden) {
    closeNowPlaying();
    return;
  }

  if (!playerTrack || event.target.closest("input, textarea, select, dialog[open]")) return;
  const key = event.key.toLocaleLowerCase();
  const duration = audioEngine.duration || Number(playerTrack.duration_seconds) || 0;

  if (event.code === "Space") {
    event.preventDefault();
    if (audioEngine.paused) await audioEngine.play();
    else audioEngine.pause();
    updatePlayerDisplay();
    return;
  }

  if (key === "m") {
    event.preventDefault();
    byId("player-mute").click();
    updateNowPlayingControls();
    return;
  }

  if (key === "s") {
    event.preventDefault();
    byId("player-shuffle").click();
    updateNowPlayingControls();
    return;
  }

  if (key === "r") {
    event.preventDefault();
    byId("player-repeat").click();
    updateNowPlayingControls();
    return;
  }

  if (key === "l" && !byId("now-playing").hidden) {
    event.preventDefault();
    byId("now-playing-like").click();
    return;
  }

  if (key === "o" && byId("now-playing").hidden && playerTrack) {
    event.preventDefault();
    updateNowPlayingDisplay();
    return;
  }

  if (key === "n") {
    event.preventDefault();
    await playNextTrack();
    updateNowPlayingControls();
    return;
  }

  if (key === "p") {
    event.preventDefault();
    if (audioEngine.currentTime > 3) {
      audioEngine.currentTime = 0;
    } else {
      const tracks = playbackTracks().filter((track) => track.audio_url);
      const currentIndex = tracks.findIndex((track) => track.id === playerTrack.id);
      const previousTrack = tracks[currentIndex - 1];
      if (previousTrack) await playTrack(previousTrack);
      else audioEngine.currentTime = 0;
    }
    updatePlayerDisplay();
    updateNowPlayingControls();
    return;
  }

  if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
    event.preventDefault();
    const shift = event.shiftKey
      ? (event.key === "ArrowLeft" ? -15 : 15)
      : (event.key === "ArrowLeft" ? -5 : 5);
    audioEngine.currentTime = Math.max(0, Math.min(
      duration,
      audioEngine.currentTime + shift,
    ));
    updatePlayerDisplay();
    updateNowPlayingControls();
    return;
  }

  if (event.key === "ArrowUp" || event.key === "ArrowDown") {
    event.preventDefault();
    const shift = event.key === "ArrowUp" ? 0.05 : -0.05;
    audioEngine.volume = Math.max(0, Math.min(1, audioEngine.volume + shift));
    audioEngine.muted = false;
    byId("player-volume").value = audioEngine.volume;
    byId("now-playing-volume").value = audioEngine.volume;
    localStorage.setItem("sideb-volume", String(audioEngine.volume));
    updatePlayerDisplay();
    updateNowPlayingControls();
  }
});

byId("sidebar-toggle").addEventListener("click", () => {
  setSidebarCollapsed(!document.body.classList.contains("sidebar-collapsed"));
});
byId("sidebar-reopen").addEventListener("click", () => setSidebarCollapsed(false));
setSidebarCollapsed(savedSidebarState);
loadPlaylists(null);


let draggedTrackId = null;
let draggedScope = null;

document.addEventListener("dragstart", (event) => {
  const row = event.target.closest("[data-track-id][draggable='true']");
  if (!row) return;

  draggedTrackId = Number(row.dataset.trackId);
  draggedScope = row.dataset.dragScope;
  row.classList.add("is-dragging");

  event.dataTransfer.effectAllowed = "move";
  event.dataTransfer.setData("text/plain", String(draggedTrackId));
});

document.addEventListener("dragend", (event) => {
  const row = event.target.closest("[data-track-id][draggable='true']");
  if (row) row.classList.remove("is-dragging");

  document
    .querySelectorAll(".drag-over")
    .forEach((item) => item.classList.remove("drag-over"));

  draggedTrackId = null;
  draggedScope = null;
});

document.addEventListener("dragover", (event) => {
  const row = event.target.closest("[data-track-id][draggable='true']");
  if (!row) return;

  event.preventDefault();
  row.classList.add("drag-over");
});

document.addEventListener("dragleave", (event) => {
  const row = event.target.closest("[data-track-id][draggable='true']");
  if (row) row.classList.remove("drag-over");
});

document.addEventListener("drop", async (event) => {
  const targetRow = event.target.closest("[data-track-id][draggable='true']");
  if (!targetRow || draggedTrackId === null) return;

  event.preventDefault();
  targetRow.classList.remove("drag-over");

  const targetScope = targetRow.dataset.dragScope;
  const targetTrackId = Number(targetRow.dataset.trackId);

  if (draggedScope !== targetScope || draggedTrackId === targetTrackId) {
    return;
  }

  const container = targetRow.parentElement;
  const draggedRow = container.querySelector(
    `[data-track-id="${draggedTrackId}"]`,
  );

  if (!draggedRow) return;

  const rows = [...container.querySelectorAll("[data-track-id]")];
  const draggedIndex = rows.indexOf(draggedRow);
  const targetIndex = rows.indexOf(targetRow);

  if (draggedIndex < targetIndex) {
    targetRow.after(draggedRow);
  } else {
    targetRow.before(draggedRow);
  }

  const orderedIds = [
    ...container.querySelectorAll("[data-track-id]"),
  ].map((row) => Number(row.dataset.trackId));

  try {
    if (targetScope === "playlist") {
      await api(`/api/playlists/${state.activeId}/tracks/reorder`, {
        method: "PATCH",
        body: JSON.stringify(orderedIds),
      });

      const tracksById = new Map(
        state.activePlaylist.tracks.map((track) => [track.id, track]),
      );

      state.activePlaylist.tracks = orderedIds
        .map((id) => tracksById.get(id))
        .filter(Boolean);

      showToast("Playlist order saved.");
    }

    if (targetScope === "liked") {
      await api("/api/tracks/liked/reorder", {
        method: "PATCH",
        body: JSON.stringify(orderedIds),
      });

      const tracksById = new Map(
        state.likedTracks.map((track) => [track.id, track]),
      );

      state.likedTracks = orderedIds
        .map((id) => tracksById.get(id))
        .filter(Boolean);

      showToast("Liked songs order saved.");
    }
  } catch (error) {
    showToast(error.message, true);

    if (targetScope === "liked") {
      await loadLikedSongs();
    } else {
      await loadPlaylist(state.activeId);
    }
  }
});
