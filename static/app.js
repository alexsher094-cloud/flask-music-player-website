/* ============================================================
   STATE
   ============================================================ */
const FAV_KEY   = "alpha-music-favorites";
const EQ_KEY    = "alpha-music-eq";
const THEME_KEY = "alpha-music-theme";
const THEME_VERSION = 3;

const state = {
    tracks: [],
    filteredTracks: [],
    albums: [],
    filteredAlbums: [],
    artists: [],
    filteredArtists: [],
    currentAlbumKey: null,
    currentArtistKey: null,
    albumBackTarget: "albums",
    albumBackArtistKey: null,
    currentTrackId: null,
    currentTrack: null,
    switchingTrack: false,
    libVersion: -1,   // -1 = ещё ни один ответ /api/tracks не применяли
    currentQueue: [],
    isPlaying: false,
    isSeeking: false,
    volume: 0.7,
    shuffle: false,
    repeat: "off",
    shuffleOrder: [],
    pendingShuffle: null,
    view: "all",
    search: "",
    favorites: new Set(JSON.parse(localStorage.getItem(FAV_KEY) || "[]")),
    eq: {
        enabled: true,
        preset: "flat",
        gains: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    },
    theme: {
        preset: "classic",
        accent: "#a855f7",
        brightness: 20,
        radius: 8,
        animations: true,
    },
};

/* ============================================================
   WINDOWS API (pywebview)
   ============================================================ */
function winApi(name) {
    return (window.pywebview && window.pywebview.api && window.pywebview.api[name]) || null;
}

/* ============================================================
   DISCORD RICH PRESENCE
   ============================================================ */
// Отправляем снимок только на событиях (старт/пауза/seek), а не каждую секунду:
// Discord сам считает таймер по start/end.
function syncPresence() {
    const api = winApi("update_presence");
    const track = state.currentTrack;
    if (!api || !track) return;

    const duration = Number.isFinite(audio.duration) && audio.duration > 0
        ? audio.duration
        : (track.duration || 0);

    api({
        title: track.title,
        artist: track.artist,
        album: track.album,
        duration,
        position: Number.isFinite(audio.currentTime) ? audio.currentTime : 0,
        playing: !audio.paused && !audio.ended,
    }).catch(() => {});
}

function clearPresence() {
    const api = winApi("clear_presence");
    if (api) api().catch(() => {});
}

/* ============================================================
   COVER URLS
   ============================================================ */
// s= округляется сервером вверх до 64/128/256/512/1024;
// v= хэш обложки (track.cover) — вечный кэш браузера,
//    без v сервер отвечает 304/200 по свежести файла.
function coverUrl(id, size = 512, v = "") {
    return `/api/cover/${id}?s=${size}${v ? `&v=${v}` : ""}`;
}

/* ============================================================
   ТЕМЫ
   ============================================================ */
const THEMES = {
    classic: {
        label: "Classic", hue: 240, sat: 8,
        colors: {
            bg: 4.3, bgElev: 7.6, surface: 10.2, hover: 14.9, active: 17.8,
            border: 14.9, border2: 19.6,
            text: 100, text2: 68, text3: 45.5,
        },
        preview: { sidebar: "#0a0a0c", surface: "#18181c", accent: "#a855f7" },
    },
    dark: {
        label: "Dark", hue: 258, sat: 0,
        colors: { bg: 8, bgElev: 14, surface: 20, hover: 30, active: 40, text: 98, text2: 70, text3: 45 },
        preview: { sidebar: "#141418", surface: "#1a1a1f", accent: "#a855f7" },
    },
    midnight: {
        label: "Midnight", hue: 230, sat: 25,
        colors: { bg: 6, bgElev: 10, surface: 14, hover: 20, active: 26, text: 96, text2: 70, text3: 50 },
        preview: { sidebar: "#0d1120", surface: "#131828", accent: "#6366f1" },
    },
    dracula: {
        label: "Dracula", hue: 260, sat: 20,
        colors: { bg: 12, bgElev: 16, surface: 20, hover: 26, active: 32, text: 96, text2: 72, text3: 50 },
        preview: { sidebar: "#1a1625", surface: "#231e30", accent: "#bd93f9" },
    },
    nord: {
        label: "Nord", hue: 210, sat: 18,
        colors: { bg: 14, bgElev: 18, surface: 22, hover: 28, active: 34, text: 94, text2: 72, text3: 52 },
        preview: { sidebar: "#2e3440", surface: "#3b4252", accent: "#88c0d0" },
    },
    amoled: {
        label: "AMOLED", hue: 0, sat: 0,
        colors: { bg: 0, bgElev: 3, surface: 6, hover: 10, active: 14, text: 98, text2: 65, text3: 40 },
        preview: { sidebar: "#000000", surface: "#0a0a0a", accent: "#a855f7" },
    },
    forest: {
        label: "Forest", hue: 140, sat: 20,
        colors: { bg: 10, bgElev: 14, surface: 18, hover: 24, active: 30, text: 96, text2: 70, text3: 48 },
        preview: { sidebar: "#0d1a12", surface: "#142018", accent: "#22c55e" },
    },
    sunset: {
        label: "Sunset", hue: 20, sat: 25,
        colors: { bg: 12, bgElev: 16, surface: 20, hover: 26, active: 32, text: 97, text2: 72, text3: 52 },
        preview: { sidebar: "#1f1611", surface: "#2a1e18", accent: "#f97316" },
    },
    light: {
        label: "Light",
        hue: 240, sat: 10,
        colors: {
            bg: 82, bgElev: 94, surface: 100, hover: 92, active: 84,
            border: 72, border2: 62,
            text: 8, text2: 30, text3: 52,
        },
        preview: { sidebar: "#d1d1d6", surface: "#ffffff", accent: "#a855f7" },
        isLight: true,
    },
    sepia: {
        label: "Sepia",
        hue: 32, sat: 30,
        colors: {
            bg: 76, bgElev: 92, surface: 98, hover: 89, active: 78,
            border: 68, border2: 58,
            text: 15, text2: 38, text3: 55,
        },
        preview: { sidebar: "#d4c9b0", surface: "#fdf8ec", accent: "#b45309" },
        isLight: true,
    },
    gray: {
        label: "Gray",
        hue: 0, sat: 0,
        colors: {
            bg: 74, bgElev: 90, surface: 96, hover: 86, active: 76,
            border: 66, border2: 56,
            text: 10, text2: 32, text3: 50,
        },
        preview: { sidebar: "#c8c8c8", surface: "#f5f5f5", accent: "#6b7280" },
        isLight: true,
    },
};

/* ============================================================
   ЗАГРУЗКА ИЗ LOCALSTORAGE
   ============================================================ */
try {
    const saved = JSON.parse(localStorage.getItem(THEME_KEY) || "{}");
    if (saved.version === THEME_VERSION) {
        if (saved.preset && THEMES[saved.preset]) state.theme.preset = saved.preset;
        if (saved.accent) state.theme.accent = saved.accent;
        if (typeof saved.brightness === "number") state.theme.brightness = saved.brightness;
        if (typeof saved.radius === "number") state.theme.radius = saved.radius;
        if (saved.animations !== undefined) state.theme.animations = saved.animations;
    }
} catch {}

try {
    const savedEq = JSON.parse(localStorage.getItem(EQ_KEY) || "{}");
    if (savedEq.enabled !== undefined) state.eq.enabled = savedEq.enabled;
    if (savedEq.preset) state.eq.preset = savedEq.preset;
    if (Array.isArray(savedEq.gains) && savedEq.gains.length === 10) {
        state.eq.gains = savedEq.gains.map(v => {
            const n = parseFloat(v);
            return isNaN(n) ? 0 : n;
        });
    }
} catch {}

try {
    if (localStorage.getItem("alpha-music-shuffle") === "1") state.shuffle = true;
    const savedRepeat = localStorage.getItem("alpha-music-repeat");
    if (savedRepeat && ["off", "all", "one"].includes(savedRepeat)) {
        state.repeat = savedRepeat;
    }
} catch {}

try {
    const savedVolume = localStorage.getItem("alpha-music-volume");
    if (savedVolume !== null) {
        const v = parseFloat(savedVolume);
        if (!isNaN(v) && v >= 0 && v <= 1) {
            state.volume = v;
        }
    }
} catch {}

// AudioEngine (static/audio-engine.js): два <audio> для gapless, ReplayGain, выбор устройства.
// Повторяет нужную часть интерфейса HTMLAudioElement, поэтому остальной код не меняется.
const audio = new AudioEngine(document.getElementById("audio"));
audio.volume = state.volume;

/* ============================================================
   ТЕМА
   ============================================================ */
function hsl(h, s, l) { return `hsl(${h}, ${s}%, ${l}%)`; }

function applyTheme() {
    const t = state.theme;
    const preset = THEMES[t.preset] || THEMES.classic;
    const root = document.documentElement;
    const body = document.body;

    const accent = t.accent;
    root.style.setProperty("--accent", accent);
    root.style.setProperty("--accent-hover", shadeColor(accent, 10));
    root.style.setProperty("--accent-glow", hexToRgba(accent, 0.35));
    root.style.setProperty("--accent-2", shadeColor(accent, -15));

    const c = preset.colors;
    const hue = preset.hue;
    const sat = preset.sat;

    const brightnessMul = t.brightness / 20;
    const adjustL = (l) => {
        if (preset.isLight) {
            const norm = (t.brightness - 20) / 80;
            if (norm >= 0) {
                return Math.min(100, l + (100 - l) * norm * 0.5);
            }
            return Math.max(0, l - l * (-norm) * 0.5);
        }
        return Math.max(0, Math.min(50, l * brightnessMul));
    };

    root.style.setProperty("--bg",         hsl(hue, sat, adjustL(c.bg)));
    root.style.setProperty("--bg-elev",    hsl(hue, sat, adjustL(c.bgElev)));
    root.style.setProperty("--bg-surface", hsl(hue, sat, adjustL(c.surface)));
    root.style.setProperty("--bg-hover",   hsl(hue, sat, adjustL(c.hover)));
    root.style.setProperty("--bg-active",  hsl(hue, sat, adjustL(c.active)));

    const borderL  = c.border  !== undefined ? c.border  : (c.text > 50 ? 88 : Math.min(45, c.active + 8));
    const border2L = c.border2 !== undefined ? c.border2 : borderL + 5;
    root.style.setProperty("--border",   hsl(hue, sat * 0.5, adjustL(borderL)));
    root.style.setProperty("--border-2", hsl(hue, sat * 0.5, adjustL(border2L)));

    root.style.setProperty("--text",   hsl(hue, sat * 0.3, c.text));
    root.style.setProperty("--text-2", hsl(hue, sat * 0.3, c.text2));
    root.style.setProperty("--text-3", hsl(hue, sat * 0.3, c.text3));

    root.style.setProperty("--radius", t.radius + "px");

    if (preset.isLight) {
        root.style.setProperty("--shadow-sm",  "0 1px 3px rgba(0, 0, 0, 0.06)");
        root.style.setProperty("--shadow",     "0 8px 24px rgba(0, 0, 0, 0.10)");
        root.style.setProperty("--shadow-lg",  "0 24px 48px rgba(0, 0, 0, 0.14)");
        root.style.setProperty("--shadow-img", "0 8px 24px rgba(0, 0, 0, 0.12)");
        root.style.setProperty("--overlay",    "rgba(15, 15, 20, 0.40)");
    } else {
        root.style.setProperty("--shadow-sm",  "0 1px 2px rgba(0, 0, 0, 0.40)");
        root.style.setProperty("--shadow",     "0 8px 24px rgba(0, 0, 0, 0.45)");
        root.style.setProperty("--shadow-lg",  "0 24px 48px rgba(0, 0, 0, 0.60)");
        root.style.setProperty("--shadow-img", "0 8px 24px rgba(0, 0, 0, 0.50)");
        root.style.setProperty("--overlay",    "rgba(0, 0, 0, 0.65)");
    }

    body.classList.toggle("theme-light", !!preset.isLight);
    body.classList.toggle("no-animations", !t.animations);

    syncThemeUI();
    syncNativeBackground();
}

function shadeColor(hex, percent) {
    let r = parseInt(hex.slice(1, 3), 16);
    let g = parseInt(hex.slice(3, 5), 16);
    let b = parseInt(hex.slice(5, 7), 16);
    r = Math.min(255, Math.max(0, r + Math.round(255 * percent / 100)));
    g = Math.min(255, Math.max(0, g + Math.round(255 * percent / 100)));
    b = Math.min(255, Math.max(0, b + Math.round(255 * percent / 100)));
    return "#" + [r, g, b].map(x => x.toString(16).padStart(2, "0")).join("");
}

function hexToRgba(hex, alpha) {
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function saveTheme() {
    localStorage.setItem(THEME_KEY, JSON.stringify({
        ...state.theme,
        version: THEME_VERSION,
    }));
}

function syncThemeUI() {
    document.querySelectorAll(".theme-preset").forEach(el => {
        el.classList.toggle("active", el.dataset.preset === state.theme.preset);
    });

    const accentInput = $("#theme-accent");
    if (accentInput) accentInput.value = state.theme.accent;
    const accentVal = $("#theme-accent-value");
    if (accentVal) accentVal.textContent = state.theme.accent.toUpperCase();

    const bInput = $("#theme-brightness");
    if (bInput) bInput.value = state.theme.brightness;
    const bVal = $("#theme-brightness-value");
    if (bVal) bVal.textContent = state.theme.brightness;

    const rInput = $("#theme-radius");
    if (rInput) rInput.value = state.theme.radius;
    const rVal = $("#theme-radius-value");
    if (rVal) rVal.textContent = state.theme.radius + "px";

    const aInput = $("#theme-animations");
    if (aInput) aInput.checked = state.theme.animations;
}

function buildThemePresets() {
    const container = $("#theme-presets");
    if (!container) return;
    container.innerHTML = "";

    Object.entries(THEMES).forEach(([key, theme]) => {
        const el = document.createElement("button");
        el.className = "theme-preset";
        el.dataset.preset = key;

        el.innerHTML = `
            <div class="theme-preset-preview" style="
                background: ${theme.preview.sidebar};
                --preset-sidebar: ${theme.preview.sidebar};
                --preset-surface: ${theme.preview.surface};
                --preset-accent: ${theme.preview.accent};
            ">
                <div class="theme-preset-dot"></div>
            </div>
            <div class="theme-preset-name">${theme.label}</div>
        `;

        el.addEventListener("click", () => {
            state.theme.preset = key;
            state.theme.accent = theme.preview.accent;
            applyTheme();
            saveTheme();
        });

        container.appendChild(el);
    });
}

function initTheme() {
    buildThemePresets();
    applyTheme();

    const accentInput = $("#theme-accent");
    if (accentInput) {
        accentInput.addEventListener("input", (e) => {
            state.theme.accent = e.target.value;
            applyTheme();
            saveTheme();
        });
    }

    const bInput = $("#theme-brightness");
    if (bInput) {
        bInput.addEventListener("input", (e) => {
            state.theme.brightness = parseInt(e.target.value);
            applyTheme();
            saveTheme();
        });
    }

    const rInput = $("#theme-radius");
    if (rInput) {
        rInput.addEventListener("input", (e) => {
            state.theme.radius = parseInt(e.target.value);
            applyTheme();
            saveTheme();
        });
    }

    const aInput = $("#theme-animations");
    if (aInput) {
        aInput.addEventListener("change", (e) => {
            state.theme.animations = e.target.checked;
            applyTheme();
            saveTheme();
        });
    }

    const resetBtn = $("#theme-reset");
    if (resetBtn) {
        resetBtn.addEventListener("click", () => {
            state.theme = {
                preset: "classic",
                accent: "#a855f7",
                brightness: 20,
                radius: 8,
                animations: true,
            };
            applyTheme();
            saveTheme();
        });
    }
}

/* ============================================================
   EQUALIZER
   ============================================================ */
const EQ_BANDS = [
    { freq: 31,    type: "lowshelf",  label: "31 Hz" },
    { freq: 62,    type: "peaking",   label: "62 Hz" },
    { freq: 125,   type: "peaking",   label: "125 Hz" },
    { freq: 250,   type: "peaking",   label: "250 Hz" },
    { freq: 500,   type: "peaking",   label: "500 Hz" },
    { freq: 1000,  type: "peaking",   label: "1 kHz" },
    { freq: 2000,  type: "peaking",   label: "2 kHz" },
    { freq: 4000,  type: "peaking",   label: "4 kHz" },
    { freq: 8000,  type: "peaking",   label: "8 kHz" },
    { freq: 16000, type: "highshelf", label: "16 kHz" },
];

const EQ_PRESETS = {
    flat:       { label: "Flat",       gains: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0] },
    pop:        { label: "Pop",        gains: [-1, -1, 0, 2, 4, 4, 2, 0, -1, -1] },
    rock:       { label: "Rock",       gains: [4, 3, 2, 0, -1, -1, 1, 2, 3, 4] },
    jazz:       { label: "Jazz",       gains: [3, 2, 1, 1, 0, 0, 1, 1, 2, 3] },
    classical:  { label: "Classical",  gains: [4, 3, 2, 1, 0, 0, 0, 1, 2, 4] },
    electronic: { label: "Electronic", gains: [4, 3, 1, 0, 0, 0, 1, 2, 4, 5] },
    bass:       { label: "Bass Boost", gains: [9, 8, 6, 3, 1, 0, 0, 0, 0, 0] },
    treble:     { label: "Treble",     gains: [0, 0, 0, 0, 0, 1, 3, 5, 7, 9] },
    vocal:      { label: "Vocal",      gains: [-3, -2, -1, 1, 3, 4, 3, 1, 0, -1] },
    loudness:   { label: "Loudness",   gains: [7, 5, 0, -1, -2, -1, 0, 3, 5, 7] },
    night:      { label: "Night",      gains: [-4, -3, -1, 1, 3, 3, 2, 0, -2, -4] },
};

let audioCtx = null;
let eqFilters = [];

// AudioContext и слоты (A/B → ReplayGain-gain → audio.bus) создаёт AudioEngine;
// здесь к его шине подключается эквалайзер, а за ним — выход.
function ensureAudioContext() {
    const ctx = audio.ensureContext();
    if (!ctx || audioCtx) return;
    audioCtx = ctx;
    try {
        eqFilters = EQ_BANDS.map((band, i) => {
            const filter = audioCtx.createBiquadFilter();
            filter.type = band.type;
            filter.frequency.value = band.freq;
            filter.Q.value = 1;
            filter.gain.value = state.eq.enabled ? (state.eq.gains[i] || 0) : 0;
            return filter;
        });
        let node = audio.bus;
        eqFilters.forEach(f => { node.connect(f); node = f; });
        node.connect(audioCtx.destination);
    } catch (e) {
        console.error("EQ init failed:", e);
        eqFilters = [];
        try { audio.bus.disconnect(); audio.bus.connect(audioCtx.destination); } catch {}
    }
}

function applyEQGains() {
    if (!eqFilters.length) return;
    const useGains = state.eq.enabled;
    eqFilters.forEach((f, i) => {
        const target = useGains ? (state.eq.gains[i] || 0) : 0;
        f.gain.setTargetAtTime(target, audioCtx.currentTime, 0.01);
    });
}

function saveEQ() {
    localStorage.setItem(EQ_KEY, JSON.stringify(state.eq));
}

function formatGain(v) {
    if (v === 0) return "0";
    const sign = v > 0 ? "+" : "";
    const num = Number.isInteger(v) ? v : v.toFixed(1);
    return sign + num;
}

/* ============================================================
   ХЕЛПЕРЫ
   ============================================================ */
const $  = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

function formatTime(seconds) {
    if (!seconds || isNaN(seconds)) return "0:00";
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m}:${s.toString().padStart(2, "0")}`;
}

function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
}

function highlight(text, query) {
    const safe = escapeHtml(text);
    if (!query) return safe;
    const q = query.toLowerCase();
    const lower = text.toLowerCase();
    const idx = lower.indexOf(q);
    if (idx === -1) return safe;
    return escapeHtml(text.slice(0, idx)) +
           `<mark>${escapeHtml(text.slice(idx, idx + q.length))}</mark>` +
           escapeHtml(text.slice(idx + q.length));
}

function purpleGradient(str) {
    let hash = 0;
    for (let i = 0; i < str.length; i++) hash = str.charCodeAt(i) + ((hash << 5) - hash);
    const h1 = 258 + (Math.abs(hash) % 16);
    const h2 = 258 + (Math.abs(hash >> 8) % 16);
    const l1 = 18 + (Math.abs(hash >> 16) % 8);
    const l2 = 12 + (Math.abs(hash >> 24) % 6);
    return `linear-gradient(135deg, hsl(${h1}, 60%, ${l1}%), hsl(${h2}, 70%, ${l2}%))`;
}

function artistGradient(str) {
    let hash = 0;
    for (let i = 0; i < str.length; i++) hash = str.charCodeAt(i) + ((hash << 5) - hash);
    const h1 = Math.abs(hash) % 360;
    const h2 = (h1 + 50) % 360;
    return `linear-gradient(135deg, hsl(${h1}, 60%, 45%), hsl(${h2}, 65%, 32%))`;
}

function getInitials(str) {
    if (!str) return "?";
    return str.split(/\s+/).map(w => w[0]).filter(Boolean).slice(0, 2).join("").toUpperCase();
}

function getAlbumYear(album) {
    for (const t of album.tracks) {
        if (t.date) {
            const m = String(t.date).match(/\d{4}/);
            if (m) return parseInt(m[0], 10);
        }
    }
    for (const t of album.tracks) {
        if (t.mtime) return new Date(t.mtime * 1000).getFullYear();
    }
    return 0;
}

const UNKNOWN_ALBUM_NAME = "Без альбома";
function isUnknownAlbum(album) {
    return album.name === UNKNOWN_ALBUM_NAME;
}

/* ============================================================
   FAVORITES
   ============================================================ */
function saveFavorites() {
    localStorage.setItem(FAV_KEY, JSON.stringify([...state.favorites]));
}

function isFavorite(trackId) {
    return state.favorites.has(trackId);
}

function toggleFavorite(trackId) {
    if (state.favorites.has(trackId)) state.favorites.delete(trackId);
    else state.favorites.add(trackId);
    saveFavorites();
    updateFavCount();

    const row = document.querySelector(`.track-row[data-track-id="${trackId}"]`);
    if (row) {
        const btn = row.querySelector(".fav-btn");
        if (btn) {
            const active = isFavorite(trackId);
            btn.classList.toggle("active", active);
            if (active) {
                btn.classList.add("pop");
                setTimeout(() => btn.classList.remove("pop"), 400);
            }
        }
    }

    if (state.currentTrackId === trackId) {
        updatePlayerFavBtn();
        const pBtn = $("#player-fav");
        if (pBtn && isFavorite(trackId)) {
            pBtn.classList.add("pop");
            setTimeout(() => pBtn.classList.remove("pop"), 400);
        }
    }

    if (state.view === "favorites" && !state.currentAlbumKey && !state.currentArtistKey) {
        applyFilters();
    }
}

function updateFavCount() {
    const n = state.favorites.size;
    document.querySelectorAll(".count-badge").forEach(badge => {
        if (n > 0) {
            badge.textContent = n;
            badge.classList.remove("hidden");
        } else {
            badge.classList.add("hidden");
        }
    });
}

function updatePlayerFavBtn() {
    const btn = $("#player-fav");
    if (!btn) return;
    if (!state.currentTrackId) {
        btn.disabled = true;
        btn.classList.remove("active");
        return;
    }
    btn.disabled = false;
    btn.classList.toggle("active", isFavorite(state.currentTrackId));
}

/* ============================================================
   ЗАГРУЗКА
   ============================================================ */
async function loadTracks() {
    try {
        const res = await fetch("/api/tracks");
        const data = await res.json();
        // Если список уже пуст — применяем ответ всегда: иначе один
        // неудачный первый запрос оставляет UI пустым до перезапуска.
        if (state.tracks.length && data.version === state.libVersion) return;
        state.libVersion = data.version;
        state.tracks = data.tracks || [];
        $("#track-count").textContent = state.tracks.length;

        buildAlbums();
        buildArtists();

        updateFavCount();
        applyFilters();
    } catch (e) {
        console.error("Ошибка загрузки:", e);
    }
}

/* ============================================================
   ГРУППИРОВКА В АЛЬБОМЫ
   ============================================================ */
function buildAlbums() {
    const map = new Map();
    state.tracks.forEach(track => {
        const albumName = (track.album || "").trim() || UNKNOWN_ALBUM_NAME;
        const artist = (track.artist || "").trim() || "Неизвестный";
        const key = `${albumName}::${artist}`;
        if (!map.has(key)) {
            map.set(key, { key, name: albumName, artist, tracks: [], coverTrackId: null, totalDuration: 0 });
        }
        const album = map.get(key);
        album.tracks.push(track);
        album.totalDuration += track.duration || 0;
        if (!album.coverTrackId && track.hasCover) album.coverTrackId = track.id;
    });
    map.forEach(album => {
        album.tracks.sort((a, b) => a.title.localeCompare(b.title, "ru", { sensitivity: "base" }));
    });
    state.albums = [...map.values()].sort((a, b) => {
        const aUnknown = isUnknownAlbum(a);
        const bUnknown = isUnknownAlbum(b);
        if (aUnknown && !bUnknown) return 1;
        if (!aUnknown && bUnknown) return -1;
        return a.name.localeCompare(b.name, "ru", { sensitivity: "base" });
    });
}

/* ============================================================
   ГРУППИРОВКА В АРТИСТОВ
   ============================================================ */
function buildArtists() {
    const map = new Map();
    state.tracks.forEach(track => {
        const name = (track.artist || "").trim() || "Неизвестный исполнитель";
        if (!map.has(name)) {
            map.set(name, { key: name, name, tracks: [], totalDuration: 0, albums: new Set(), avatarTrackId: null });
        }
        const artist = map.get(name);
        artist.tracks.push(track);
        artist.totalDuration += track.duration || 0;
        if (track.album) artist.albums.add(track.album);
    });
    map.forEach(a => {
        a.tracks.sort((x, y) => x.title.localeCompare(y.title, "ru", { sensitivity: "base" }));
        const byDate = [...a.tracks].sort((x, y) => {
            const dx = x.date || "", dy = y.date || "";
            if (dx !== dy) return dy.localeCompare(dx);
            return (y.mtime || 0) - (x.mtime || 0);
        });
        const withCover = byDate.find(t => t.hasCover);
        a.avatarTrackId = withCover ? withCover.id : null;
    });
    state.artists = [...map.values()].sort((a, b) => a.name.localeCompare(b.name, "ru", { sensitivity: "base" }));
}

/* ============================================================
   ФИЛЬТРАЦИЯ
   ============================================================ */
function applyFilters() {
    if (state.currentArtistKey) {
        const artist = state.artists.find(a => a.key === state.currentArtistKey);
        if (!artist) { state.currentArtistKey = null; return; }
        let list = artist.tracks;
        if (state.search) {
            const q = state.search.toLowerCase();
            list = list.filter(t => t.title.toLowerCase().includes(q) || (t.album || "").toLowerCase().includes(q));
        }
        state.filteredTracks = list;
        renderCurrentView();
        renderArtistDetail(artist);
        updateArtistDetailHeader(artist);
        return;
    }

    if (state.currentAlbumKey) {
        const album = state.albums.find(a => a.key === state.currentAlbumKey);
        if (!album) { state.currentAlbumKey = null; return; }
        let list = album.tracks;
        if (state.search) {
            const q = state.search.toLowerCase();
            list = list.filter(t => t.title.toLowerCase().includes(q) || t.artist.toLowerCase().includes(q));
        }
        state.filteredTracks = list;
        renderCurrentView();
        renderAlbumDetail(album);
        updateAlbumDetailHeader(album);
        return;
    }

    let trackList = state.tracks;
    if (state.view === "favorites") trackList = trackList.filter(t => isFavorite(t.id));
    if (state.search) {
        const q = state.search.toLowerCase();
        trackList = trackList.filter(t =>
            t.title.toLowerCase().includes(q) ||
            t.artist.toLowerCase().includes(q) ||
            (t.album || "").toLowerCase().includes(q)
        );
    }
    state.filteredTracks = trackList;

    let albumList = state.albums;
    if (state.search) {
        const q = state.search.toLowerCase();
        albumList = albumList.filter(a => a.name.toLowerCase().includes(q) || a.artist.toLowerCase().includes(q));
    }
    state.filteredAlbums = albumList;

    let artistList = state.artists;
    if (state.search) {
        const q = state.search.toLowerCase();
        artistList = artistList.filter(a => a.name.toLowerCase().includes(q));
    }
    state.filteredArtists = artistList;

    renderCurrentView();
    updatePageHeader();
}

/* ============================================================
   ПЕРЕКЛЮЧЕНИЕ VIEW
   ============================================================ */
function renderCurrentView() {
    const views = [
        "#view-tracks", "#view-albums", "#view-artists",
        "#view-album-detail", "#view-artist-detail", "#view-settings", "#empty-state"
    ];
    views.forEach(v => $(v).classList.add("hidden"));

    if (state.view === "settings" && !state.currentAlbumKey && !state.currentArtistKey) {
        $("#view-settings").classList.remove("hidden");
        return;
    }
    if (state.currentAlbumKey) {
        $("#view-album-detail").classList.remove("hidden");
        return;
    }
    if (state.currentArtistKey) {
        $("#view-artist-detail").classList.remove("hidden");
        return;
    }
    if (state.view === "albums") {
        if (!state.filteredAlbums.length) { showEmptyState(); return; }
        $("#view-albums").classList.remove("hidden");
        renderAlbums();
        return;
    }
    if (state.view === "artists") {
        if (!state.filteredArtists.length) { showEmptyState(); return; }
        $("#view-artists").classList.remove("hidden");
        renderArtists();
        return;
    }
    if (!state.filteredTracks.length) { showEmptyState(); return; }
    $("#view-tracks").classList.remove("hidden");
    renderTracks();
}

function showEmptyState() {
    const empty = $("#empty-state");
    empty.classList.remove("hidden");

    if (state.search) {
        $("#empty-title").textContent = "Ничего не найдено";
        $("#empty-text").innerHTML = `По запросу «${escapeHtml(state.search)}» ничего нет`;
    } else if (state.view === "favorites") {
        $("#empty-title").textContent = "В избранном пусто";
        $("#empty-text").innerHTML = "Отмечай треки сердечком, чтобы они появились здесь";
    } else if (state.view === "albums") {
        $("#empty-title").textContent = "Альбомов нет";
        $("#empty-text").innerHTML = "Добавь музыку с тегами альбома в папку <code>music/</code>";
    } else if (state.view === "artists") {
        $("#empty-title").textContent = "Артистов нет";
        $("#empty-text").innerHTML = "Добавь музыку в папку <code>music/</code>";
    } else {
        $("#empty-title").textContent = "Папка пуста";
        $("#empty-text").innerHTML = "Добавь музыку в папку <code>music/</code>";
    }
}

function updatePageHeader() {
    const titles = { all: "Все треки", albums: "Альбомы", artists: "Артисты", favorites: "Избранное", settings: "Настройки" };
    $("#page-title").textContent = titles[state.view] || "Все треки";

    let subtitle = "";
    if (state.search) {
        if (state.view === "albums") subtitle = `найдено альбомов: ${state.filteredAlbums.length}`;
        else if (state.view === "artists") subtitle = `найдено артистов: ${state.filteredArtists.length}`;
        else subtitle = `найдено: ${state.filteredTracks.length}`;
    } else if (state.view === "favorites") {
        subtitle = state.filteredTracks.length ? `${state.filteredTracks.length} треков` : "";
    } else if (state.view === "albums") {
        subtitle = state.filteredAlbums.length ? `${state.filteredAlbums.length} альбомов` : "";
    } else if (state.view === "artists") {
        subtitle = state.filteredArtists.length ? `${state.filteredArtists.length} артистов` : "";
    }
    $("#page-subtitle").textContent = subtitle;

    const playAll = $("#btn-play-all");
    const searchWrap = document.querySelector(".search-wrap");

    if (state.view === "settings") {
        playAll.classList.add("hidden");
        if (searchWrap) searchWrap.classList.add("hidden");
    } else {
        if (searchWrap) searchWrap.classList.remove("hidden");
        if (state.view === "albums" || state.view === "artists") {
            playAll.classList.add("hidden");
        } else {
            playAll.classList.remove("hidden");
            playAll.disabled = state.filteredTracks.length === 0;
        }
    }
}

function updateAlbumDetailHeader(album) {
    $("#page-title").textContent = album.name;
    $("#page-subtitle").textContent = "";
    $("#btn-play-all").classList.add("hidden");
    const backText = $("#back-to-albums-text");
    if (backText) {
        backText.textContent = state.albumBackTarget === "artists" ? "Назад к артисту" : "Назад к альбомам";
    }
}

function updateArtistDetailHeader(artist) {
    $("#page-title").textContent = artist.name;
    $("#page-subtitle").textContent = "";
    $("#btn-play-all").classList.add("hidden");
}

/* ============================================================
   ОТРИСОВКА ТРЕКОВ
   ============================================================ */
function renderTracks() {
    const list = $("#track-list");
    list.innerHTML = "";
    renderTrackRows(list, state.filteredTracks);
}

function renderTrackRows(container, tracks) {
    tracks.forEach((track, i) => {
        const row = document.createElement("div");
        row.className = "track-row";
        row.dataset.index = i;
        row.dataset.trackId = track.id;
        if (track.id === state.currentTrackId) row.classList.add("playing");

        const coverHtml = track.hasCover
            ? `<img src="${coverUrl(track.id, 128, track.cover)}" alt="" loading="lazy">`
            : `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>`;

        const favActive = isFavorite(track.id) ? "active" : "";

        row.innerHTML = `
            <div class="row-num">
                <span class="row-num-text">${i + 1}</span>
                <span class="row-play-icon"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg></span>
            </div>
            <div class="row-cover">${coverHtml}</div>
            <div class="row-title-block">
                <div class="row-title">${highlight(track.title, state.search)}</div>
                <div class="row-artist">${highlight(track.artist, state.search)}</div>
            </div>
            <div class="row-album">${highlight(track.album || "—", state.search)}</div>
            <div class="row-duration">${formatTime(track.duration)}</div>
            <button class="fav-btn ${favActive}" data-fav-id="${track.id}" title="В избранное">
                <svg viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>
                </svg>
            </button>
        `;

        row.addEventListener("click", () => playTrackFromList(track, tracks));
        row.querySelector(".fav-btn").addEventListener("click", (e) => {
            e.stopPropagation();
            toggleFavorite(track.id);
        });

        container.appendChild(row);
    });
}

function renderAlbums() {
    const grid = $("#albums-grid");
    grid.innerHTML = "";
    state.filteredAlbums.forEach((album, i) => {
        const card = document.createElement("div");
        card.className = "album-card";
        card.style.animationDelay = (i * 0.03) + "s";

        const coverHtml = album.coverTrackId
            ? `<img src="${coverUrl(album.coverTrackId, 256)}" alt="" loading="lazy">` : "";
        const coverStyle = album.coverTrackId ? ""
            : `style="background: ${purpleGradient(album.name + album.artist)}"`;

        card.innerHTML = `
            <div class="album-cover" ${coverStyle}>
                ${coverHtml}
                <button class="album-play-btn" title="Играть альбом">
                    <svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
                </button>
            </div>
            <div class="album-info">
                <div class="album-name">${highlight(album.name, state.search)}</div>
                <div class="album-artist">${highlight(album.artist, state.search)}</div>
                <div class="album-count">${album.tracks.length} треков</div>
            </div>
        `;

        card.addEventListener("click", () => openAlbum(album.key));
        card.querySelector(".album-play-btn").addEventListener("click", (e) => {
            e.stopPropagation();
            if (album.tracks.length) playTrackFromList(album.tracks[0], album.tracks);
        });
        grid.appendChild(card);
    });
}

function renderArtists() {
    const grid = $("#artists-grid");
    grid.innerHTML = "";
    state.filteredArtists.forEach((artist, i) => {
        const card = document.createElement("div");
        card.className = "artist-card";
        card.style.animationDelay = (i * 0.03) + "s";

        const avatarHtml = artist.avatarTrackId
            ? `<img src="${coverUrl(artist.avatarTrackId, 256)}" alt="" loading="lazy">`
            : getInitials(artist.name);
        const avatarStyle = artist.avatarTrackId ? "" : `background: ${artistGradient(artist.name)}`;

        card.innerHTML = `
            <div class="artist-avatar" style="${avatarStyle}">${avatarHtml}</div>
            <div class="artist-name">${highlight(artist.name, state.search)}</div>
            <div class="artist-count">${artist.tracks.length} треков</div>
        `;

        card.addEventListener("click", () => openArtist(artist.key));
        grid.appendChild(card);
    });
}

/* ============================================================
   ДЕТАЛИ АЛЬБОМА
   ============================================================ */
function openAlbum(key, fromArtist = false) {
    state.currentAlbumKey = key;
    state.currentArtistKey = null;
    state.albumBackTarget = fromArtist ? "artists" : "albums";
    state.search = "";
    $("#search-input").value = "";
    $("#search-clear").classList.add("hidden");
    applyFilters();
    document.querySelector(".main").scrollTop = 0;
}

function closeAlbum() {
    state.currentAlbumKey = null;
    applyFilters();
}

function renderAlbumDetail(album) {
    const coverEl = $("#album-hero-cover");
    if (album.coverTrackId) {
        coverEl.innerHTML = `<img src="${coverUrl(album.coverTrackId, 512)}" alt="">`;
        coverEl.style.background = "";
    } else {
        coverEl.innerHTML = "";
        coverEl.style.background = purpleGradient(album.name + album.artist);
    }
    $("#album-hero-title").textContent = album.name;

    const totalMin = Math.round(album.totalDuration / 60);
    $("#album-hero-meta").innerHTML = `
        <span>${escapeHtml(album.artist)}</span>
        <span class="dot"></span>
        <span>${album.tracks.length} треков</span>
        <span class="dot"></span>
        <span>${totalMin} мин</span>
    `;

    const playBtn = $("#btn-play-album");
    playBtn.onclick = () => { if (album.tracks.length) playTrackFromList(album.tracks[0], album.tracks); };
    playBtn.disabled = album.tracks.length === 0;

    const list = $("#album-track-list");
    list.innerHTML = "";
    if (!state.filteredTracks.length) {
        list.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-3)">Ничего не найдено</div>';
        return;
    }
    renderTrackRows(list, state.filteredTracks);
}

/* ============================================================
   ДЕТАЛИ АРТИСТА
   ============================================================ */
function openArtist(key) {
    state.currentArtistKey = key;
    state.currentAlbumKey = null;
    state.search = "";
    $("#search-input").value = "";
    $("#search-clear").classList.add("hidden");
    applyFilters();
    document.querySelector(".main").scrollTop = 0;
}

function closeArtist() {
    state.currentArtistKey = null;
    applyFilters();
}

function renderArtistDetail(artist) {
    const avatarEl = $("#artist-hero-avatar");
    if (artist.avatarTrackId) {
        avatarEl.innerHTML = `<img src="${coverUrl(artist.avatarTrackId, 512)}" alt="">`;
        avatarEl.style.background = "";
    } else {
        avatarEl.textContent = getInitials(artist.name);
        avatarEl.style.background = artistGradient(artist.name);
    }
    $("#artist-hero-title").textContent = artist.name;

    const totalMin = Math.round(artist.totalDuration / 60);
    const albumsCount = artist.albums.size;
    const albumsWord = albumsCount === 1 ? "альбом" : (albumsCount < 5 ? "альбома" : "альбомов");

    $("#artist-hero-meta").innerHTML = `
        <span>${artist.tracks.length} треков</span>
        <span class="dot"></span>
        <span>${albumsCount} ${albumsWord}</span>
        <span class="dot"></span>
        <span>${totalMin} мин</span>
    `;

    const playBtn = $("#btn-play-artist");
    playBtn.onclick = () => { if (artist.tracks.length) playTrackFromList(artist.tracks[0], artist.tracks); };
    playBtn.disabled = artist.tracks.length === 0;

    renderArtistAlbums(artist);

    const list = $("#artist-track-list");
    list.innerHTML = "";
    if (!state.filteredTracks.length) {
        list.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-3)">Ничего не найдено</div>';
        return;
    }
    renderTrackRows(list, state.filteredTracks);
}

function renderArtistAlbums(artist) {
    const section = $("#artist-albums-section");
    const scroll = $("#artist-albums-scroll");

    const artistAlbums = state.albums.filter(a => a.artist === artist.name);
    if (!artistAlbums.length) { section.classList.add("hidden"); return; }
    section.classList.remove("hidden");

    artistAlbums.sort((a, b) => {
        const aUnknown = isUnknownAlbum(a);
        const bUnknown = isUnknownAlbum(b);
        if (aUnknown && !bUnknown) return 1;
        if (!aUnknown && bUnknown) return -1;
        return getAlbumYear(b) - getAlbumYear(a);
    });

    scroll.innerHTML = "";

    artistAlbums.forEach(album => {
        const card = document.createElement("div");
        card.className = "artist-album-card";

        const coverInner = album.coverTrackId
            ? `<img src="${coverUrl(album.coverTrackId, 256)}" alt="" loading="lazy">`
            : "";

        const coverStyle = album.coverTrackId ? ""
            : `background: ${purpleGradient(album.name + album.artist)}`;

        const year = getAlbumYear(album);
        const yearText = year ? `${year}` : `${album.tracks.length} треков`;

        card.innerHTML = `
            <div class="artist-album-cover" style="${coverStyle}">
                ${coverInner}
                <button class="artist-album-play" title="Играть альбом">
                    <svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
                </button>
            </div>
            <div class="artist-album-name">${escapeHtml(album.name)}</div>
            <div class="artist-album-year">${yearText}</div>
        `;

        card.addEventListener("click", () => {
            state.albumBackArtistKey = artist.key;
            state.currentArtistKey = null;
            state.currentAlbumKey = album.key;
            state.albumBackTarget = "artists";
            state.view = "albums";
            $$("[data-view]").forEach(b => b.classList.remove("active"));
            document.querySelectorAll('[data-view="albums"]').forEach(b => b.classList.add("active"));
            applyFilters();
            document.querySelector(".main").scrollTop = 0;
        });

        card.querySelector(".artist-album-play").addEventListener("click", (e) => {
            e.stopPropagation();
            if (album.tracks.length) playTrackFromList(album.tracks[0], album.tracks);
        });

        scroll.appendChild(card);
    });

    updateArtistAlbumsNav();
}

function updateArtistAlbumsNav() {
    const scroll = $("#artist-albums-scroll");
    const prevBtn = $("#artist-albums-prev");
    const nextBtn = $("#artist-albums-next");
    if (!scroll || !prevBtn || !nextBtn) return;

    const scrollAmount = () => Math.max(200, scroll.clientWidth - 100);
    const updateButtons = () => {
        const maxScroll = scroll.scrollWidth - scroll.clientWidth;
        prevBtn.disabled = scroll.scrollLeft <= 5;
        nextBtn.disabled = scroll.scrollLeft >= maxScroll - 5;
    };

    prevBtn.onclick = () => scroll.scrollBy({ left: -scrollAmount(), behavior: "smooth" });
    nextBtn.onclick = () => scroll.scrollBy({ left: scrollAmount(), behavior: "smooth" });

    scroll.addEventListener("scroll", updateButtons);
    window.addEventListener("resize", updateButtons);
    setTimeout(updateButtons, 50);
}

/* ============================================================
   ПЛЕЕР
   ============================================================ */
function makeShuffleOrder(n) {
    const order = [...Array(n).keys()];
    for (let i = n - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [order[i], order[j]] = [order[j], order[i]];
    }
    return order;
}

function buildShuffleOrder() {
    state.shuffleOrder = makeShuffleOrder(state.currentQueue.length);
    state.pendingShuffle = null;   // заранее выбранный следующий круг больше не актуален
}

function playTrackFromList(track, list) {
    ensureAudioContext();

    if (state.currentQueue !== list) {
        state.currentQueue = list;
        buildShuffleOrder();
    }

    state.currentTrackId = track.id;
    state.currentTrack = track;
    state.switchingTrack = true;
    audio.volume = state.volume;
    audio.playTrack(track).then(() => {
        if (state.currentTrackId !== track.id) return;   // уже переключили трек
        state.isPlaying = true;
        state.switchingTrack = false;
        onTrackStarted(track);
    }).catch((e) => {
        if (state.currentTrackId !== track.id) return;   // это отменённый старый трек
        state.switchingTrack = false;
        console.error("Play error:", e);
        clearPresence();
    });
}

// Всё, что нужно обновить, когда трек реально начал играть
// (после обычного старта и после gapless-перехода).
function onTrackStarted(track) {
    updatePlayerUI(track);
    updatePlayIcon();
    highlightCurrent();

    pushRecent(track);
    const _api = winApi("set_playback_state");
    if (_api) _api(true).catch(() => {});

    updateMediaSessionMetadata(track);
    if ("mediaSession" in navigator) {
        navigator.mediaSession.playbackState = "playing";
    }
    updatePositionState();
    syncPresence();
}

// Что играть следом — без побочных эффектов. Нужно и playNext(), и gapless-движку.
// null — очередь закончилась; pending — начало нового круга перемешанной очереди.
function peekNext(manual) {
    const q = state.currentQueue;
    if (!q.length) return null;

    const currentIdx = q.findIndex(t => t.id === state.currentTrackId);
    if (currentIdx === -1) return null;

    if (state.repeat === "one" && !manual) return { track: q[currentIdx] };

    if (state.shuffle) {
        const nextPos = state.shuffleOrder.indexOf(currentIdx) + 1;
        if (nextPos >= state.shuffleOrder.length) {
            if (state.repeat === "off") return null;
            if (!state.pendingShuffle || state.pendingShuffle.length !== q.length) {
                state.pendingShuffle = makeShuffleOrder(q.length);
            }
            return { track: q[state.pendingShuffle[0]], pending: true };
        }
        return { track: q[state.shuffleOrder[nextPos]] };
    }

    if (currentIdx === q.length - 1) {
        return state.repeat === "off" ? null : { track: q[0] };
    }
    return { track: q[currentIdx + 1] };
}

function commitPendingShuffle() {
    if (state.pendingShuffle && state.pendingShuffle.length === state.currentQueue.length) {
        state.shuffleOrder = state.pendingShuffle;
    }
    state.pendingShuffle = null;
}

function playNext(manual = false) {
    if (!state.currentQueue.length) return;

    if (state.repeat === "one" && !manual) {
        audio.currentTime = 0;
        audio.play();
        return;
    }

    const currentIdx = state.currentQueue.findIndex(t => t.id === state.currentTrackId);
    if (currentIdx === -1) {
        playTrackFromList(state.currentQueue[0], state.currentQueue);
        return;
    }

    const next = peekNext(true);
    if (!next) {
        audio.pause();
        state.isPlaying = false;
        updatePlayIcon();
        clearPresence();
        return;
    }
    if (next.pending) commitPendingShuffle();
    playTrackFromList(next.track, state.currentQueue);
}

function playPrev() {
    if (!state.currentQueue.length) return;
    if (audio.currentTime > 3) { audio.currentTime = 0; return; }

    const currentIdx = state.currentQueue.findIndex(t => t.id === state.currentTrackId);
    if (currentIdx === -1) return;

    let prevIdx;

    if (state.shuffle) {
        const posInShuffle = state.shuffleOrder.indexOf(currentIdx);
        const prevPos = posInShuffle - 1;
        prevIdx = prevPos < 0
            ? state.shuffleOrder[state.shuffleOrder.length - 1]
            : state.shuffleOrder[prevPos];
    } else {
        prevIdx = (currentIdx - 1 + state.currentQueue.length) % state.currentQueue.length;
    }

    playTrackFromList(state.currentQueue[prevIdx], state.currentQueue);
}

function togglePlay() {
    if (!state.currentTrackId && state.filteredTracks.length) {
        playTrackFromList(state.filteredTracks[0], state.filteredTracks);
        return;
    }
    ensureAudioContext();
    if (audio.paused) { audio.play(); state.isPlaying = true; }
    else { audio.pause(); state.isPlaying = false; }
    updatePlayIcon();

    const _api = winApi("set_playback_state");
    if (_api) _api(state.isPlaying).catch(() => {});
}

function updatePlayIcon() {
    const iconPlay = $("#icon-play");
    const iconPause = $("#icon-pause");
    if (state.isPlaying) {
        iconPlay.classList.add("hidden");
        iconPause.classList.remove("hidden");
    } else {
        iconPlay.classList.remove("hidden");
        iconPause.classList.add("hidden");
    }
}

function updatePlayerUI(track) {
    state.currentTrack = track;
    $("#player-title").textContent = track.title;
    $("#player-artist").textContent = track.artist + (track.album ? " · " + track.album : "");

    const cover = $("#player-cover");
    if (track.hasCover) {
        cover.innerHTML = `<img src="${coverUrl(track.id, 512, track.cover)}" alt="">`;
        cover.classList.add("has-cover");
    } else {
        cover.innerHTML = `
            <div class="cover-fallback">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                    <path d="M9 18V5l12-2v13"/>
                    <circle cx="6" cy="18" r="3"/>
                    <circle cx="18" cy="16" r="3"/>
                </svg>
            </div>
        `;
        cover.classList.remove("has-cover");
    }
    updatePlayerFavBtn();
}

function highlightCurrent() {
    $$(".track-row").forEach((row) => {
        row.classList.toggle("playing", row.dataset.trackId === state.currentTrackId);
    });
}

/* ============================================================
   MEDIA SESSION
   ============================================================ */
function updateMediaSessionMetadata(track) {
    if (!("mediaSession" in navigator) || !("MediaMetadata" in window)) return;
    if (!track) return;

    const artwork = [];
    if (track.hasCover) {
        // сервер снапит s= вверх: просим реально существующие размеры
        const art = (px) => coverUrl(track.id, px, track.cover);
        artwork.push(
            { src: art(128), sizes: "96x96",   type: "image/jpeg" },
            { src: art(128), sizes: "128x128", type: "image/jpeg" },
            { src: art(256), sizes: "192x192", type: "image/jpeg" },
            { src: art(256), sizes: "256x256", type: "image/jpeg" },
            { src: art(512), sizes: "384x384", type: "image/jpeg" },
            { src: art(512), sizes: "512x512", type: "image/jpeg" }
        );
    }

    try {
        navigator.mediaSession.metadata = new MediaMetadata({
            title: track.title || "Неизвестный трек",
            artist: track.artist || "Неизвестный исполнитель",
            album: track.album || "",
            artwork: artwork,
        });
    } catch (e) {
        console.warn("MediaMetadata failed:", e);
    }
}

function setupMediaSessionHandlers() {
    if (!("mediaSession" in navigator)) return;

    const safeSet = (action, handler) => {
        try { navigator.mediaSession.setActionHandler(action, handler); } catch (e) {}
    };

    safeSet("play", async () => {
        try {
            await audio.play();
            state.isPlaying = true;
            updatePlayIcon();
            navigator.mediaSession.playbackState = "playing";
        } catch (e) { console.warn("MS play failed:", e); }
    });

    safeSet("pause", () => {
        audio.pause();
        state.isPlaying = false;
        updatePlayIcon();
        navigator.mediaSession.playbackState = "paused";
    });

    safeSet("previoustrack", () => { playPrev(); });
    safeSet("nexttrack", () => { playNext(true); });

    safeSet("seekbackward", (details) => {
        const skip = details.seekOffset || 10;
        audio.currentTime = Math.max(audio.currentTime - skip, 0);
        updatePositionState();
    });

    safeSet("seekforward", (details) => {
        const skip = details.seekOffset || 10;
        audio.currentTime = Math.min(audio.currentTime + skip, audio.duration || 0);
        updatePositionState();
    });

    safeSet("seekto", (details) => {
        if (details.fastSeek && "fastSeek" in audio) {
            try { audio.fastSeek(details.seekTime); } catch (e) {}
            return;
        }
        audio.currentTime = details.seekTime;
        updatePositionState();
    });

    safeSet("stop", () => {
        audio.pause();
        audio.currentTime = 0;
        state.isPlaying = false;
        updatePlayIcon();
        navigator.mediaSession.playbackState = "none";
    });
}

function updatePositionState() {
    if (!("mediaSession" in navigator)) return;
    if (typeof navigator.mediaSession.setPositionState !== "function") return;
    if (!audio.duration || isNaN(audio.duration) || audio.duration <= 0) return;

    try {
        navigator.mediaSession.setPositionState({
            duration: audio.duration,
            playbackRate: audio.playbackRate || 1,
            position: Math.min(audio.currentTime, audio.duration),
        });
    } catch (e) {}
}

/* ============================================================
   ПРОГРЕСС
   ============================================================ */
let _lastTaskbarUpdate = 0;

audio.addEventListener("timeupdate", () => {
    if (state.isSeeking) return;
    const pct = (audio.currentTime / audio.duration) * 100 || 0;
    $("#progress-fill").style.width = pct + "%";
    $("#progress-thumb").style.left = pct + "%";
    $("#time-current").textContent = formatTime(audio.currentTime);

    updatePositionState();

    // Прогресс на значке панели задач — не чаще 1 раза в секунду
    const now = performance.now();
    if (now - _lastTaskbarUpdate > 1000) {
        _lastTaskbarUpdate = now;
        const _api = winApi("update_taskbar_progress");
        if (_api && audio.duration > 0) {
            _api(audio.currentTime, audio.duration, !audio.paused).catch(() => {});
        }
    }
});

audio.addEventListener("loadedmetadata", () => {
    $("#time-total").textContent = formatTime(audio.duration);
    updatePositionState();
});

audio.addEventListener("ended", () => {
    const _clr = winApi("clear_taskbar_progress");
    if (_clr) _clr().catch(() => {});
    clearPresence();
    playNext(false);
});

audio.addEventListener("play", () => {
    state.switchingTrack = false;
    const _api = winApi("set_playback_state");
    if (_api) _api(true).catch(() => {});
    syncPresence();
});

audio.addEventListener("pause", () => {
    const _api = winApi("set_playback_state");
    if (_api) _api(false).catch(() => {});
    if (!state.switchingTrack && !audio.ended) syncPresence();
});

audio.addEventListener("seeked", () => {
    if (!state.switchingTrack) syncPresence();
});

const progressBar = $("#progress-bar");
progressBar.addEventListener("mousedown", (e) => { state.isSeeking = true; seekTo(e); });
document.addEventListener("mousemove", (e) => { if (state.isSeeking) seekTo(e); });
document.addEventListener("mouseup", () => {
    if (state.isSeeking) {
        state.isSeeking = false;
        audio.currentTime = seekTo.lastTime || 0;
    }
});

function seekTo(e) {
    const rect = progressBar.getBoundingClientRect();
    const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    $("#progress-fill").style.width = (pct * 100) + "%";
    $("#progress-thumb").style.left = (pct * 100) + "%";
    seekTo.lastTime = pct * audio.duration;
}

/* ============================================================
   ГРОМКОСТЬ
   ============================================================ */
const volumeBar = $("#volume-bar");
const volumeFill = $("#volume-fill");
const volumeThumb = $("#volume-thumb");

function updateVolumeIcon() {
    const iconOn  = document.getElementById("icon-volume-on");
    const iconOff = document.getElementById("icon-volume-off");
    const btn     = document.getElementById("btn-mute");
    if (!iconOn || !iconOff || !btn) return;

    const isMuted = audio.volume <= 0.001 || state.volume <= 0.001;

    if (isMuted) {
        iconOn.classList.add("hidden");
        iconOff.classList.remove("hidden");
        btn.title = "Включить звук";
    } else {
        iconOn.classList.remove("hidden");
        iconOff.classList.add("hidden");
        btn.title = "Выключить звук";
    }
}

function saveVolume() {
    localStorage.setItem("alpha-music-volume", state.volume.toString());
}

function updateVolumeUI() {
    const pct = state.volume * 100;
    volumeFill.style.width = pct + "%";
    volumeThumb.style.left = pct + "%";
    updateVolumeIcon();
}

let volumeDragging = false;

function updateVolumeFromEvent(e) {
    const rect = volumeBar.getBoundingClientRect();
    const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    state.volume = pct;
    audio.volume = pct;
    updateVolumeUI();
    saveVolume();
}

if (volumeBar) {
    volumeBar.addEventListener("mousedown", (e) => {
        e.preventDefault();
        volumeDragging = true;
        updateVolumeFromEvent(e);
    });

    volumeBar.addEventListener("wheel", (e) => {
        e.preventDefault();
        const delta = e.deltaY > 0 ? -0.05 : 0.05;
        state.volume = Math.max(0, Math.min(1, state.volume + delta));
        audio.volume = state.volume;
        updateVolumeUI();
        saveVolume();
    }, { passive: false });
}

document.addEventListener("mousemove", (e) => { if (volumeDragging) updateVolumeFromEvent(e); });
document.addEventListener("mouseup", () => { volumeDragging = false; });

$("#btn-mute").addEventListener("click", () => {
    if (audio.volume > 0) {
        audio.dataset.prevVolume = audio.volume;
        audio.volume = 0;
        state.volume = 0;
    } else {
        const v = parseFloat(audio.dataset.prevVolume || 0.7);
        audio.volume = v;
        state.volume = v;
    }
    updateVolumeUI();
    saveVolume();
});

/* ============================================================
   КНОПКА ИЗБРАННОГО В ПЛЕЕРЕ
   ============================================================ */
$("#player-fav").addEventListener("click", () => {
    if (!state.currentTrackId) return;
    toggleFavorite(state.currentTrackId);
});

/* ============================================================
   КНОПКА МИНИ-РЕЖИМА
   ============================================================ */
const _miniBtn = $("#btn-mini");
if (_miniBtn) {
    _miniBtn.addEventListener("click", () => {
        const _api = winApi("toggle_mini_mode");
        if (_api) _api().catch(() => {});
    });
}

/* ============================================================
   SHUFFLE / REPEAT
   ============================================================ */
function updateShuffleUI() {
    const btn = $("#btn-shuffle");
    if (btn) btn.classList.toggle("active", state.shuffle);
}

function updateRepeatUI() {
    const btn = $("#btn-repeat");
    const iconRepeat = $("#icon-repeat");
    const iconRepeatOne = $("#icon-repeat-one");

    if (!btn) return;

    btn.classList.toggle("active", state.repeat !== "off");

    if (state.repeat === "one") {
        if (iconRepeat) iconRepeat.classList.add("hidden");
        if (iconRepeatOne) iconRepeatOne.classList.remove("hidden");
        btn.title = "Повтор одного трека";
    } else {
        if (iconRepeat) iconRepeat.classList.remove("hidden");
        if (iconRepeatOne) iconRepeatOne.classList.add("hidden");
        btn.title = state.repeat === "all" ? "Повтор всей очереди" : "Повтор выключен";
    }
}

$("#btn-shuffle").addEventListener("click", () => {
    state.shuffle = !state.shuffle;
    if (state.shuffle) buildShuffleOrder();
    updateShuffleUI();
    localStorage.setItem("alpha-music-shuffle", state.shuffle ? "1" : "0");
});

$("#btn-repeat").addEventListener("click", () => {
    const cycle = { off: "all", all: "one", one: "off" };
    state.repeat = cycle[state.repeat];
    updateRepeatUI();
    localStorage.setItem("alpha-music-repeat", state.repeat);
});

/* ============================================================
   EQUALIZER UI
   ============================================================ */
function buildEQPresets() {
    const container = $("#eq-presets");
    if (!container) return;
    container.innerHTML = "";

    Object.entries(EQ_PRESETS).forEach(([key, preset]) => {
        const btn = document.createElement("button");
        btn.className = "eq-preset-btn";
        btn.dataset.preset = key;
        btn.textContent = preset.label;
        btn.addEventListener("click", () => applyEQPreset(key));
        container.appendChild(btn);
    });
    updateEQPresetActive();
}

function buildEQBands() {
    const container = $("#eq-bands");
    if (!container) return;
    container.innerHTML = "";

    EQ_BANDS.forEach((band, i) => {
        const wrapper = document.createElement("div");
        wrapper.className = "eq-band";

        const gain = state.eq.gains[i] || 0;
        const gainText = formatGain(gain);
        const cls = gain > 0 ? "positive" : (gain < 0 ? "negative" : "zero");

        wrapper.innerHTML = `
            <div class="eq-band-value ${cls}" id="eq-val-${i}">${gainText}</div>
            <div class="eq-band-slider">
                <input type="range" min="-24" max="24" step="0.5" value="${gain}" data-band="${i}" id="eq-slider-${i}">
            </div>
            <div class="eq-band-label">${band.label}</div>
        `;

        container.appendChild(wrapper);

        wrapper.querySelector("input").addEventListener("input", (e) => {
            const val = parseFloat(e.target.value);
            state.eq.gains[i] = val;
            state.eq.preset = "custom";

            const el = $(`#eq-val-${i}`);
            if (el) {
                el.textContent = formatGain(val);
                el.classList.remove("positive", "negative", "zero");
                el.classList.add(val > 0 ? "positive" : (val < 0 ? "negative" : "zero"));
            }

            applyEQGains();
            updateEQPresetActive();
            saveEQ();
        });
    });
}

function applyEQPreset(key) {
    const preset = EQ_PRESETS[key];
    if (!preset) return;
    state.eq.preset = key;
    state.eq.gains = [...preset.gains];

    preset.gains.forEach((g, i) => {
        const slider = $(`#eq-slider-${i}`);
        if (slider) slider.value = g;
        const valEl = $(`#eq-val-${i}`);
        if (valEl) {
            valEl.textContent = formatGain(g);
            valEl.classList.remove("positive", "negative", "zero");
            valEl.classList.add(g > 0 ? "positive" : (g < 0 ? "negative" : "zero"));
        }
    });

    applyEQGains();
    updateEQPresetActive();
    saveEQ();
}

function updateEQPresetActive() {
    $$(".eq-preset-btn").forEach(btn => {
        btn.classList.toggle("active", btn.dataset.preset === state.eq.preset);
    });
}

function resetEQ() {
    state.eq.gains = new Array(EQ_BANDS.length).fill(0);
    state.eq.preset = "flat";
    state.eq.enabled = true;

    const toggle = $("#eq-enabled");
    if (toggle) toggle.checked = true;

    EQ_BANDS.forEach((_, i) => {
        const slider = $(`#eq-slider-${i}`);
        if (slider) slider.value = 0;
        const valEl = $(`#eq-val-${i}`);
        if (valEl) {
            valEl.textContent = "0";
            valEl.classList.remove("positive", "negative");
            valEl.classList.add("zero");
        }
    });

    applyEQGains();
    updateEQPresetActive();
    saveEQ();
}

function initEQ() {
    buildEQPresets();
    buildEQBands();

    const toggle = $("#eq-enabled");
    if (toggle) {
        toggle.checked = state.eq.enabled;
        toggle.addEventListener("change", (e) => {
            state.eq.enabled = e.target.checked;
            ensureAudioContext();
            applyEQGains();
            saveEQ();
        });
    }

    const resetBtn = $("#eq-reset");
    if (resetBtn) resetBtn.addEventListener("click", resetEQ);
}

/* ============================================================
   DISCORD SETTINGS
   ============================================================ */
function initDiscordSettings() {
    const section = $("#discord-section");
    if (!section) return;

    if (!winApi("get_discord_config")) {
        window.addEventListener("pywebviewready", initDiscordSettings, { once: true });
        return;
    }

    section.classList.remove("hidden");

    const enabled = $("#discord-enabled");
    const input = $("#discord-client-id");
    const status = $("#discord-status");
    const save = $("#discord-save");
    if (!enabled || !input || !status || !save) return;

    let inputDirty = false;
    input.addEventListener("input", () => { inputDirty = true; });

    const render = (cfg) => {
        if (!cfg) return;
        enabled.checked = !!cfg.enabled;
        if (!inputDirty) input.value = cfg.client_id || "";

        let text;
        if (!cfg.available) {
            text = "Модуль pypresence не установлен — выполни pip install pypresence.";
        } else if (!cfg.client_id) {
            text = "Укажи Application ID и нажми «Сохранить».";
        } else if (!cfg.enabled) {
            text = "Выключено.";
        } else if (cfg.connected) {
            text = "Подключено к Discord.";
        } else if (cfg.error) {
            let err = String(cfg.error);
            if (/could not find discord/i.test(err)) err = "Discord не запущен.";
            else if (/invalid/i.test(err)) err = "Проверь Application ID.";
            text = "Жду Discord… (" + err + ")";
        } else {
            text = "Подключение…";
        }

        status.textContent = text;
        status.classList.toggle("ok", !!cfg.connected && !!cfg.enabled);
    };

    const refresh = () => {
        const api = winApi("get_discord_config");
        if (api) api().then(render).catch(() => {});
    };

    const apply = () => {
        const api = winApi("set_discord_config");
        if (!api) return;
        api(enabled.checked, input.value.trim()).then((cfg) => {
            inputDirty = false;
            render(cfg);
        }).catch(() => {});
    };

    save.addEventListener("click", apply);
    enabled.addEventListener("change", apply);
    input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") apply();
    });

    refresh();
    setInterval(() => {
        if (state.view === "settings") refresh();
    }, 5000);
}

/* ============================================================
   SEARCH
   ============================================================ */
const searchInput = $("#search-input");
const searchClear = $("#search-clear");
let searchTimeout = null;

searchInput.addEventListener("input", (e) => {
    const value = e.target.value;
    searchClear.classList.toggle("hidden", !value);
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(() => {
        state.search = value.trim();
        applyFilters();
    }, 150);
});

searchClear.addEventListener("click", () => {
    searchInput.value = "";
    searchClear.classList.add("hidden");
    state.search = "";
    applyFilters();
    searchInput.focus();
});

document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "f") {
        e.preventDefault();
        searchInput.focus();
        searchInput.select();
    }
});

/* ============================================================
   NAV
   ============================================================ */
$$("[data-view]").forEach(btn => {
    btn.addEventListener("click", () => {
        const view = btn.dataset.view;

        $$("[data-view]").forEach(b => b.classList.remove("active"));

        document.querySelectorAll(`[data-view="${view}"]`).forEach(b => {
            b.classList.add("active");
        });

        state.view = view;
        state.currentAlbumKey = null;
        state.currentArtistKey = null;
        state.albumBackTarget = "albums";
        state.albumBackArtistKey = null;
        applyFilters();
    });
});

$("#back-to-albums").addEventListener("click", () => {
    if (state.albumBackTarget === "artists" && state.albumBackArtistKey) {
        const artistKey = state.albumBackArtistKey;
        state.currentAlbumKey = null;
        state.currentArtistKey = artistKey;
        state.albumBackTarget = "albums";
        state.albumBackArtistKey = null;
        state.view = "artists";
        $$("[data-view]").forEach(b => b.classList.remove("active"));
        document.querySelectorAll('[data-view="artists"]').forEach(b => b.classList.add("active"));
        applyFilters();
        document.querySelector(".main").scrollTop = 0;
        return;
    }
    $$("[data-view]").forEach(b => b.classList.remove("active"));
    document.querySelectorAll('[data-view="albums"]').forEach(b => b.classList.add("active"));
    state.view = "albums";
    closeAlbum();
});

$("#back-to-artists").addEventListener("click", () => {
    $$("[data-view]").forEach(b => b.classList.remove("active"));
    document.querySelectorAll('[data-view="artists"]').forEach(b => b.classList.add("active"));
    state.view = "artists";
    closeArtist();
});

/* ============================================================
   КНОПКИ
   ============================================================ */
$("#btn-play").addEventListener("click", togglePlay);
$("#btn-next").addEventListener("click", () => playNext(true));
$("#btn-prev").addEventListener("click", playPrev);
$("#btn-play-all").addEventListener("click", () => {
    if (state.filteredTracks.length) {
        playTrackFromList(state.filteredTracks[0], state.filteredTracks);
    }
});

document.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
    if (e.code === "Space") { e.preventDefault(); togglePlay(); }
    else if (e.code === "ArrowRight" && e.shiftKey) playNext(true);
    else if (e.code === "ArrowLeft" && e.shiftKey) playPrev();
    else if (e.code === "Escape") {
        if (state.currentAlbumKey) $("#back-to-albums").click();
        else if (state.currentArtistKey) $("#back-to-artists").click();
    }
});

/* ============================================================
   DESKTOP MODE — кастомный титлбар
   ============================================================ */
let nativeBgLast = "";

// Цвет подложки окна = фон темы (меньше вспышек при растягивании)
function syncNativeBackground() {
    const api = window.pywebview && window.pywebview.api;
    if (!api || typeof api.set_background !== "function") return;
    const bg = getComputedStyle(document.body).backgroundColor;
    if (!bg || bg === nativeBgLast) return;
    nativeBgLast = bg;
    api.set_background(bg).catch(() => {});
}

function initDesktopMode() {
    const api = window.pywebview && window.pywebview.api;
    if (!api || typeof api.start_drag !== "function") return;
    if (document.body.classList.contains("desktop-mode")) return;

    document.body.classList.add("desktop-mode");

    const titlebar = document.getElementById("titlebar");
    const btnMax   = document.getElementById("tb-maximize");
    const glyphMax = document.getElementById("tb-glyph-maximize");

    function setMaximized(isMax) {
        document.body.classList.toggle("maximized", isMax);
        if (glyphMax) glyphMax.textContent = isMax ? "\uE923" : "\uE922";
        if (btnMax) {
            const label = isMax ? "Восстановить" : "Развернуть";
            btnMax.title = label;
            btnMax.setAttribute("aria-label", label);
        }
    }

    window.__onWindowState = (isMax, isActive) => {
        setMaximized(!!isMax);
        document.body.classList.toggle("window-inactive", !isActive);
    };

    api.is_maximized().then(setMaximized).catch(() => {});

    const bindBtn = (id, fn) => {
        const el = document.getElementById(id);
        if (el) el.addEventListener("click", fn);
    };
    bindBtn("tb-minimize", () => api.minimize());
    bindBtn("tb-maximize", () => api.toggle_maximize());
    bindBtn("tb-close",    () => api.close());

    if (titlebar) {
        titlebar.addEventListener("mousedown", (e) => {
            if (e.button !== 0) return;
            e.preventDefault();
            if (e.target.closest(".titlebar-controls")) return;
            api.start_drag();
        });
        titlebar.addEventListener("contextmenu", (e) => e.preventDefault());
    }

    // ---- мини-режим: заголовка нет, окно тянется за плеер ----
    const playerEl = document.querySelector(".player");
    if (playerEl) {
        playerEl.addEventListener("mousedown", (e) => {
            if (!document.body.classList.contains("mini-mode") || e.button !== 0) return;
            if (e.target.closest("button, input, .progress-bar, .volume-bar")) return;
            e.preventDefault();
            api.start_drag();
        });
    }
    bindBtn("btn-mini-exit",  () => api.set_mini_mode(false));
    bindBtn("btn-mini-close", () => api.close());
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && document.body.classList.contains("mini-mode")) {
            api.set_mini_mode(false);
        }
    });
    // после перезагрузки страницы подхватить состояние окна
    if (api.is_mini_mode) {
        api.is_mini_mode().then((v) => window.__onMiniMode(!!v)).catch(() => {});
    }

    const EDGES = {
        left: 10, right: 11, top: 12, topleft: 13,
        topright: 14, bottom: 15, bottomleft: 16, bottomright: 17,
    };
    for (const [name, code] of Object.entries(EDGES)) {
        const h = document.createElement("div");
        h.className = "rz rz-" + name;
        h.addEventListener("mousedown", (e) => {
            if (e.button !== 0) return;
            e.preventDefault();
            e.stopPropagation();
            api.start_resize(code);
        });
        document.body.appendChild(h);
    }

    syncNativeBackground();
}

if (window.pywebview && window.pywebview.api) {
    initDesktopMode();
}
window.addEventListener("pywebviewready", initDesktopMode);

/* ============================================================
   ОТКРЫТИЕ ФАЙЛОВ ИЗВНЕ (вызывается из desktop.py)
   items — список треков {id, title, artist, ...}; mode: play | enqueue
   ============================================================ */
window.__openExternal = function (items, mode) {
    if (!Array.isArray(items) || !items.length) return;

    if (mode === "enqueue" && state.currentTrackId && state.currentQueue.length) {
        state.currentQueue = [...state.currentQueue, ...items];
        buildShuffleOrder();
        return;
    }
    playTrackFromList(items[0], items);
};

/* ============================================================
   WINDOWS-ИНТЕГРАЦИЯ
   — вызывается из windows_integration.py через evaluate_js
   — pushRecent формирует Jump List
   ============================================================ */
const RECENT_KEY = "alpha-music-recent";
const RECENT_MAX = 10;

function getRecent() {
    try {
        return JSON.parse(localStorage.getItem(RECENT_KEY) || "[]");
    } catch {
        return [];
    }
}

function pushRecent(track) {
    if (!track || !track.id) return;
    let recent = getRecent().filter(id => id !== track.id);
    recent.unshift(track.id);
    recent = recent.slice(0, RECENT_MAX);
    localStorage.setItem(RECENT_KEY, JSON.stringify(recent));

    const _api = winApi("update_recent_tracks_by_ids");
    if (_api) _api(recent).catch(() => {});
}

window.__mediaAction = function (action) {
    if (action === "playpause") {
        togglePlay();
    } else if (action === "next") {
        playNext(true);
    } else if (action === "prev") {
        playPrev();
    } else if (action === "stop") {
        audio.pause();
        audio.currentTime = 0;
        state.isPlaying = false;
        updatePlayIcon();
        const _api = winApi("set_playback_state");
        if (_api) _api(false).catch(() => {});
        const _clr = winApi("clear_taskbar_progress");
        if (_clr) _clr().catch(() => {});
    }
};

window.__onMiniMode = function (enabled) {
    document.body.classList.toggle("mini-mode", !!enabled);
};

// Библиотека изменилась (watchdog/первичный скан) — перезагрузить список
// с небольшим дебаунсом, чтобы порции скана не дёргали UI каждую секунду.
window.__onLibraryChanged = function () {
    clearTimeout(window.__libTimer);
    window.__libTimer = setTimeout(loadTracks, 300);
};

// При старте — синхронизируем Jump List с уже сохранёнными id
function _syncJumpListOnStart() {
    const _api = winApi("update_recent_tracks_by_ids");
    if (_api) _api(getRecent()).catch(() => {});
}

if (window.pywebview && window.pywebview.api) {
    _syncJumpListOnStart();
}
window.addEventListener("pywebviewready", _syncJumpListOnStart);

/* ============================================================
   INIT
   ============================================================ */
initTheme();
updateVolumeUI();
updatePlayerFavBtn();
updateShuffleUI();
updateRepeatUI();
initEQ();
initDiscordSettings();
setupMediaSessionHandlers();

// Gapless: движок спрашивает, что играть дальше, и сообщает, когда переключился сам
audio.nextProvider = () => {
    const next = peekNext(false);
    return next ? next.track : null;
};

audio.onAdvance = (track) => {
    const next = peekNext(false);
    if (next && next.pending && next.track.id === track.id) commitPendingShuffle();

    state.currentTrackId = track.id;
    state.currentTrack = track;
    state.switchingTrack = false;
    state.isPlaying = true;

    const _clr = winApi("clear_taskbar_progress");
    if (_clr) _clr().catch(() => {});
    onTrackStarted(track);
};

// Секция «Звук» в настройках
initAudioSettings(audio, {
    onDeviceLost: () => { if (state.isPlaying) togglePlay(); },
});
loadTracks();