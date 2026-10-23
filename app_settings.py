"""
app_settings.py — настройки приложения и папки библиотеки.

Модуль намеренно не зависит от WinAPI, чтобы его можно было использовать
и в desktop.py, и в обычном Flask-режиме.
"""

import json
import os

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MUSIC_DIR = os.path.join(APP_DIR, "music")

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


def save_settings(settings: dict) -> None:
    try:
        tmp = _settings_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
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


def load_music_dirs() -> list:
    """Список корневых папок библиотеки.

    Если настройка ещё ни разу не сохранялась — используется встроенная music/.
    Пустой сохранённый список — это осознанный выбор пользователя (пустая
    библиотека), поэтому его не подменяем значением по умолчанию.
    """
    settings = load_settings()

    if "music_dirs" in settings:
        return _clean_paths(settings.get("music_dirs"))

    # Обратная совместимость на случай, если когда-то уже был один music_dir.
    legacy = settings.get("music_dir")
    if isinstance(legacy, str) and legacy.strip():
        return _clean_paths([legacy])

    return [DEFAULT_MUSIC_DIR]


def save_music_dirs(paths) -> None:
    settings = load_settings()
    settings["music_dirs"] = _clean_paths(paths)
    settings.pop("music_dir", None)
    save_settings(settings)
