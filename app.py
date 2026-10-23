# app.py
import math
import os
import logging
from flask import (Flask, jsonify, render_template, request, send_file,
                   abort)
from mutagen import File as MutagenFile
from mutagen.id3 import ID3
from mutagen.apev2 import APEv2
from mutagen.flac import FLAC
from mutagen.mp4 import MP4

import app_settings
from filename_parser import (UNKNOWN_ARTIST, normalize_album,
                             normalize_artist, parse_filename)
from library import Library, track_id_for  # noqa: F401  (track_id_for реэкспортируется для desktop.py)

app = Flask(__name__)

MUSIC_DIR = app_settings.DEFAULT_MUSIC_DIR
ALLOWED_EXT = {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav"}

# Файлы, открытые «извне» (двойной клик, «Открыть с помощью», контекстное меню):
# id -> абсолютный путь. Заполняется из desktop.py, отдаётся теми же роутами.
EXTERNAL_TRACKS = {}


def _data_dir() -> str:
    # совпадает с instance.data_dir() на Windows: %APPDATA%\Music
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "Music")


# ============================================================
# ТЕГИ
# ============================================================
def extract_cover(path: str):
    """Возвращает (bytes, mime) обложки из тегов или (None, None)."""
    try:
        ext = os.path.splitext(path)[1].lower()

        if ext == ".mp3":
            audio = ID3(path)
            for tag in audio.getall("APIC"):
                return tag.data, tag.mime

        elif ext == ".flac":
            audio = FLAC(path)
            if audio.pictures:
                return audio.pictures[0].data, audio.pictures[0].mime

        elif ext in (".m4a", ".mp4"):
            audio = MP4(path)
            if "covr" in audio:
                data = bytes(audio["covr"][0])
                mime = "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
                return data, mime

    except Exception:
        pass
    return None, None


_ARTIST_PLACEHOLDERS = {
    "", UNKNOWN_ARTIST.casefold(), "неизвестен", "unknown artist", "unknown",
    "artist unknown", "без исполнителя", "n/a",
}
_TITLE_PLACEHOLDERS = {
    "", "unknown", "unknown title", "untitled", "без названия", "неизвестно",
}
_ALBUM_PLACEHOLDERS = {
    "", "unknown", "unknown album", "неизвестен", "без альбома",
}


def _tag_value(tags, key: str) -> str:
    value = tags.get(key)
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value).strip() if value is not None else ""


def _is_placeholder(value: str, placeholders) -> bool:
    return str(value or "").strip().casefold() in placeholders


def _same_as_filename(value: str, filename: str) -> bool:
    value = str(value or "").strip()
    if not value:
        return False
    if os.path.splitext(value)[1].lower() in ALLOWED_EXT:
        value = os.path.splitext(value)[0]
    norm = lambda s: " ".join(s.split()).casefold()
    return norm(value) == norm(filename)


def extract_metadata(path: str) -> dict:
    filename = os.path.splitext(os.path.basename(path))[0]
    parsed = parse_filename(filename)
    data = {
        "title": parsed["title"] or filename,
        "artist": normalize_artist(parsed["artist"]) or UNKNOWN_ARTIST,
        "album": normalize_album(parsed["album"]) or "",
        "duration": 0,
        "date": "",             # год/дата из тега
        "mtime": 0,             # время модификации файла
    }

    try:
        data["mtime"] = int(os.path.getmtime(path))
    except Exception:
        pass

    tag_title = tag_artist = tag_album = tag_albumartist = ""

    try:
        audio = MutagenFile(path, easy=True)
        if audio is None:
            return data

        if audio.tags:
            tag_title = _tag_value(audio.tags, "title")
            tag_artist = _tag_value(audio.tags, "artist")
            tag_album = _tag_value(audio.tags, "album")
            tag_albumartist = normalize_artist(
                _tag_value(audio.tags, "albumartist"))

            if tag_title:
                data["title"] = tag_title
            if tag_artist:
                data["artist"] = tag_artist
            if tag_album:
                data["album"] = tag_album
            # Дата/год из тега (может быть "2023" или "2023-05-15")
            data["date"] = (_tag_value(audio.tags, "date")
                            or _tag_value(audio.tags, "year"))

        if hasattr(audio, "info") and audio.info:
            data["duration"] = int(audio.info.length)

    except Exception:
        pass

    # Убираем скобки и приводим список исполнителей к единому виду.
    data["artist"] = normalize_artist(data["artist"])
    data["album"] = normalize_album(data["album"])

    # Некоторые теги содержат всё имя файла целиком — в этом случае
    # используем разобранные части, а не мусорную строку.
    if (_is_placeholder(data["title"], _TITLE_PLACEHOLDERS)
            or _same_as_filename(data["title"], filename)):
        if parsed["title"]:
            data["title"] = parsed["title"]

    # Если исполнителя в тегах нет (или там заглушка), берём его из имени.
    artist_missing = _is_placeholder(data["artist"], _ARTIST_PLACEHOLDERS)
    if artist_missing:
        if parsed["artist"]:
            data["artist"] = parsed["artist"]
        elif (tag_albumartist
              and not _is_placeholder(tag_albumartist,
                                      _ARTIST_PLACEHOLDERS | {"various artists"})):
            data["artist"] = tag_albumartist

    # Название альбома из имени файла — только если тег пуст.
    if _is_placeholder(data["album"], _ALBUM_PLACEHOLDERS) and parsed["album"]:
        data["album"] = parsed["album"]

    # Иногда имя файла бесполезно, но полное имя с исполнителем записано
    # в тег title. Разбираем и его как запасной вариант.
    if artist_missing and tag_title and not parsed["artist"]:
        parsed_tag = parse_filename(tag_title)
        if parsed_tag["artist"]:
            data["artist"] = parsed_tag["artist"]
            if parsed_tag["title"]:
                data["title"] = parsed_tag["title"]
            if (parsed_tag["album"]
                    and _is_placeholder(data["album"], _ALBUM_PLACEHOLDERS)):
                data["album"] = parsed_tag["album"]

    data["artist"] = normalize_artist(data["artist"]) or UNKNOWN_ARTIST
    data["album"] = normalize_album(data["album"])
    return data


# ============================================================
# REPLAYGAIN
# ============================================================
_RG_KEYS = {
    "replaygain_track_gain": "tg", "replaygain_track_peak": "tp",
    "replaygain_album_gain": "ag", "replaygain_album_peak": "ap",
}


def _parse_float(value):
    """'-6.54 dB' / b'+3,2' / ['0.98'] -> float | None"""
    if isinstance(value, (list, tuple)):
        if not value:
            return None
        value = value[0]
    if isinstance(value, bytes):
        value = value.decode("utf-8", "ignore")
    s = str(value).strip().lower().replace(",", ".").replace("db", "").strip()
    try:
        f = float(s)
    except ValueError:
        return None
    return f if math.isfinite(f) else None


def _collect_rg(pairs):
    """
    pairs — итератор (ключ, значение) из любого формата тегов.
    Возвращает {"tg","tp","ag","ap"} (gain в дБ, peak линейный) или None,
    если нет ни трекового, ни альбомного gain.
    """
    out, r128 = {}, {}
    for key, value in pairs:
        # iTunes: "----:com.apple.iTunes:replaygain_track_gain" -> последний сегмент
        k = str(key).lower().rsplit(":", 1)[-1].strip()
        slot = _RG_KEYS.get(k)
        if slot:
            f = _parse_float(value)
            if f is not None:
                out.setdefault(slot, f)
        elif k in ("r128_track_gain", "r128_album_gain"):
            f = _parse_float(value)
            if f is not None:
                r128["tg" if "track" in k else "ag"] = f

    # Opus: R128_*_GAIN — Q7.8 относительно -23 LUFS; ReplayGain считает от -18 LUFS (+5 дБ)
    for slot in ("tg", "ag"):
        if slot not in out and slot in r128:
            out[slot] = r128[slot] / 256.0 + 5.0

    if "tg" not in out and "ag" not in out:
        return None
    for slot in ("tp", "ap"):
        if out.get(slot) is not None and out[slot] <= 0:
            out.pop(slot)
    return {k: out.get(k) for k in ("tg", "tp", "ag", "ap")}


def extract_replaygain(path: str):
    """ReplayGain-теги файла: {"tg","tp","ag","ap"} или None."""
    try:
        ext = os.path.splitext(path)[1].lower()

        if ext == ".mp3":
            pairs = []
            try:
                for fr in ID3(path).getall("TXXX"):
                    if fr.text:
                        pairs.append((fr.desc, fr.text[0]))
            except Exception:
                pass
            rg = _collect_rg(pairs)
            if rg is None:                      # foobar2000 пишет RG в APEv2 даже в mp3
                try:
                    rg = _collect_rg(APEv2(path).items())
                except Exception:
                    pass
            return rg

        if ext in (".m4a", ".mp4"):
            tags = MP4(path).tags or {}
            return _collect_rg((k, v) for k, v in tags.items() if str(k).startswith("----"))

        if ext in (".flac", ".ogg", ".opus"):
            audio = MutagenFile(path)
            if audio is not None and audio.tags:
                return _collect_rg(audio.tags.items())

    except Exception:
        pass
    return None


# ============================================================
# БИБЛИОТЕКА (SQLite-кэш + превью + watchdog)
# ============================================================
# Потоки стартуют только после lib.start() — см. desktop.py / __main__ ниже.
lib = Library(
    music_dirs=app_settings.load_music_dirs(),
    db_path=os.path.join(_data_dir(), "library.db"),
    cover_dir=os.path.join(_data_dir(), "covers"),
    read_meta=extract_metadata,
    read_cover=extract_cover,
    allowed_ext=ALLOWED_EXT,
    read_rg=extract_replaygain,
    legacy_root_path=MUSIC_DIR,
)


def find_track_by_id(track_id: str):
    ext_path = EXTERNAL_TRACKS.get(track_id)
    if ext_path and os.path.isfile(ext_path):
        return ext_path
    return lib.path_for_id(track_id)


# Jump list (desktop.py) раньше пользовался отдельным кэшем путей;
# теперь это тот же быстрый поиск по индексу в БД.
find_track_path = find_track_by_id


# ============================================================
# ROUTES
# ============================================================
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/tracks")
def api_tracks():
    return jsonify({"tracks": lib.tracks(), "version": lib.version})


@app.route("/api/library/version")
def api_library_version():
    return jsonify({"version": lib.version})


@app.route("/api/library/rescan", methods=["POST"])
def api_library_rescan():
    lib.request_rescan()
    return jsonify(ok=True)


def _library_payload() -> dict:
    folders = lib.folders()
    return {
        "folders": folders,
        "track_count": lib.track_count(),
        "scanning": lib.is_scanning(),
        "version": lib.version,
    }


@app.route("/api/library/folders")
def api_library_folders():
    return jsonify(_library_payload())


@app.route("/api/library/folders", methods=["POST"])
def api_library_folder_add():
    data = request.get_json(silent=True) or {}
    ok, error = lib.add_music_dir(data.get("path", ""))
    if not ok:
        return jsonify({"ok": False, "error": error}), 400
    app_settings.save_music_dirs(lib.music_dirs())
    return jsonify({"ok": True, **_library_payload()})


@app.route("/api/library/folders/remove", methods=["POST"])
def api_library_folder_remove():
    data = request.get_json(silent=True) or {}
    ok, error = lib.remove_music_dir(data.get("path", ""))
    if not ok:
        return jsonify({"ok": False, "error": error}), 400
    app_settings.save_music_dirs(lib.music_dirs())
    return jsonify({"ok": True, **_library_payload()})


@app.route("/api/stream/<track_id>")
def api_stream(track_id):
    path = find_track_by_id(track_id)
    if not path:
        abort(404)

    ext = os.path.splitext(path)[1].lower()
    mime_map = {
        ".mp3": "audio/mpeg",
        ".flac": "audio/flac",
        ".m4a": "audio/mp4",
        ".ogg": "audio/ogg",
        ".opus": "audio/opus",
        ".wav": "audio/wav",
    }
    mimetype = mime_map.get(ext, "audio/mpeg")

    return send_file(path, mimetype=mimetype, conditional=True)


@app.route("/api/cover/<track_id>")
def api_cover(track_id):
    """
    /api/cover/<id>?s=128         — превью (64/128/256/512/1024, по умолчанию 512)
    /api/cover/<id>?s=128&v=<h>   — то же, но с «вечным» кэшем браузера
                                    (h — поле `cover` из /api/tracks)
    Всегда отдаётся JPEG.
    """
    size = request.args.get("s", type=int)

    ext_path = EXTERNAL_TRACKS.get(track_id)
    if ext_path and os.path.isfile(ext_path):
        thumb = lib.thumb_path(ext_path, size)
    else:
        info = lib.get(track_id)
        if not info:
            abort(404)
        thumb = lib.thumb_path(info["path"], size, info["cover"])

    if not thumb:
        abort(404)

    resp = send_file(thumb, mimetype="image/jpeg", conditional=True, max_age=0)
    if request.args.get("v"):
        # имя файла превью = хэш содержимого, значит при смене обложки сменится и v
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resp


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"

    # с reloader'ом процесс запускается дважды — библиотеку стартуем только в рабочем
    if not debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        lib.start()

    print(" Музыка:")
    for folder in lib.music_dirs():
        print(f"   {folder}")
    print(f" Кэш:    {_data_dir()}")
    print(f" Debug: {'ON' if debug else 'OFF'}")
    app.run(host="0.0.0.0", port=5000, debug=debug)
