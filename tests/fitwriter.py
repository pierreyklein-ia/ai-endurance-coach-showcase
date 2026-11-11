"""Mini-encodeur FIT pour les tests (file_id, record, session) — pas de dépendance."""
from __future__ import annotations

import struct
from datetime import UTC, datetime

FIT_EPOCH = datetime(1989, 12, 31, tzinfo=UTC)
_CRC = [0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
        0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400]


def crc16(data: bytes, crc: int = 0) -> int:
    for byte in data:
        tmp = _CRC[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ _CRC[byte & 0xF]
        tmp = _CRC[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ _CRC[(byte >> 4) & 0xF]
    return crc


# (numéro de champ, format struct, type de base FIT)
FILE_ID = (0, [(0, "B", 0x00), (1, "H", 0x84), (4, "I", 0x86)])
RECORD = (20, [(253, "I", 0x86), (3, "B", 0x02), (5, "I", 0x86), (78, "I", 0x86)])
SESSION = (18, [(253, "I", 0x86), (2, "I", 0x86), (5, "B", 0x00), (6, "B", 0x00), (7, "I", 0x86),
                (8, "I", 0x86), (9, "I", 0x86), (16, "B", 0x02), (22, "H", 0x84)])


def _definition(local: int, msg) -> bytes:
    gnum, fields = msg
    out = struct.pack("<BBBHB", 0x40 | local, 0, 0, gnum, len(fields))
    for num, fmt, base in fields:
        out += struct.pack("<BBB", num, struct.calcsize(fmt), base)
    return out


def _data(local: int, msg, values) -> bytes:
    return bytes([local]) + b"".join(struct.pack("<" + f[1], v) for f, v in zip(msg[1], values))


def ts(dt: datetime) -> int:
    return int((dt - FIT_EPOCH).total_seconds())


def write_fit(path, start: datetime, seconds: int, hr_fn, speed_ms: float = 2.5,
              climb_m_per_s: float = 0.1, sport: int = 1, sub_sport: int = 0) -> None:
    """Sortie synthétique à 1 Hz ; sport 1 = course, sub_sport 3 ≈ trail."""
    body = _definition(0, FILE_ID) + _data(0, FILE_ID, (4, 255, ts(start)))
    body += _definition(1, RECORD)
    for i in range(seconds):
        alt = 100 + climb_m_per_s * i
        body += _data(1, RECORD, (ts(start) + i, int(hr_fn(i)), int(speed_ms * i * 100), int((alt + 500) * 5)))
    body += _definition(2, SESSION)
    body += _data(2, SESSION, (ts(start) + seconds, ts(start), sport, sub_sport, seconds * 1000,
                               seconds * 1000, int(speed_ms * seconds * 100),
                               int(sum(hr_fn(i) for i in range(seconds)) / seconds),
                               int(climb_m_per_s * seconds)))
    header = struct.pack("<BBHI4s", 14, 0x20, 2132, len(body), b".FIT")
    header += struct.pack("<H", crc16(header))
    blob = header + body
    with open(path, "wb") as f:
        f.write(blob + struct.pack("<H", crc16(blob)))
