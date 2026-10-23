"""Разбор имён музыкальных файлов, когда теги пустые или неполные.

Основные поддерживаемые раскладки:

    Artist - Title
    Artist - Album — Title
    Artist - Album - Title
    01 - Artist - Title
    Artist - 01 - Title
    Artist - Title - Remix

Разбор намеренно осторожный: если в имени нет явных разделителей, оно
считается названием трека, а не попыткой угадать исполнителя.
"""

import re
import unicodedata

UNKNOWN_ARTIST = "Неизвестный исполнитель"

# Дефисы, которые в именах файлов используются как разделители.
_DASH_CHARS = (
    "\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2015", "\u2212",
)

_TRACK_PREFIX_DOT = re.compile(r"^\s*\[?\d{1,3}\]?\s*[.)]\s*")
_TRACK_PREFIX_DASH = re.compile(r"^\s*\[?\d{1,3}\]?\s*[-\u2013\u2014]\s*")
_TRACK_PREFIX_SPACE = re.compile(
    r"^\s*\[?\d{1,3}\]?\s+(?!\d+\s)(?=\S.*\s+[-\u2013\u2014]\s+)"
)
_TRACK_NUMBER = re.compile(r"^\d{1,3}$")

_VERSION_WORDS = {
    "remix", "rmx", "live", "instrumental", "inst", "extended",
    "extended mix", "radio edit", "acoustic", "demo", "cover",
    "karaoke", "remaster", "remastered", "sped up", "slowed",
    "nightcore", "vip", "bootleg", "flip", "reprise", "unplugged",
}

_VERSION_START = re.compile(
    r"^(?:english|official|lyrics?|lyric video|audio|video|opening|"
    r"ending|full|short|radio|extended|sped up|slowed|nightcore|"
    r"remix|live|instrumental|acoustic|demo|cover|karaoke|"
    r"remaster(?:ed)?|vip|bootleg|flip|reprise|unplugged)\b",
    re.IGNORECASE,
)


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text or ""))
    for ch in _DASH_CHARS:
        if ch in ("\u2014", "\u2015"):
            text = text.replace(ch, "\u2014")
        elif ch != "\u2013":
            text = text.replace(ch, "-")
    # Отдельно стоящее подчёркивание — это скорее разделитель артистов.
    text = re.sub(r"\s+_\s+", ", ", text)

    # Остальные подчёркивания обычно заменяют пробелы, но не внутри
    # скобок (там это часть ссылок/ников) и не между цифрами (25_17).
    out, depth = [], 0
    for i, ch in enumerate(text):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)

        if ch == "_" and depth == 0:
            between_digits = (
                0 < i < len(text) - 1
                and text[i - 1].isdigit() and text[i + 1].isdigit()
            )
            out.append(ch if between_digits else " ")
        else:
            out.append(ch)

    return re.sub(r"\s+", " ", "".join(out)).strip()


_ARTIST_SPLIT = re.compile(
    r"\s*(?:,|&|;)\s*"
    r"|\s+(?:feat\.?|ft\.?|featuring)\s+"
    r"|\s+[x\u00d7]\s+"
    r"|\s+vs\.?\s+"
    r"|\s+\+\s+",
    re.IGNORECASE,
)


def strip_brackets(text: str) -> str:
    """Убирает содержимое ``(...)`` и ``[...]`` вместе со скобками."""
    text = str(text or "")
    out = []
    round_depth = 0
    square_depth = 0

    for ch in text:
        if ch == "(":
            round_depth += 1
        elif ch == ")":
            round_depth = max(0, round_depth - 1)
        elif ch == "[":
            square_depth += 1
        elif ch == "]":
            square_depth = max(0, square_depth - 1)
        elif round_depth == 0 and square_depth == 0:
            out.append(ch)

    return re.sub(r"\s+", " ", "".join(out)).strip()


def normalize_album(value: str) -> str:
    """Чистит название альбома: убирает скобки и лишние разделители."""
    return strip_brackets(value).strip(" -\u2013\u2014,;")


def normalize_artist(value: str) -> str:
    """Приводит список исполнителей к виду ``Артист1, Артист2``.

    Поддерживаются ``&``, запятая, ``;``, ``feat.``/``ft.``, ``x``/``×``,
    ``vs.`` и ``+``. Содержимое ``(...)`` и ``[...]`` удаляется.
    """
    text = strip_brackets(value)
    if not text:
        return ""

    names = []
    seen = set()
    for part in _ARTIST_SPLIT.split(text):
        # ``+`` в конце — часть сценического имени (например, Kamiyada+),
        # поэтому срезаем только разделители, но не плюс.
        name = part.strip(" -\u2013\u2014,;&")
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)

    return ", ".join(names)


def _has_hyphen_outside_brackets(text: str) -> bool:
    depth = 0
    for i, ch in enumerate(text):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif (depth == 0 and ch == "-"
              and 0 < i < len(text) - 1
              and text[i - 1].isspace() and text[i + 1].isspace()):
            return True
    return False


def _split_parts(text: str) -> list:
    """Делит имя по разделителям, не заходя внутрь скобок.

    Обычный дефис — основной разделитель. En dash (``–``) считаем
    разделителем, только если дефиса в имени нет: иначе он часто является
    частью названия (``DEAD BLONDE - Питер – город криминала``). Em dash
    (``—``) считаем сильным разделителем в обеих ситуациях.
    """
    has_hyphen = _has_hyphen_outside_brackets(text)
    parts, buf, depth = [], [], 0

    for i, ch in enumerate(text):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)

        split = False
        if depth == 0:
            if (ch == "-" and 0 < i < len(text) - 1
                    and text[i - 1].isspace() and text[i + 1].isspace()):
                split = True
            elif ch == "\u2014":
                split = True
            elif ch == "\u2013" and not has_hyphen:
                split = True

        if split:
            part = "".join(buf).strip()
            if part:
                parts.append(part)
            buf = []
        else:
            buf.append(ch)

    part = "".join(buf).strip()
    if part:
        parts.append(part)
    return parts


def _strip_track_prefix(text: str) -> str:
    text = _TRACK_PREFIX_DOT.sub("", text, count=1)
    text = _TRACK_PREFIX_DASH.sub("", text, count=1)
    text = _TRACK_PREFIX_SPACE.sub("", text, count=1)
    return text.strip()


def _is_track_number(part: str) -> bool:
    value = part.strip().strip("[]()").strip(" ._-")
    return bool(_TRACK_NUMBER.fullmatch(value))


def _is_version(part: str) -> bool:
    value = part.casefold().strip(" ._-")
    if value in _VERSION_WORDS:
        return True
    if _VERSION_START.match(value):
        return True
    return bool(re.match(r"^(feat\.?|ft\.?)\s+\S", value))


def _result(artist=None, album=None, title=None) -> dict:
    return {
        "artist": artist or None,
        "album": album or None,
        "title": title or None,
    }


def parse_filename(stem: str) -> dict:
    """Возвращает ``{"artist", "album", "title"}``; неизвестные поля — None.

    Строка передаётся без расширения. Функция не обращается к файлам и не
    использует эвристики вроде «папка похожа на альбом» — только имя.
    """
    text = _strip_track_prefix(_normalize(stem))
    if not text:
        return _result()

    parts = [
        part.strip(" -\u2013\u2014")
        for part in _split_parts(text)
        if part.strip(" -\u2013\u2014")
    ]
    if not parts:
        return _result()

    # Номер трека может стоять между исполнителем и названием/альбомом.
    if len(parts) >= 3:
        parts = [parts[0]] + [
            part for part in parts[1:-1] if not _is_track_number(part)
        ] + [parts[-1]]
        while len(parts) > 2 and _is_track_number(parts[-1]):
            parts.pop()

    if len(parts) == 1:
        return _result(title=_strip_track_prefix(parts[0]) or parts[0])

    if len(parts) == 2:
        return _result(
            artist=parts[0],
            title=_strip_track_prefix(parts[1]) or parts[1],
        )

    # Artist - Title - Remix / Artist - Title - feat. X: последняя часть
    # относится к названию, а не к альбому.
    if _is_version(parts[-1]):
        if len(parts) == 3:
            return _result(
                artist=parts[0],
                title=_strip_track_prefix(" - ".join(parts[1:])),
            )
        return _result(
            artist=parts[0],
            album=parts[1],
            title=_strip_track_prefix(" - ".join(parts[2:])),
        )

    # Oxxxymiron - Oxxxymiron ft. MARKUL - Title: вторая часть — повтор
    # исполнителя с кредитами, а не альбом.
    if (re.search(r"\b(?:feat|ft)\.?\b", parts[1], re.IGNORECASE)
            and parts[1].casefold().startswith(parts[0].casefold())):
        return _result(
            artist=parts[0],
            title=_strip_track_prefix(" - ".join(parts[2:])),
        )

    # Artist - Title feat. X - Remix: кредиты относятся к названию.
    if re.search(r"\b(?:feat|ft)\.?\b", parts[1], re.IGNORECASE):
        return _result(
            artist=parts[0],
            title=_strip_track_prefix(" - ".join(parts[1:])),
        )

    if len(parts) == 3:
        return _result(
            artist=parts[0],
            album=parts[1],
            title=_strip_track_prefix(parts[2]) or parts[2],
        )

    return _result(
        artist=parts[0],
        album=parts[1],
        title=_strip_track_prefix(" - ".join(parts[2:])),
    )
