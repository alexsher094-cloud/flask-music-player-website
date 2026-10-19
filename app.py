# app.py
import os
import hashlib
from flask import Flask, jsonify, render_template, send_file, abort, Response
from mutagen import File as MutagenFile
from mutagen.id3 import ID3
from mutagen.flac import FLAC
from mutagen.mp4 import MP4

app = Flask(__name__)

MUSIC_DIR = os.path.join(os.path.dirname(__file__), "music")
ALLOWED_EXT = {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav"}

# Файлы, открытые «извне» (двойной клик, «Открыть с помощью», контекстное меню):
# id -> абсолютный путь. Заполняется из desktop.py, отдаётся теми же роутами.
EXTERNAL_TRACKS = {}


# ============================================================
# ХЕЛПЕРЫ
# ============================================================
def track_id_for(path: str) -> str:
    return hashlib.md5(path.encode("utf-8")).hexdigest()[:16]


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


def scan_music() -> list:
    tracks = []
    if not os.path.isdir(MUSIC_DIR):
        return tracks

    for root, _, files in os.walk(MUSIC_DIR):
        for name in sorted(files):
            ext = os.path.splitext(name)[1].lower()
            if ext not in ALLOWED_EXT:
                continue

            path = os.path.join(root, name)
            rel_path = os.path.relpath(path, MUSIC_DIR)

            meta = extract_metadata(path)
            covers = extract_cover(path)

            tracks.append({
                "id": track_id_for(rel_path),
                "file": rel_path,
                "title": meta["title"],
                "artist": meta["artist"],
                "album": meta["album"],
                "duration": meta["duration"],
                "date": meta["date"],
                "mtime": meta["mtime"],
                "hasCover": covers[0] is not None,
            })

    return tracks


def find_track_by_id(track_id: str):
    ext_path = EXTERNAL_TRACKS.get(track_id)
    if ext_path and os.path.isfile(ext_path):
        return ext_path

    for root, _, files in os.walk(MUSIC_DIR):
        for name in files:
            ext = os.path.splitext(name)[1].lower()
            if ext not in ALLOWED_EXT:
                continue
            path = os.path.join(root, name)
            rel_path = os.path.relpath(path, MUSIC_DIR)
            if track_id_for(rel_path) == track_id:
                return path
    return None


# ============================================================
# ROUTES
# ============================================================
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/tracks")
def api_tracks():
    return jsonify({"tracks": scan_music()})


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
    path = find_track_by_id(track_id)
    if not path:
        abort(404)

    data, mime = extract_cover(path)
    if not data:
        abort(404)

    return Response(data, mimetype=mime or "image/jpeg")


if __name__ == "__main__":
    if not os.path.isdir(MUSIC_DIR):
        os.makedirs(MUSIC_DIR)
    print(f" Музыка: {MUSIC_DIR}")
    print(f" Найдено треков: {len(scan_music())}")
    app.run(host="0.0.0.0", port=5000, debug=True)