"""
tray.py — иконка в области уведомлений (pystray + Pillow).
Левый клик — открыть окно. Меню: управление воспроизведением,
настройки «закрывать в трей» / автозапуск, ассоциации, выход.

Важно:
    • start() и stop() идемпотентны — повторный вызов ничего не делает.
    • stop() дожидается, пока поток pystray обработает WM_STOP и вызовет
      NIM_DELETE. Иначе процесс завершается раньше, чем Windows убирает
      значок, и в трее остаётся «призрак» до наведения мышью.
"""

import os
import threading

from PIL import Image, ImageDraw

import file_assoc
import instance


def make_icon(size: int = 64) -> Image.Image:
    """Иконка рисуется кодом — отдельный файл не нужен."""
    s = size * 4
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=int(s * 0.22), fill=(168, 85, 247, 255))
    white = (255, 255, 255, 255)
    d.ellipse((s * 0.20, s * 0.56, s * 0.46, s * 0.78), fill=white)
    d.rectangle((s * 0.41, s * 0.24, s * 0.47, s * 0.68), fill=white)
    d.polygon([(s * 0.41, s * 0.24), (s * 0.74, s * 0.33),
               (s * 0.74, s * 0.46), (s * 0.41, s * 0.37)], fill=white)
    return img.resize((size, size), Image.LANCZOS)


class Tray:
    def __init__(self, controller):
        self._c = controller
        self._icon = None
        self._started = False
        self._ready = threading.Event()

    def start(self) -> None:
        if self._started:
            return
        import pystray  # ImportError ловит вызывающий код

        c = self._c
        menu = pystray.Menu(
            pystray.MenuItem("Открыть", lambda i, it: c.show_window(), default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Играть / Пауза", lambda i, it: c.click("btn-play")),
            pystray.MenuItem("Следующий",      lambda i, it: c.click("btn-next")),
            pystray.MenuItem("Предыдущий",     lambda i, it: c.click("btn-prev")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Мини-режим", lambda i, it: c.toggle_mini(),
                             checked=lambda it: c.is_mini()),
            pystray.MenuItem("Закрывать в трей", self._toggle_close_to_tray,
                             checked=lambda it: bool(c.settings.get("close_to_tray", True))),
            pystray.MenuItem("Discord Rich Presence", self._toggle_discord,
                             checked=lambda it: bool(c.settings.get("discord_presence", True))),
            pystray.MenuItem("Запускать вместе с Windows", self._toggle_autostart,
                             checked=lambda it: file_assoc.is_autostart()),
            pystray.MenuItem("Сопоставить с аудиофайлами…", self._register),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Выход", lambda i, it: c.quit()),
        )

        self._ready.clear()
        self._icon = pystray.Icon("MusicPlayer", make_icon(), "Music", menu)

        def setup(icon):
            try:
                icon.visible = True
            finally:
                self._ready.set()

        try:
            self._icon.run_detached(setup)
            # Ждём, пока pystray создаст окно и покажет значок. Это нужно,
            # чтобы stop() не вызвался раньше, чем _running станет True.
            self._ready.wait(timeout=2.0)
        except Exception:
            self._icon = None
            raise
        self._started = True

    def stop(self) -> None:
        if not self._started or self._icon is None:
            self._started = False
            self._icon = None
            return
        icon = self._icon
        try:
            icon.stop()
        except Exception:
            pass
        finally:
            # pystray удаляет значок (NIM_DELETE) в своём потоке уже после
            # WM_STOP. Без join процесс может умереть раньше, и Windows
            # оставит значок-«призрак» до наведения мышью.
            thread = getattr(icon, "_thread", None)
            if (thread is not None and thread.is_alive()
                    and thread is not threading.current_thread()):
                thread.join(timeout=3.0)
            self._icon = None
            self._started = False

    # ---------- пункты меню ----------
    def _toggle_close_to_tray(self, icon, item):
        self._c.settings["close_to_tray"] = not self._c.settings.get("close_to_tray", True)
        instance.save_settings(self._c.settings)
        icon.update_menu()

    def _toggle_autostart(self, icon, item):
        file_assoc.set_autostart(not file_assoc.is_autostart())
        icon.update_menu()

    def _toggle_discord(self, icon, item):
        enabled = not self._c.settings.get("discord_presence", True)
        self._c.set_discord_config(
            enabled, self._c.settings.get("discord_client_id", ""))
        icon.update_menu()

    def _register(self, icon, item):
        file_assoc.register()
        try:
            os.startfile("ms-settings:defaultapps")
        except OSError:
            pass