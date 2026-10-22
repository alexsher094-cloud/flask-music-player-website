/* ============================================================
   audio-engine.js

   Два <audio>-элемента (слоты A/B):

       <audio A> → MediaElementSource → GainNode (ReplayGain) ┐
                                                              ├→ bus → [EQ из app.js] → выход
       <audio B> → MediaElementSource → GainNode (ReplayGain) ┘

   • Gapless — следующий трек заранее загружается в свободный слот и
     запускается за `latency` секунд до конца текущего. `latency` (время от
     play() до начала звука) измеряется на каждом переходе и усредняется,
     так что щель/нахлёст сходятся к нескольким миллисекундам.
   • ReplayGain — усиление считается для каждого слота отдельно
     (режим трек/альбом, предусилитель, защита от клиппинга по peak).
   • Устройство вывода — AudioContext.setSinkId (Chromium/WebView2 110+).

   AudioEngine повторяет ту часть интерфейса HTMLAudioElement, которой
   пользуется app.js (currentTime, duration, paused, volume, play(), pause(),
   addEventListener…), поэтому остальной код плеера почти не меняется.
   События (timeupdate, ended, …) пробрасываются только от АКТИВНОГО слота.
   ============================================================ */
(function () {
    "use strict";

    const SETTINGS_KEY = "alpha-music-audio";
    const LATENCY_KEY  = "alpha-music-gapless-latency";
    const LABELS_KEY   = "alpha-music-audio-labels";

    const DEFAULTS = Object.freeze({
        gapless: true,
        rgMode: "track",        // off | track | album
        rgPreamp: 0,            // дБ — для файлов с ReplayGain-тегами
        rgFallback: 0,          // дБ — для файлов без тегов
        rgPreventClip: true,    // не усиливать выше, чем позволяет peak
        sinkId: "",             // "" = системное устройство по умолчанию
        pauseOnUnplug: true,
    });

    const FORWARDED = ["timeupdate", "loadedmetadata", "durationchange", "ended",
                       "play", "pause", "playing", "waiting", "seeking", "seeked", "error"];

    const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
    const dbToGain = (db) => Math.pow(10, db / 20);

    /* ---------- настройки ---------- */
    function loadJSON(key, fallback) {
        try {
            const raw = localStorage.getItem(key);
            return raw === null ? fallback : JSON.parse(raw);
        } catch { return fallback; }
    }

    function saveJSON(key, value) {
        try { localStorage.setItem(key, JSON.stringify(value)); } catch {}
    }

    function sanitize(s) {
        s = s && typeof s === "object" ? s : {};
        const num = (v, lo, hi, d) => {
            v = Number(v);
            return Number.isFinite(v) ? clamp(v, lo, hi) : d;
        };
        const bool = (v, d) => (typeof v === "boolean" ? v : d);
        return {
            gapless:       bool(s.gapless, DEFAULTS.gapless),
            rgMode:        ["off", "track", "album"].includes(s.rgMode) ? s.rgMode : DEFAULTS.rgMode,
            rgPreamp:      num(s.rgPreamp, -15, 15, DEFAULTS.rgPreamp),
            rgFallback:    num(s.rgFallback, -15, 15, DEFAULTS.rgFallback),
            rgPreventClip: bool(s.rgPreventClip, DEFAULTS.rgPreventClip),
            sinkId:        typeof s.sinkId === "string" ? s.sinkId : DEFAULTS.sinkId,
            pauseOnUnplug: bool(s.pauseOnUnplug, DEFAULTS.pauseOnUnplug),
        };
    }

    /* ============================================================
       ДВИЖОК
       ============================================================ */
    class AudioEngine {
        constructor(primaryEl) {
            this.settings = sanitize(loadJSON(SETTINGS_KEY, {}));

            primaryEl.preload = "auto";
            this.slots = [this._makeSlot(primaryEl, 0), this._makeSlot(this._createElement(), 1)];
            this.slots.forEach((s) => this._wire(s));
            this._active = 0;

            this.ctx = null;
            this.bus = null;          // сюда app.js подключает EQ → destination
            this.sinkOk = false;      // контекст сейчас реально выводит на settings.sinkId

            this.nextProvider = null; // () => track | null  — какой трек играть следом
            this.onAdvance = null;    // (track) => void     — gapless-переход произошёл
            this.dataset = {};        // app.js кладёт сюда prevVolume

            const savedLatency = Number(loadJSON(LATENCY_KEY, 0));
            this.latency = savedLatency > 0 ? clamp(savedLatency, 0.005, 0.3) : 0.05;
            this._latencySamples = savedLatency > 0 ? 3 : 0;   // сохранённое значение уже откалибровано
            this._lastLatencySave = 0;

            this._listeners = Object.create(null);
            this._timer = null;
            this._advancing = false;
            this._lastPreloadCheck = 0;
            this._tickId = setInterval(() => this._tick(), 50);
        }

        /* ---------- слоты ---------- */
        _createElement() {
            const el = new Audio();
            el.preload = "auto";
            el.crossOrigin = "anonymous";
            return el;
        }

        _makeSlot(el, index) {
            return {
                el, index,
                track: null, trackId: null,
                source: null, gain: null,
                errored: false,
                measure: false, playCalledAt: 0,
                token: 0,
            };
        }

        _cur()  { return this.slots[this._active]; }
        _idle() { return this.slots[1 - this._active]; }

        _wire(slot) {
            FORWARDED.forEach((type) => {
                slot.el.addEventListener(type, (ev) => {
                    if (type === "playing") this._onPlaying(slot);
                    if (type === "error") slot.errored = true;
                    if (slot !== this._cur()) return;      // события неактивного слота не нужны UI
                    this._emit(type, ev);
                });
            });
        }

        /* ---------- интерфейс «как у HTMLAudioElement» ---------- */
        addEventListener(type, fn) {
            (this._listeners[type] || (this._listeners[type] = [])).push(fn);
        }

        removeEventListener(type, fn) {
            const list = this._listeners[type];
            if (list) this._listeners[type] = list.filter((f) => f !== fn);
        }

        _emit(type, ev) {
            (this._listeners[type] || []).slice().forEach((fn) => {
                try { fn.call(this, ev || { type, target: this._cur().el }); }
                catch (e) { console.error(e); }
            });
        }

        get currentTime()  { return this._cur().el.currentTime; }
        set currentTime(v) { this._cur().el.currentTime = v; }
        get duration()     { return this._cur().el.duration; }
        get paused()       { return this._cur().el.paused; }
        get ended()        { return this._cur().el.ended; }
        get playbackRate() { return 1; }

        get volume() { return this._cur().el.volume; }
        set volume(v) { this.slots.forEach((s) => { s.el.volume = v; }); }

        play() {
            this.ensureContext();
            return this._cur().el.play();
        }

        pause() {
            this._clearTimer();
            this.slots.forEach((s) => { s.token++; s.el.pause(); });
        }

        fastSeek(t) {
            const el = this._cur().el;
            if (typeof el.fastSeek === "function") el.fastSeek(t);
            else el.currentTime = t;
        }

        /* ---------- AudioContext ---------- */
        ensureContext() {
            if (this.ctx) {
                if (this.ctx.state === "suspended") this.ctx.resume().catch(() => {});
                return this.ctx;
            }
            const Ctx = window.AudioContext || window.webkitAudioContext;
            if (!Ctx) return null;

            try {
                this.ctx = new Ctx();
                this.bus = this.ctx.createGain();
                this.slots.forEach((slot) => {
                    try {
                        slot.source = this.ctx.createMediaElementSource(slot.el);
                        slot.gain = this.ctx.createGain();
                        slot.source.connect(slot.gain);
                        slot.gain.connect(this.bus);
                        this._applyGain(slot, true);
                    } catch (e) {
                        console.error("MediaElementSource failed:", e);
                    }
                });
            } catch (e) {
                console.error("AudioContext init failed:", e);
                this.ctx = null;
                return null;
            }

            if (this.ctx.state === "suspended") this.ctx.resume().catch(() => {});
            if (this.settings.sinkId) this.setSinkId(this.settings.sinkId, false);
            return this.ctx;
        }

        /* ---------- устройство вывода ---------- */
        static get supportsSink() {
            const C = window.AudioContext || window.webkitAudioContext;
            return !!(C && C.prototype && typeof C.prototype.setSinkId === "function");
        }

        async setSinkId(id, persist = true) {
            id = id || "";
            const ctx = this.ensureContext();
            if (!ctx || typeof ctx.setSinkId !== "function") {
                this.sinkOk = false;
                return false;
            }
            try {
                await ctx.setSinkId(id);
                this.sinkOk = true;
                if (persist) {
                    this.settings.sinkId = id;
                    saveJSON(SETTINGS_KEY, this.settings);
                }
                return true;
            } catch (e) {
                console.warn("setSinkId failed:", e);
                this.sinkOk = false;
                return false;
            }
        }

        /* ---------- ReplayGain ---------- */
        static replayGain(track, s) {
            if (s.rgMode === "off") return 1;

            const rg = track && track.rg;
            let db = null, peak = 0;
            if (rg) {
                const hasT = Number.isFinite(rg.tg);
                const hasA = Number.isFinite(rg.ag);
                if (s.rgMode === "album" && hasA)  { db = rg.ag; peak = rg.ap; }
                else if (hasT)                     { db = rg.tg; peak = rg.tp; }
                else if (hasA)                     { db = rg.ag; peak = rg.ap; }
            }

            if (db === null) return dbToGain(s.rgFallback);

            let g = dbToGain(clamp(db, -40, 30) + s.rgPreamp);
            if (s.rgPreventClip && Number.isFinite(peak) && peak > 0) g = Math.min(g, 1 / peak);
            return g;
        }

        _applyGain(slot, immediate) {
            if (!this.ctx || !slot.gain) return;
            const g = clamp(AudioEngine.replayGain(slot.track, this.settings), 0, 16);
            const p = slot.gain.gain;
            const t = this.ctx.currentTime;
            p.cancelScheduledValues(t);
            if (immediate) p.setValueAtTime(g, t);
            else p.setTargetAtTime(g, t, 0.05);
        }

        updateSettings(patch) {
            const prev = this.settings;
            this.settings = sanitize({ ...prev, ...patch });
            saveJSON(SETTINGS_KEY, this.settings);

            if (["rgMode", "rgPreamp", "rgFallback", "rgPreventClip"].some((k) => k in patch)) {
                this.slots.forEach((s) => this._applyGain(s, false));
            }
            if (prev.gapless && !this.settings.gapless) {
                this._clearTimer();
                const idle = this._idle();
                if (idle.el.paused || idle.el.ended) this._unload(idle);
            }
            return this.settings;
        }

        /* ---------- загрузка / запуск трека ---------- */
        _load(slot, track) {
            slot.token++;
            slot.track = track;
            slot.trackId = track.id;
            slot.errored = false;
            slot.measure = false;
            slot.el.src = "/api/stream/" + encodeURIComponent(track.id);
            this._applyGain(slot, true);
        }

        _unload(slot) {
            slot.token++;
            slot.el.pause();
            slot.el.removeAttribute("src");
            slot.el.load();
            slot.track = null;
            slot.trackId = null;
            slot.errored = false;
        }

        // короткий fade-out, чтобы смена трека не щёлкала
        _fadeStop(slot) {
            const token = ++slot.token;
            if (this.ctx && slot.gain && !slot.el.paused) {
                const t = this.ctx.currentTime;
                slot.gain.gain.cancelScheduledValues(t);
                slot.gain.gain.setTargetAtTime(0, t, 0.004);
                setTimeout(() => { if (slot.token === token) slot.el.pause(); }, 30);
            } else {
                slot.el.pause();
            }
        }

        /**
         * Запустить трек. Если он уже предзагружен как «следующий» — старт мгновенный.
         * Возвращает Promise от el.play().
         */
        playTrack(track) {
            this.ensureContext();
            this._clearTimer();
            this._advancing = false;

            const cur = this._cur();
            const idle = this._idle();
            const idleReady = idle.trackId === track.id && !idle.errored
                && idle.el.readyState >= 2 && (idle.el.paused || idle.el.ended);

            if (this.settings.gapless && idleReady) {
                this._fadeStop(cur);
                this._active = idle.index;
                if (idle.el.currentTime > 0.05) idle.el.currentTime = 0;
                this._applyGain(idle, true);
                const p = idle.el.play();
                this._emit("loadedmetadata");
                return p;
            }

            if (!(idle.el.paused || idle.el.ended)) this._fadeStop(idle);   // доигрывающий хвост
            this._load(cur, track);
            return cur.el.play();
        }

        /* ---------- gapless ---------- */
        _lead() { return Math.max(0, this.latency - 0.003); }

        _clearTimer() {
            if (this._timer) { clearTimeout(this._timer); this._timer = null; }
        }

        _tick() {
            const cur = this._cur();
            const el = cur.el;
            if (!cur.track || el.paused) return;
            if (!(el.duration > 0) || !isFinite(el.duration)) return;
            if (!this.settings.gapless) return;

            const remaining = el.duration - el.currentTime;
            const now = performance.now();

            if (now - this._lastPreloadCheck > 400) {
                this._lastPreloadCheck = now;
                if (el.currentTime > 1 || remaining < 15) this._ensurePreload();
            }

            if (!this._timer && !this._advancing && remaining <= 0.4) {
                const wait = Math.max(0, (remaining - this._lead()) * 1000);
                this._timer = setTimeout(() => { this._timer = null; this._fire(); }, wait);
            }
        }

        _ensurePreload() {
            const idle = this._idle();
            if (!(idle.el.paused || idle.el.ended)) return;      // ещё доигрывает хвост
            const next = this.nextProvider ? this.nextProvider() : null;
            if (!next) return;

            if (idle.trackId === next.id) {
                // тот же трек уже лежит в слоте (repeat / круг очереди) — «перемотать» в начало
                if (idle.el.ended || idle.el.currentTime > 0.05) {
                    try { idle.el.currentTime = 0; } catch {}
                }
                return;
            }
            this._load(idle, next);
        }

        _canAdvance() {
            const next = this.nextProvider ? this.nextProvider() : null;
            const idle = this._idle();
            return !!(next && idle.trackId === next.id && !idle.errored
                && idle.el.readyState >= 3
                && (idle.el.paused || idle.el.ended)
                && idle.el.currentTime < 0.05);
        }

        _fire() {
            const cur = this._cur();
            if (this._advancing || cur.el.paused) return;
            const remaining = cur.el.duration - cur.el.currentTime;
            if (remaining > this._lead() + 0.03) return;     // перемотали назад — tick пересчитает
            if (!this._canAdvance()) return;                 // не готов — отработает обычный 'ended'
            this._advance();
        }

        _advance() {
            const next = this._idle();
            const track = next.track;

            this._advancing = true;
            next.measure = true;
            next.playCalledAt = performance.now();
            this._active = next.index;                       // старый слот доигрывает сам
            this._applyGain(next, true);

            const p = next.el.play();
            if (p && typeof p.catch === "function") {
                p.catch((e) => {
                    console.warn("gapless play failed:", e);
                    this._advancing = false;
                });
            }

            this._emit("loadedmetadata");                    // у нового слота метаданные уже были
            if (this.onAdvance) {
                try { this.onAdvance(track); } catch (e) { console.error(e); }
            }
        }

        _onPlaying(slot) {
            if (slot.measure && slot.playCalledAt) {
                const dt = (performance.now() - slot.playCalledAt) / 1000;
                slot.playCalledAt = 0;
                slot.measure = false;
                if (dt > 0.003 && dt < 0.5) {
                    // первые замеры весомее, дальше — плавное усреднение
                    const a = this._latencySamples < 3 ? 0.65 : 0.3;
                    this._latencySamples++;
                    this.latency = clamp(this.latency * (1 - a) + dt * a, 0.005, 0.3);
                    const now = performance.now();
                    if (now - this._lastLatencySave > 5000) {
                        this._lastLatencySave = now;
                        saveJSON(LATENCY_KEY, this.latency);
                    }
                }
            }
            if (slot === this._cur()) this._advancing = false;
        }
    }

    /* ============================================================
       УСТРОЙСТВА ВЫВОДА + UI НАСТРОЕК
       ============================================================ */
    const isVirtual = (id) => id === "default" || id === "communications";

    async function listOutputs() {
        if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) {
            return { devices: [], labeled: false };
        }
        try {
            const all = await navigator.mediaDevices.enumerateDevices();
            const devices = all.filter((d) => d.kind === "audiooutput");
            return { devices, labeled: devices.some((d) => d.label) };
        } catch {
            return { devices: [], labeled: false };
        }
    }

    function realIds(devices) {
        return new Set(devices.filter((d) => d.deviceId && !isVirtual(d.deviceId)).map((d) => d.deviceId));
    }

    // Какое физическое устройство сейчас «по умолчанию» (по groupId виртуальной записи "default")
    function defaultRealId(devices) {
        const def = devices.find((d) => d.deviceId === "default");
        if (!def || !def.groupId) return null;
        const real = devices.find((d) => d.deviceId && !isVirtual(d.deviceId) && d.groupId === def.groupId);
        return real ? real.deviceId : null;
    }

    /**
     * Подключает секцию «Звук» в настройках.
     * hooks.onDeviceLost — вызвать, когда пропало устройство вывода (app.js ставит на паузу).
     */
    function initAudioSettings(engine, hooks) {
        hooks = hooks || {};
        const $ = (id) => document.getElementById(id);
        const el = {
            gapless:  $("audio-gapless"),
            mode:     $("audio-rg-mode"),
            preamp:   $("audio-rg-preamp"),
            preampV:  $("audio-rg-preamp-value"),
            fallback: $("audio-rg-fallback"),
            fallbackV:$("audio-rg-fallback-value"),
            clip:     $("audio-rg-clip"),
            device:   $("audio-device"),
            refresh:  $("audio-device-refresh"),
            unlock:   $("audio-device-unlock"),
            hint:     $("audio-device-hint"),
            unplug:   $("audio-unplug"),
            reset:    $("audio-reset"),
        };
        if (!el.mode) return;                     // секции нет в разметке

        const fmtDb = (v) => (v > 0 ? "+" : "") + v.toFixed(1) + " дБ";

        function syncControls() {
            const s = engine.settings;
            el.gapless.checked = s.gapless;
            el.mode.value = s.rgMode;
            el.preamp.value = s.rgPreamp;
            el.preampV.textContent = fmtDb(s.rgPreamp);
            el.fallback.value = s.rgFallback;
            el.fallbackV.textContent = fmtDb(s.rgFallback);
            el.clip.checked = s.rgPreventClip;
            el.unplug.checked = s.pauseOnUnplug;
            const off = s.rgMode === "off";
            [el.preamp, el.fallback, el.clip].forEach((x) => { x.disabled = off; });
        }

        el.gapless.addEventListener("change", () => engine.updateSettings({ gapless: el.gapless.checked }));
        el.mode.addEventListener("change", () => { engine.updateSettings({ rgMode: el.mode.value }); syncControls(); });
        el.preamp.addEventListener("input", () => {
            el.preampV.textContent = fmtDb(Number(el.preamp.value));
            engine.updateSettings({ rgPreamp: Number(el.preamp.value) });
        });
        el.fallback.addEventListener("input", () => {
            el.fallbackV.textContent = fmtDb(Number(el.fallback.value));
            engine.updateSettings({ rgFallback: Number(el.fallback.value) });
        });
        el.clip.addEventListener("change", () => engine.updateSettings({ rgPreventClip: el.clip.checked }));
        el.unplug.addEventListener("change", () => engine.updateSettings({ pauseOnUnplug: el.unplug.checked }));

        /* ---------- устройства ---------- */
        let prevDefault = null;

        function setHint(text) { if (el.hint) el.hint.textContent = text || ""; }

        function fillDevices(devices, labeled) {
            const saved = engine.settings.sinkId;
            el.device.innerHTML = "";

            const optDefault = document.createElement("option");
            optDefault.value = "";
            optDefault.textContent = "Системное по умолчанию";
            el.device.appendChild(optDefault);

            let n = 0;
            devices.forEach((d) => {
                if (!d.deviceId || isVirtual(d.deviceId)) return;
                n++;
                const o = document.createElement("option");
                o.value = d.deviceId;
                o.textContent = d.label || ("Устройство " + n);
                el.device.appendChild(o);
            });

            const present = !saved || [...el.device.options].some((o) => o.value === saved);
            el.device.value = present ? saved : "";

            el.unlock.classList.toggle("hidden", labeled || n === 0);
            if (!present) setHint("Сохранённое устройство сейчас недоступно — используется системное.");
            else if (!labeled && n > 0) setHint("Названия устройств скрыты браузером. Нажмите «Показать названия».");
            else setHint("");
        }

        async function refreshDevices() {
            if (!AudioEngine.supportsSink) {
                el.device.disabled = true;
                el.refresh.disabled = true;
                el.unlock.classList.add("hidden");
                setHint("Выбор устройства недоступен: версия WebView2 слишком старая (нужен Chromium 110+).");
                return [];
            }
            const { devices, labeled } = await listOutputs();
            fillDevices(devices, labeled);
            prevDefault = defaultRealId(devices);
            return devices;
        }

        el.device.addEventListener("change", async () => {
            const wanted = el.device.value;
            const ok = await engine.setSinkId(wanted);
            if (!ok) {
                el.device.value = engine.settings.sinkId;
                setHint("Не удалось переключить устройство вывода.");
            } else {
                setHint("");
            }
        });

        el.refresh.addEventListener("click", refreshDevices);

        el.unlock.addEventListener("click", async () => {
            try {
                // Доступ к «микрофону» нужен только затем, чтобы Chromium показал имена устройств.
                // Поток сразу останавливается, ничего не записывается.
                const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                stream.getTracks().forEach((t) => t.stop());
                try { localStorage.setItem(LABELS_KEY, "1"); } catch {}
            } catch (e) {
                setHint("Доступ не разрешён — названия устройств останутся скрытыми.");
                return;
            }
            refreshDevices();
        });

        // Устройства подключили/отключили
        if (navigator.mediaDevices && navigator.mediaDevices.addEventListener) {
            navigator.mediaDevices.addEventListener("devicechange", async () => {
                const before = prevDefault;
                const devices = await refreshDevices();
                if (!devices.length) return;

                const ids = realIds(devices);
                const saved = engine.settings.sinkId;
                let lost = false;

                if (saved) {
                    if (engine.sinkOk && !ids.has(saved)) {
                        lost = true;
                        await engine.setSinkId("", false);     // вернуться на системное
                        engine.sinkOk = false;
                        fillDevices(devices, devices.some((d) => d.label));
                    } else if (!engine.sinkOk && ids.has(saved)) {
                        await engine.setSinkId(saved, false);  // устройство вернулось
                        fillDevices(devices, devices.some((d) => d.label));
                    }
                } else if (before && !ids.has(before)) {
                    lost = true;                               // отключили текущее «по умолчанию»
                }

                if (lost && engine.settings.pauseOnUnplug && !engine.paused && hooks.onDeviceLost) {
                    hooks.onDeviceLost();
                }
            });
        }

        el.reset.addEventListener("click", async () => {
            engine.updateSettings({ ...DEFAULTS });
            await engine.setSinkId("", true);
            syncControls();
            refreshDevices();
        });

        syncControls();
        refreshDevices();
    }

    /* ---------- экспорт ---------- */
    window.AudioEngine = AudioEngine;
    window.initAudioSettings = initAudioSettings;
    if (typeof module !== "undefined" && module.exports) {
        module.exports = { AudioEngine, sanitize, DEFAULTS };
    }
})();
