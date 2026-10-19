"""
tray.py — иконка в области уведомлений (pystray + Pillow).
Левый клик — открыть окно. Меню: управление воспроизведением,
настройки «закрывать в трей» / автозапуск, ассоциации, выход.
"""

import os

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
    d.ellipse((s * 0.20, s * 0.56, s * 0.46, s * 0.78), fill=white)       # головка ноты
    d.rectangle((s * 0.41, s * 0.24, s * 0.47, s * 0.68), fill=white)     # штиль
    d.polygon([(s * 0.41, s * 0.24), (s * 0.74, s * 0.33),
               (s * 0.74, s * 0.46), (s * 0.41, s * 0.37)], fill=white)   # флажок
    return img.resize((size, size), Image.LANCZOS)


class Tray:
    def __init__(self, controller):
        self._c = controller
        self._icon = None

    def start(self) -> None:
        import pystray  # ImportError ловит вызывающий код

        c = self._c
        menu = pystray.Menu(
            pystray.MenuItem("Открыть", lambda i, it: c.show_window(), default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Играть / Пауза", lambda i, it: c.click("btn-play")),
            pystray.MenuItem("Следующий", lambda i, it: c.click("btn-next")),
            pystray.MenuItem("Предыдущий", lambda i, it: c.click("btn-prev")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Закрывать в трей", self._toggle_close_to_tray,
                             checked=lambda it: bool(c.settings.get("close_to_tray", True))),
            pystray.MenuItem("Запускать вместе с Windows", self._toggle_autostart,
                             checked=lambda it: file_assoc.is_autostart()),
            pystray.MenuItem("Сопоставить с аудиофайлами…", self._register),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Выход", lambda i, it: c.quit()),
        )
        self._icon = pystray.Icon("MusicPlayer", make_icon(), "Music", menu)
        self._icon.run_detached()

    def stop(self) -> None:
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass

    # ---------- пункты меню ----------
    def _toggle_close_to_tray(self, icon, item):
        
        self._c.settings["close_to_tray"] = not self._c.settings.get("close_to_tray", True)
        instance.save_settings(self._c.settings)
        icon.update_menu()

    def _toggle_autostart(self, icon, item):
        file_assoc.set_autostart(not file_assoc.is_autostart())
        icon.update_menu()

    def _register(self, icon, item):
        file_assoc.register()
        try:
            os.startfile("ms-settings:defaultapps")   # выбрать Music по умолчанию
        except OSError:
            pass