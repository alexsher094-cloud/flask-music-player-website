# app.py
import os
import logging
from flask import (Flask, jsonify, render_template, request, send_file,
                   abort)
from mutagen import File as MutagenFile
from mutagen.id3 import ID3
from mutagen.flac import FLAC
from mutagen.mp4 import MP4

from library import Library, track_id_for  # noqa: F401  (track_id_for реэкспортируется для desktop.py)

app = Flask(__name__)

MUSIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "music")
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


def extract_metadata(path: str) -> dict:
    filename = os.path.splitext(os.path.basename(path))[0]
    data = {
        "title": filename,
        "artist": "Неизвестный исполнитель",
        "album": "",
        "duration": 0,
        "date": "",             # год/дата из тега
        "mtime": 0,             # время модификации файла
    }

    try:
        data["mtime"] = int(os.path.getmtime(path))
    except Exception:
        pass

    try:
        audio = MutagenFile(path, easy=True)
        if audio is None:
            return data

        if audio.tags:
            data["title"] = (audio.tags.get("title") or [filename])[0]
            data["artist"] = (audio.tags.get("artist") or [data["artist"]])[0]
            data["album"] = (audio.tags.get("album") or [""])[0]
            # Дата/год из тега (может быть "2023" или "2023-05-15")
            date_tag = audio.tags.get("date") or audio.tags.get("year") or [""]
            data["date"] = str(date_tag[0]) if date_tag[0] else ""

        if hasattr(audio, "info") and audio.info:
            data["duration"] = int(audio.info.length)

    except Exception:
        pass

    return data


# ============================================================
# БИБЛИОТЕКА (SQLite-кэш + превью + watchdog)
# ============================================================
# Потоки стартуют только после lib.start() — см. desktop.py / __main__ ниже.
lib = Library(
    music_dir=MUSIC_DIR,
    db_path=os.path.join(_data_dir(), "library.db"),
    cover_dir=os.path.join(_data_dir(), "covers"),
    read_meta=extract_metadata,
    read_cover=extract_cover,
    allowed_ext=ALLOWED_EXT,
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
    if not os.path.isdir(MUSIC_DIR):
        os.makedirs(MUSIC_DIR)
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"

    # с reloader'ом процесс запускается дважды — библиотеку стартуем только в рабочем
    if not debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        lib.start()

    print(f" Музыка: {MUSIC_DIR}")
    print(f" Кэш:    {_data_dir()}")
    print(f" Debug: {'ON' if debug else 'OFF'}")
    app.run(host="0.0.0.0", port=5000, debug=debug)