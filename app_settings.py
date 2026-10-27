"""
app_settings.py — настройки приложения и папки библиотеки.

Модуль намеренно не зависит от WinAPI, чтобы его можно было использовать
и в desktop.py, и в обычном Flask-режиме.
"""

import json
import os
import sys
import threading

APP_DIR = os.path.dirname(os.path.abspath(__file__))

# settings.json обновляют и Flask (папки библиотеки), и окно/трей
# (геометрия, переключатели). Запись должна быть атомарной и не терять
# ключи, которые другой поток успел сохранить.
_save_lock = threading.RLock()
_LIBRARY_KEYS = {"music_dirs", "music_dir"}


def _default_music_dir() -> str:
    """Папка библиотеки по умолчанию.

    В исходниках это music/ рядом с проектом. В собранном приложении
    APP_DIR указывает на временную папку PyInstaller, поэтому берём
    стандартную папку «Музыка» пользователя.
    """
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.expanduser("~"), "Music")
    return os.path.join(APP_DIR, "music")


DEFAULT_MUSIC_DIR = _default_music_dir()

_DEFAULTS = {
    "close_to_tray": True,
    "window": None,
    "discord_presence": True,
    "discord_client_id": "",
    "discord_large_image": "",
}


def data_dir() -> str:
    """Папка данных приложения: %APPDATA%\\Music (или ~/Music на других ОС)."""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "Music")
    os.makedirs(path, exist_ok=True)
    return path


def _settings_path() -> str:
    return os.path.join(data_dir(), "settings.json")


def load_settings() -> dict:
    data = dict(_DEFAULTS)
    try:
        with open(_settings_path(), encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            data.update(loaded)
    except (OSError, ValueError):
        pass
    return data


def save_settings(settings: dict, replace: bool = False) -> None:
    """Сохраняет настройки.

    По умолчанию перечитывает файл и обновляет только переданные ключи:
    у окна и трея есть свой снимок настроек, и при закрытии он не должен
    затирать music_dirs, добавленные из веб-интерфейса. replace=True
    записывает ровно переданный словарь (нужно для save_music_dirs,
    чтобы можно было удалить устаревший music_dir).
    """
    if not isinstance(settings, dict):
        return
    with _save_lock:
        if replace:
            data = dict(settings)
        else:
            data = load_settings()
            # Список папок меняется только через save_music_dirs(). У окна
            # и трея может быть устаревшая копия music_dirs; не даём ей
            # вернуть удалённую папку или потерять только что добавленную.
            data.update({k: v for k, v in settings.items()
                         if k not in _LIBRARY_KEYS})
        try:
            tmp = _settings_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, _settings_path())
        except OSError:
            pass


def _clean_paths(paths) -> list:
    """Убирает пустые/некорректные/повторяющиеся пути, сохраняя порядок."""
    out, seen = [], set()
    if not isinstance(paths, (list, tuple)):
        return out

    for raw in paths:
        if not isinstance(raw, str):
            continue
        value = raw.strip()
        if not value:
            continue
        path = os.path.abspath(os.path.expanduser(value))
        key = os.path.normcase(path)
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def has_saved_music_dirs() -> bool:
    """True, если список папок уже сохранялся (включая старый music_dir)."""
    settings = load_settings()
    if isinstance(settings.get("music_dirs"), list):
        return True
    legacy = settings.get("music_dir")
    return isinstance(legacy, str) and bool(legacy.strip())


def load_music_dirs() -> list:
    """Список корневых папок библиотеки.

    Если настройка ещё ни разу не сохранялась — используется встроенная music/.
    Пустой сохранённый список — это осознанный выбор пользователя (пустая
    библиотека), поэтому его не подменяем значением по умолчанию.
    """
    settings = load_settings()

    if isinstance(settings.get("music_dirs"), list):
        return _clean_paths(settings.get("music_dirs"))

    # Обратная совместимость на случай, если когда-то уже был один music_dir.
    legacy = settings.get("music_dir")
    if isinstance(legacy, str) and legacy.strip():
        return _clean_paths([legacy])

    return [DEFAULT_MUSIC_DIR]


def save_music_dirs(paths) -> None:
    with _save_lock:
        settings = load_settings()
        settings["music_dirs"] = _clean_paths(paths)
        settings.pop("music_dir", None)
        save_settings(settings, replace=True)
