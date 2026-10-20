"""
presence.py — Discord Rich Presence.

Идея: плеер не должен ждать Discord. WindowApi.update_presence() только
кладёт снимок состояния в очередь и сразу возвращается, а отдельный поток
сам подключается к клиенту Discord, шлёт SET_ACTIVITY и переподключается,
если Discord перезапустился.

Настройки лежат в %APPDATA%\\Music\\settings.json:
    "discord_presence": true,
    "discord_client_id": "123456789012345678",
    "discord_large_image": ""      # ключ ассета из Developer Portal (необязательно)

Client ID можно также задать переменной окружения MUSIC_DISCORD_CLIENT_ID.
"""

import logging
import queue
import threading
import time
from collections import deque

log = logging.getLogger("presence")

try:
    from pypresence import ActivityType, Presence
    _IMPORT_ERROR = ""
except Exception as exc:  # ImportError и возможные проблемы совместимости
    ActivityType = None
    Presence = None
    _IMPORT_ERROR = str(exc) or exc.__class__.__name__

RATE_WINDOW = 20.0        # Discord: примерно 5 обновлений за 20 секунд
RATE_MAX = 5
HEARTBEAT = 60.0          # раз в минуту проверяем, жив ли IPC-канал
RECONNECT_MIN = 5.0
RECONNECT_MAX = 60.0
MAX_TEXT = 128
CONNECT_TIMEOUT = 3.0
RESPONSE_TIMEOUT = 3.0
IDLE_TICK = 0.5


def _clip(value, limit: int = MAX_TEXT) -> str:
    return " ".join(str(value or "").split())[:limit]


def _to_int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class DiscordPresence:
    def __init__(self, client_id: str = "", enabled: bool = True,
                 large_image: str = "", large_text: str = ""):
        self._lock = threading.RLock()
        self._queue = queue.Queue()
        self._thread = None

        self._client_id = str(client_id or "").strip()
        self._enabled = bool(enabled)
        self._large_image = str(large_image or "").strip()
        self._large_text = str(large_text or "").strip()

        self._rpc = None
        self._connected = False
        self._error = _IMPORT_ERROR
        self._backoff = RECONNECT_MIN
        self._next_connect = 0.0

        self._sent_at = deque()
        self._last_key = None
        self._desired = None
        self._dirty = False

    # ============================================================
    # ПУБЛИЧНОЕ API (вызывается из любого потока)
    # ============================================================
    def start(self) -> None:
        if Presence is None:
            return
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._run, name="discord-presence", daemon=True)
            self._thread.start()

    def update(self, info) -> None:
        """Снимок: title, artist, album, duration, position, playing."""
        if isinstance(info, dict):
            self._queue.put(("update", dict(info)))
            self.start()

    def clear(self) -> None:
        self._queue.put(("clear", None))
        self.start()

    def configure(self, client_id=None, enabled=None, large_image=None) -> None:
        data = {}
        if client_id is not None:
            data["client_id"] = str(client_id).strip()
        if enabled is not None:
            data["enabled"] = bool(enabled)
        if large_image is not None:
            data["large_image"] = str(large_image).strip()
        self._queue.put(("config", data))
        self.start()

    def shutdown(self, clear: bool = True) -> None:
        thread = self._thread
        if thread is None:
            self._disconnect(clear=clear)
            return
        self._queue.put(("stop", bool(clear)))
        thread.join(timeout=2.0)
        with self._lock:
            if self._thread is thread and not thread.is_alive():
                self._thread = None

    def status(self) -> dict:
        with self._lock:
            return {
                "available": Presence is not None,
                "enabled": self._enabled,
                "client_id": self._client_id,
                "connected": self._connected,
                "error": self._error,
            }

    # ============================================================
    # РАБОЧИЙ ПОТОК
    # ============================================================
    def _run(self) -> None:
        while True:
            try:
                cmd, data = self._queue.get(timeout=IDLE_TICK)
            except queue.Empty:
                cmd, data = None, None

            if cmd == "stop":
                self._disconnect(clear=bool(data))
                return
            if cmd == "config":
                self._apply_config(data or {})
            elif cmd == "update":
                self._desired = self._build(data or {})
                self._dirty = True
            elif cmd == "clear":
                self._desired = None
                self._dirty = True

            try:
                self._pump()
            except Exception:
                log.exception("Discord presence")

    def _pump(self) -> None:
        if Presence is None:
            return

        with self._lock:
            enabled = self._enabled
            has_id = bool(self._client_id)

        if not enabled or not has_id:
            if self._connected:
                self._disconnect(clear=True)
            return

        if not self._connected:
            if time.monotonic() < self._next_connect:
                return
            self._connect()
            if not self._connected:
                return

        if not self._dirty:
            if (self._desired is not None and self._sent_at
                    and time.monotonic() - self._sent_at[-1] >= HEARTBEAT):
                self._dirty = True
                self._last_key = None      # heartbeat шлём даже без изменений
            else:
                return

        if self._desired is None:
            self._clear_remote()
            return

        key = self._key(self._desired)
        if key == self._last_key:
            self._dirty = False
            return
        self._send(self._desired, key)

    # ------------------------------------------------------------
    # Подключение
    # ------------------------------------------------------------
    def _connect(self) -> None:
        try:
            rpc = Presence(
                self._client_id,
                connection_timeout=CONNECT_TIMEOUT,
                response_timeout=RESPONSE_TIMEOUT,
            )
            rpc.connect()
        except Exception as exc:
            self._fail(exc)
            return

        with self._lock:
            self._rpc = rpc
            self._connected = True
            self._error = ""
            self._backoff = RECONNECT_MIN
            self._last_key = None
        log.info("Discord: подключено")

    def _disconnect(self, clear: bool = False) -> None:
        with self._lock:
            rpc = self._rpc
            self._rpc = None
            self._connected = False

        if rpc is None:
            return
        if clear:
            try:
                rpc.clear()
            except Exception:
                pass
            self._last_key = None
        try:
            rpc.close()
        except Exception:
            pass

    def _fail(self, exc) -> None:
        with self._lock:
            rpc = self._rpc
            self._rpc = None
            self._connected = False
            self._error = str(exc) or exc.__class__.__name__
            delay = self._backoff
            self._backoff = min(self._backoff * 2, RECONNECT_MAX)
            self._next_connect = time.monotonic() + delay

        if rpc is not None:
            try:
                rpc.close()
            except Exception:
                pass

        log.debug("Discord: %s (повтор через %.0f с)", self._error, delay)

    # ------------------------------------------------------------
    # Отправка
    # ------------------------------------------------------------
    def _send(self, activity: dict, key) -> None:
        now = time.monotonic()
        while self._sent_at and now - self._sent_at[0] >= RATE_WINDOW:
            self._sent_at.popleft()
        if len(self._sent_at) >= RATE_MAX:
            return                      # попробуем на следующем тике

        # Если активность ждала переподключения/лимита, сдвигаем таймер
        # на прошедшее время, чтобы прогресс в Discord не отставал.
        start = activity["start"]
        end = activity["end"]
        if start is not None:
            shift = int(time.monotonic() - activity["built_at"])
            start += shift
            if end is not None:
                end += shift

        kwargs = {
            "details": activity["details"],
            "state": activity["state"],
            "start": start,
            "end": end,
            "large_image": activity["large_image"],
            "large_text": activity["large_text"],
        }
        if ActivityType is not None:
            kwargs["activity_type"] = ActivityType.LISTENING

        try:
            self._rpc.update(**kwargs)
        except Exception as exc:
            self._fail(exc)
            return

        self._sent_at.append(now)
        self._last_key = key
        self._dirty = False
        with self._lock:
            self._error = ""

    def _clear_remote(self) -> None:
        rpc = self._rpc
        if rpc is None:
            self._dirty = False
            return
        try:
            rpc.clear()
        except Exception as exc:
            self._fail(exc)
            return
        self._last_key = None
        self._dirty = False

    # ------------------------------------------------------------
    # Состояние -> payload
    # ------------------------------------------------------------
    def _build(self, info: dict) -> dict:
        title = _clip(info.get("title")) or "Неизвестный трек"
        artist = _clip(info.get("artist")) or "Неизвестный исполнитель"
        album = _clip(info.get("album"))

        duration = max(0, _to_int(info.get("duration")))
        position = max(0.0, _to_float(info.get("position")))
        if duration:
            position = min(position, float(duration))

        playing = bool(info.get("playing"))
        state = f"{artist} — {album}" if album else artist
        if not playing:
            state = "⏸ " + state

        start = end = None
        if playing:
            start = int(time.time() - position)
            if duration > 0:
                end = start + duration

        return {
            "details": _clip(title),
            "state": _clip(state),
            "start": start,
            "end": end,
            "large_image": self._large_image or None,
            "large_text": self._large_text or None,
            "playing": playing,
            "position": position,
            "built_at": time.monotonic(),
        }

    @staticmethod
    def _key(activity: dict):
        # Позиция в 5-секундных корзинах: дубли событий не шлём,
        # а seek всё равно меняет ключ.
        return (activity["details"], activity["state"],
                activity["playing"], int(activity["position"]) // 5,
                activity["large_image"], activity["large_text"])

    def _apply_config(self, data: dict) -> None:
        old_id = self._client_id
        old_enabled = self._enabled
        with self._lock:
            if "client_id" in data:
                self._client_id = data["client_id"]
            if "enabled" in data:
                self._enabled = bool(data["enabled"])
            if "large_image" in data:
                self._large_image = data["large_image"]
            new_id = self._client_id
            enabled = self._enabled

        if not enabled:
            self._disconnect(clear=True)
        elif new_id != old_id:
            self._disconnect(clear=True)
            self._next_connect = 0.0
            self._backoff = RECONNECT_MIN
        elif not old_enabled:
            self._next_connect = 0.0
            self._backoff = RECONNECT_MIN

        if self._desired is not None:
            self._desired["large_image"] = self._large_image or None
            self._desired["large_text"] = self._large_text or None

        self._dirty = True             # переслать текущее состояние
