"""
library.py — кэш библиотеки.

  • SQLite (WAL): метаданные треков. Файл перечитывается только если у него
    изменились (mtime_ns, size). Повторный запуск = один проход по stat().
  • Превью обложек: JPEG нужного размера кэшируются на диске в
    <cover_dir>/<sha1(обложки)>_<size>.jpg. Одинаковые обложки у треков
    одного альбома хранятся один раз.
  • watchdog: новые/изменённые/удалённые файлы подхватываются без перезапуска.
    Если watchdog не установлен — запасной вариант: опрос раз в POLL_INTERVAL.

Модуль не зависит от Flask. Парсеры тегов передаются снаружи (см. app.py).
"""

import hashlib
import io
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Callable, Optional

log = logging.getLogger("library")

SCHEMA_VERSION = 1            # увеличить, если поменялась логика разбора тегов
THUMB_SIZES = (64, 128, 256, 512, 1024)
DEFAULT_THUMB = 512

DEBOUNCE = 1.0                # тишина после последнего события ФС, сек
POLL_INTERVAL = 30.0          # без watchdog
SAFETY_SYNC = 600.0           # с watchdog: полный проход «на всякий случай»
_BATCH = 100                  # строк в одной транзакции при скане
_NOTIFY_EVERY = 2.0           # не чаще, чем раз в N сек дёргать on_change при скане

_UNKNOWN = object()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    id        TEXT PRIMARY KEY,
    rel_path  TEXT NOT NULL UNIQUE,
    mtime_ns  INTEGER NOT NULL,
    size      INTEGER NOT NULL,
    title     TEXT, artist TEXT, album TEXT,
    duration  INTEGER, date TEXT, mtime INTEGER,
    cover     TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def track_id_for(path: str) -> str:
    return hashlib.md5(path.encode("utf-8")).hexdigest()[:16]


def snap_size(size) -> int:
    """Округляет запрошенный размер вверх до ближайшего из THUMB_SIZES
    (чтобы кэш не раздувался от произвольных ?s=...)."""
    try:
        size = int(size)
    except (TypeError, ValueError):
        return DEFAULT_THUMB
    if size <= 0:
        return DEFAULT_THUMB
    for s in THUMB_SIZES:
        if size <= s:
            return s
    return THUMB_SIZES[-1]


def _cover_hash(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()[:16]


def _make_thumb(data: bytes, size: int) -> bytes:
    from PIL import Image

    im = Image.open(io.BytesIO(data))
    im.draft("RGB", (size * 2, size * 2))      # ускоряет декод больших JPEG
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (10, 10, 12))
        bg.paste(im, mask=im.getchannel("A"))
        im = bg
    elif im.mode != "RGB":
        im = im.convert("RGB")
    im.thumbnail((size, size), Image.LANCZOS)  # не увеличивает маленькие
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85, optimize=True)
    return buf.getvalue()


class Library:
    def __init__(self, music_dir: str, db_path: str, cover_dir: str,
                 read_meta: Callable[[str], dict],
                 read_cover: Callable[[str], tuple],
                 allowed_ext):
        self.music_dir = os.path.abspath(music_dir)
        self.db_path = db_path
        self.cover_dir = cover_dir
        self._read_meta = read_meta
        self._read_cover = read_cover
        self.allowed_ext = frozenset(allowed_ext)

        self.on_change: Optional[Callable[[], None]] = None
        # 1 — начальное состояние. 0 оставляем как «ещё не загружено»,
        # иначе первый ответ /api/tracks можно спутать с уже применённым.
        self.version = 1

        self._db = None
        self._db_lock = threading.RLock()     # доступ к соединению
        self._sync_lock = threading.Lock()    # один скан/обновление за раз
        self._tracks_cache = None

        self._started = False
        self._ready = threading.Event()       # в БД уже есть данные / скан окончен
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._plock = threading.Lock()
        self._pending = set()
        self._full = False
        self._last_event = 0.0
        self._last_notify = 0.0
        self._observer = None
        self._period = SAFETY_SYNC
        self._thumb_locks = [threading.Lock() for _ in range(16)]

        self._open_db()

    # ============================================================
    # БД
    # ============================================================
    def _open_db(self) -> None:
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        os.makedirs(self.cover_dir, exist_ok=True)

        db = sqlite3.connect(self.db_path, check_same_thread=False,
                             isolation_level=None, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")

        if db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            db.execute("DROP TABLE IF EXISTS tracks")
            db.execute("DROP TABLE IF EXISTS meta")
        db.executescript(_SCHEMA)
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

        # папка с музыкой сменилась → кэш не наш
        cur = os.path.normcase(self.music_dir)
        row = db.execute("SELECT value FROM meta WHERE key='music_dir'").fetchone()
        if row is None or os.path.normcase(row[0]) != cur:
            db.execute("DELETE FROM tracks")
            db.execute("INSERT OR REPLACE INTO meta VALUES ('music_dir', ?)",
                       (self.music_dir,))
        self._db = db

    @contextmanager
    def _tx(self):
        with self._db_lock:
            self._db.execute("BEGIN")
            try:
                yield self._db
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def _q(self, sql: str, args=()):
        with self._db_lock:
            return self._db.execute(sql, args).fetchall()

    def _count(self) -> int:
        return self._q("SELECT COUNT(*) FROM tracks")[0][0]

    def _bump(self) -> None:
        with self._db_lock:
            self.version += 1
            self._tracks_cache = None

    def _notify(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_notify < _NOTIFY_EVERY:
            return
        self._last_notify = now
        cb = self.on_change
        if cb:
            try:
                cb()
            except Exception:
                log.exception("on_change")

    def _upsert(self, rows) -> None:
        if not rows:
            return
        with self._tx() as db:
            db.executemany(
                "INSERT OR REPLACE INTO tracks "
                "(id, rel_path, mtime_ns, size, title, artist, album, "
                " duration, date, mtime, cover) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                rows)
        self._bump()

    def _delete(self, rels) -> int:
        if not rels:
            return 0
        n = 0
        with self._tx() as db:
            for rel in rels:
                n += db.execute("DELETE FROM tracks WHERE rel_path=?", (rel,)).rowcount
        if n:
            self._bump()
        return n

    # ============================================================
    # ПУБЛИЧНОЕ ЧТЕНИЕ
    # ============================================================
    def tracks(self) -> list:
        """Список треков для /api/tracks (кэшируется в памяти до изменения)."""
        # самый первый запуск: БД пуста — подождать, пока скан даст первую порцию
        if self._started and not self._ready.is_set() and self._count() == 0:
            self._ready.wait(60)
        with self._db_lock:
            if self._tracks_cache is None:
                rows = self._db.execute(
                    "SELECT id, rel_path, title, artist, album, duration, date, "
                    "mtime, cover FROM tracks ORDER BY rel_path COLLATE NOCASE"
                ).fetchall()
                self._tracks_cache = [{
                    "id": r["id"], "file": r["rel_path"],
                    "title": r["title"], "artist": r["artist"], "album": r["album"],
                    "duration": r["duration"], "date": r["date"], "mtime": r["mtime"],
                    "hasCover": r["cover"] is not None,
                    "cover": r["cover"],           # хэш обложки (для ?v=)
                } for r in rows]
            return self._tracks_cache

    def get(self, track_id: str) -> Optional[dict]:
        rows = self._q("SELECT rel_path, cover FROM tracks WHERE id=?", (track_id,))
        if not rows:
            return None
        return {"path": os.path.join(self.music_dir, rows[0]["rel_path"]),
                "cover": rows[0]["cover"]}

    def path_for_id(self, track_id: str) -> Optional[str]:
        info = self.get(track_id)
        if info and os.path.isfile(info["path"]):
            return info["path"]
        return None

    # ============================================================
    # ОБЛОЖКИ
    # ============================================================
    def thumb_path(self, path: str, size=None, cover_hash=_UNKNOWN) -> Optional[str]:
        """
        Путь к JPEG-превью на диске (создаётся при первом обращении).
        cover_hash: хэш из БД; None — обложки нет; не передан — вычислим сами
        (для файлов, открытых «извне» и отсутствующих в БД).
        """
        if cover_hash is None:
            return None
        size = snap_size(size)

        data = None
        if cover_hash is _UNKNOWN:
            data, _ = self._read_cover(path)
            if not data:
                return None
            cover_hash = _cover_hash(data)

        out = os.path.join(self.cover_dir, f"{cover_hash}_{size}.jpg")
        if os.path.isfile(out):
            return out

        with self._thumb_locks[int(cover_hash[:2], 16) % len(self._thumb_locks)]:
            if os.path.isfile(out):
                return out
            if data is None:
                data, _ = self._read_cover(path)
            if not data:
                return None
            try:
                jpeg = _make_thumb(data, size)
            except Exception as e:
                log.warning("thumb %s: %s", path, e)
                return None
            tmp = f"{out}.{threading.get_ident()}.tmp"
            with open(tmp, "wb") as f:
                f.write(jpeg)
            os.replace(tmp, out)
        return out

    def _prune_covers(self) -> None:
        """Удаляет превью обложек, которых больше нет в библиотеке."""
        keep = {r[0] for r in self._q(
            "SELECT DISTINCT cover FROM tracks WHERE cover IS NOT NULL")}
        try:
            names = os.listdir(self.cover_dir)
        except OSError:
            return
        for name in names:
            if name.endswith(".jpg") and name.split("_", 1)[0] not in keep:
                try:
                    os.remove(os.path.join(self.cover_dir, name))
                except OSError:
                    pass

    # ============================================================
    # СКАН
    # ============================================================
    def _walk(self):
        stack = [self.music_dir]
        while stack:
            d = stack.pop()
            try:
                it = os.scandir(d)
            except OSError:
                continue
            with it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif (os.path.splitext(e.name)[1].lower() in self.allowed_ext
                              and e.is_file()):
                            yield os.path.relpath(e.path, self.music_dir), e.path, e.stat()
                    except OSError:
                        continue

    def _read_row(self, rel: str, full: str, st) -> Optional[tuple]:
        try:
            meta = self._read_meta(full)
            data, _ = self._read_cover(full)
        except Exception as e:
            log.warning("read %s: %s", full, e)
            return None
        return (track_id_for(rel), rel, st.st_mtime_ns, st.st_size,
                meta["title"], meta["artist"], meta["album"],
                int(meta.get("duration") or 0), meta.get("date") or "",
                int(meta.get("mtime") or 0),
                _cover_hash(data) if data else None)

    def sync(self) -> bool:
        """Инкрементальный проход: парсим только новые/изменённые, чистим удалённые."""
        with self._sync_lock:
            if not os.path.isdir(self.music_dir):
                return False   # диск отключён — не стираем кэш

            known = {r["rel_path"]: (r["mtime_ns"], r["size"])
                     for r in self._q("SELECT rel_path, mtime_ns, size FROM tracks")}
            seen, todo = set(), []
            for rel, full, st in self._walk():
                seen.add(rel)
                if known.get(rel) != (st.st_mtime_ns, st.st_size):
                    todo.append((rel, full, st))

            changed = False
            batch = []
            for rel, full, st in todo:
                if self._stop.is_set():
                    return changed
                row = self._read_row(rel, full, st)
                if row:
                    batch.append(row)
                if len(batch) >= _BATCH:
                    self._upsert(batch)
                    batch = []
                    changed = True
                    self._ready.set()
                    self._notify()          # UI наполняется по мере скана
            if batch:
                self._upsert(batch)
                changed = True

            removed = [rel for rel in known if rel not in seen]
            if self._delete(removed):
                changed = True

            self._ready.set()
            if changed:
                self._notify(force=True)
            log.info("sync: +%d изменено, -%d удалено, всего %d",
                     len(todo), len(removed), len(seen))
            return changed

    # ============================================================
    # WATCHER
    # ============================================================
    def start(self) -> None:
        if self._started:
            return
        self._started = True
        os.makedirs(self.music_dir, exist_ok=True)
        self._start_observer()
        threading.Thread(target=self._boot, daemon=True, name="library-boot").start()
        threading.Thread(target=self._watch_loop, daemon=True, name="library-watch").start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        obs, self._observer = self._observer, None
        if obs:
            try:
                obs.stop()
                obs.join(timeout=2)
            except Exception:
                pass
        # дождаться текущего скана/обновления (они проверяют _stop и быстро выходят)
        got = self._sync_lock.acquire(timeout=5)
        try:
            with self._db_lock:
                try:
                    self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    self._db.close()
                except Exception:
                    pass
        finally:
            if got:
                self._sync_lock.release()

    def request_rescan(self) -> None:
        self._queue(full=True)

    def _boot(self) -> None:
        try:
            self.sync()
            self._prune_covers()
        except Exception:
            log.exception("initial sync")
        finally:
            self._ready.set()

    def _start_observer(self) -> None:
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            log.warning("watchdog не установлен (pip install watchdog) — "
                        "опрос папки раз в %.0f с", POLL_INTERVAL)
            self._period = POLL_INTERVAL
            return

        lib = self

        class Handler(FileSystemEventHandler):
            def on_any_event(self, ev):
                if ev.event_type not in ("created", "modified", "moved", "deleted"):
                    return
                if ev.is_directory:
                    # «modified» у папки сыплется на любое изменение внутри — шум
                    if ev.event_type != "modified":
                        lib._queue(full=True)
                    return
                paths = [os.fsdecode(ev.src_path)]
                dest = getattr(ev, "dest_path", "")
                if dest:
                    paths.append(os.fsdecode(dest))
                lib._queue(paths=[p for p in paths
                                  if os.path.splitext(p)[1].lower() in lib.allowed_ext])

        try:
            obs = Observer()
            obs.schedule(Handler(), self.music_dir, recursive=True)
            obs.daemon = True
            obs.start()
            self._observer = obs
            self._period = SAFETY_SYNC
        except Exception:
            log.exception("watchdog не стартовал — опрос раз в %.0f с", POLL_INTERVAL)
            self._period = POLL_INTERVAL

    def _queue(self, paths=(), full: bool = False) -> None:
        if not paths and not full:
            return
        with self._plock:
            self._pending.update(paths)
            self._full = self._full or full
            self._last_event = time.monotonic()
            self._wake.set()

    def _watch_loop(self) -> None:
        while not self._stop.is_set():
            fired = self._wake.wait(timeout=self._period)
            if self._stop.is_set():
                return
            if fired:
                # ждём, пока копирование/распаковка «утихнет»
                while (not self._stop.is_set()
                       and time.monotonic() - self._last_event < DEBOUNCE):
                    time.sleep(0.2)
                if self._stop.is_set():
                    return
                with self._plock:
                    paths, self._pending = self._pending, set()
                    full, self._full = self._full, False
                    self._wake.clear()
            else:
                paths, full = set(), True      # таймер: страховочный/поллинг-проход

            try:
                if full:
                    self.sync()
                elif paths and self._refresh_paths(paths):
                    self._notify(force=True)
            except Exception:
                log.exception("watch loop")

    def _wait_stable(self, path: str, tries: int = 30) -> bool:
        """Файл дописан и не занят (копирование большого файла ещё идёт)."""
        prev = None
        for _ in range(tries):
            try:
                st = os.stat(path)
                cur = (st.st_size, st.st_mtime_ns)
                if cur == prev:
                    with open(path, "rb"):
                        return True
                prev = cur
            except OSError:
                prev = None
            if self._stop.wait(0.5):
                return False
        return False

    def _refresh_paths(self, paths) -> bool:
        changed = False
        with self._sync_lock:
            for p in paths:
                p = os.path.abspath(p)
                try:
                    rel = os.path.relpath(p, self.music_dir)
                except ValueError:
                    continue
                if rel.startswith(".."):
                    continue

                if os.path.isfile(p):
                    if not self._wait_stable(p):
                        continue
                    st = os.stat(p)
                    old = self._q("SELECT mtime_ns, size FROM tracks WHERE rel_path=?", (rel,))
                    if old and (old[0][0], old[0][1]) == (st.st_mtime_ns, st.st_size):
                        continue
                    row = self._read_row(rel, p, st)
                    if row:
                        self._upsert([row])
                        changed = True
                elif self._delete([rel]):
                    changed = True
        return changed
