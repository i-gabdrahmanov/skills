"""image.py — размеры растров макета без внешних библиотек: PNG и JPEG (по заголовку).

Нужны, чтобы привязать goal image к кадру (поля под тень вокруг кадра, спека §3.3/§14.6) и чтобы
сказать пользователю, что именно прикреплено. Содержимое картинки движок не декодирует: дифф
считается в браузере (задача 5.1).
"""
from __future__ import annotations

import struct
from pathlib import Path
from typing import Optional

_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def sniff_bytes(data: bytes) -> Optional[dict]:
    """{format: png|jpeg, width, height} по заголовку или None (не PNG/JPEG либо повреждён)."""
    if data[:8] == _PNG_SIG and len(data) >= 24 and data[12:16] == b"IHDR":
        w, h = struct.unpack(">II", data[16:24])
        return {"format": "png", "width": w, "height": h} if w and h else None
    if data[:2] == b"\xff\xd8":
        i = 2
        n = len(data)
        while i + 4 <= n:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7 or marker == 0xFF:
                i += 2 if marker != 0xFF else 1
                continue
            if marker == 0xD9:
                break
            seg = struct.unpack(">H", data[i + 2:i + 4])[0]
            # SOF0..SOF15 кроме DHT(C4), JPG(C8), DAC(CC) — в них высота и ширина
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC) and i + 9 <= n:
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return {"format": "jpeg", "width": w, "height": h} if w and h else None
            i += 2 + seg
    return None


def sniff(path: Path) -> Optional[dict]:
    try:
        with open(path, "rb") as f:
            head = f.read(1 << 20)          # заголовок PNG/JPEG укладывается в начало файла
    except OSError:
        return None
    return sniff_bytes(head)


EXT = {"png": ".png", "jpeg": ".jpg"}
