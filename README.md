# Music

Local music player: a Flask + Web Audio web version and a standalone Windows app
built with pywebview / WebView2.

## Windows app

### Install

1. Download `Music-Setup-<version>.exe` from [Releases](../../releases).
2. Run the installer. It installs per user (no administrator rights) into
   `%LOCALAPPDATA%\Programs\Music`.
3. Open **Settings → Библиотека → Добавить папку** and add your music folders.

Requirements: Windows 10/11 x64 and Microsoft Edge WebView2 Runtime
(preinstalled on Windows 11; the installer warns if it is missing).

Settings and cache live in `%APPDATA%\Music`:

| File | Purpose |
| --- | --- |
| `settings.json` | window geometry, folders, Discord, UI settings |
| `library.db` | SQLite metadata cache |
| `covers/` | cached cover thumbnails |
| `music.log` | diagnostics log (release build) |

The installed build uses `%USERPROFILE%\Music` as the initial library folder;
a source checkout uses the `music/` folder next to the code.

### Features

- frameless window, custom title bar, mini-player mode;
- tray icon, close-to-tray, autostart with Windows;
- media keys, taskbar thumbnail buttons and progress;
- jump list, file associations and Explorer context-menu entries;
- Discord Rich Presence;
- multi-folder library with SQLite cache, cover thumbnails and a file watcher;
- ReplayGain normalization, equalizer, themes;
- single instance: opening another track reuses the running player.

### Build from source

Requirements: Python 3.10+, [Inno Setup 6.3+](https://jrsoftware.org/isdl.php)
(only for the installer).

```powershell
python -m pip install -r requirments.txt pyinstaller
.\build.ps1                # app + installer in dist\
.\build.ps1 -NoInstaller   # only dist\Music\Music.exe
```

The version comes from `version.py` and is embedded into the executable and the
installer name. `Music.spec` is the PyInstaller build description,
`installer/setup.iss` is the Inno Setup script.

To run from source instead:

```powershell
python desktop.py   # Windows shell
python app.py       # web version in the browser
```

## Web version

1. Run `app.py`.

<img width="858" height="138" alt="image" src="https://github.com/user-attachments/assets/d2f70a7b-5775-4e60-b530-9b36a9a19b85" />

2. Add music to the directory.

<img width="720" height="437" alt="image" src="https://github.com/user-attachments/assets/fa6d62b6-28cb-40a9-ae88-52f9857e6596" />

3. Enter the second IP address from the log into the search bar.

<img width="548" height="237" alt="image" src="https://github.com/user-attachments/assets/174d7b47-e33e-4b9d-96f6-1aaaca5dd64d" />

4. Ready!

<img width="1850" height="961" alt="image" src="https://github.com/user-attachments/assets/603d8be7-af45-4006-a8ee-69388ab3f91f" />
<img width="1918" height="1021" alt="image" src="https://github.com/user-attachments/assets/059d4d49-61d5-4889-b836-93977f485efa" />
<img width="1918" height="1017" alt="image" src="https://github.com/user-attachments/assets/d5ed7280-52b2-4978-b727-c89fb2dae822" />
<img width="1918" height="980" alt="image" src="https://github.com/user-attachments/assets/1b27ec18-8470-48b5-aea8-579473e598a1" />

## About the project

Before this, I didn’t approach programming with the same level of seriousness as I do now.
I tried to minimize my technical debt as much as I could. Next,
I’m going to work on writing full‑fledged standalone versions of this app for Android and Windows, with all the features that these operating systems can offer.
To do this, I’ve divided the repository into branches for Web, Android, and Windows. I think you’ll like this app, as I’ve invested a lot of time and effort in it.

## Discord Rich Presence (Windows)

The Windows app can show the current track in your Discord profile.

1. Open https://discord.com/developers/applications and create a new application (the name will be shown in Discord).
2. Copy its **Application ID**.
3. In the player, open **Settings → Discord**, paste the ID, enable the toggle and click **Save**.
4. Keep the Discord desktop app running while you listen.

The Application ID can also be set through the `MUSIC_DISCORD_CLIENT_ID` environment variable or in `%APPDATA%\Music\settings.json` (`discord_client_id`).

Optional: upload a 512×512 image in the Developer Portal → **Rich Presence → Art Assets**, then put its key into `discord_large_image` in `settings.json`. Without it Discord shows the application icon.

If `pypresence` is missing, install it with `pip install -r requirments.txt`.
