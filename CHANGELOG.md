# Changelog

## 1.0.2 — 2026-10-27

- fixed favorites being lost after restart: favorites are now stored on the
  server in `%APPDATA%\Music\settings.json` and no longer depend on the
  WebView2 profile or the local port;
- WebView2 now uses a persistent profile instead of a temporary private one,
  so theme, equalizer, volume, shuffle/repeat and recent tracks also survive
  restarts;
- the app remembers its last port, keeping the page origin (and localStorage)
  stable between launches;
- existing favorites are migrated from localStorage on the first launch;
- malformed or duplicate favorite entries are ignored.

## 1.0.1 — 2026-10-27

- fixed library folders being reset on the next launch: window/tray settings
  are now merged atomically and no longer overwrite `music_dirs`;
- stale window snapshots cannot re-add a folder removed from the library;
- if `music_dirs` was already lost by 1.0.0, the folder list is recovered
  from the library database on first start.

## 1.0.0 — 2026-10-27

First standalone Windows release.

### Player

- frameless pywebview / WebView2 window with a custom title bar;
- mini-player mode with fixed height, drag handling and screen-snap protection;
- tray icon, close-to-tray, autostart with Windows;
- media keys, taskbar thumbnail buttons and progress;
- jump list, file associations and Explorer context-menu entries;
- Discord Rich Presence;
- multi-folder library with SQLite cache, cover thumbnails and a file watcher;
- metadata parsing from filename and tags, multi-artist support;
- ReplayGain normalization, equalizer, themes;
- single-instance mode: opening a track hands it to the running player.

### Distribution

- PyInstaller onedir build (`Music.spec`) with version resource and icon;
- Inno Setup installer (`installer/setup.iss`), per-user install, no UAC;
- installer shortcuts and the running app share the `Music.Player` AppUserModelID,
  so the taskbar button uses the app icon and pins/groups correctly;
- `build.ps1` builds both artefacts and reads the version from `version.py`;
- release build writes diagnostics to `%APPDATA%\Music\music.log`;
- installed build uses `%USERPROFILE%\Music` as the initial library folder;
- a corrupted SQLite cache is backed up and recreated automatically on startup.
