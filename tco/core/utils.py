"""Общие утилиты: время, деньги, идентификаторы, канонический хеш."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

UTC = timezone.utc

#: Пространство имен для детерминированных UUID (equivalence group, fingerprint).
NAMESPACE_TCO = uuid.UUID("6f1f9d1e-0f9a-5d3e-9a3c-3f1b7c2f8d40")


# --------------------------------------------------------------------------- #
# Время
# --------------------------------------------------------------------------- #


def utcnow() -> datetime:
    """Текущее время в UTC (timezone-aware)."""
    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    """Приводит наивное время к UTC, сохраняя aware-значения."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def floor_to_bucket(value: datetime, hours: int) -> datetime:
    """Округляет момент вниз до сетки в ``hours`` часов (для idempotency key)."""
    value = as_utc(value) or utcnow()
    if hours <= 0:
        return value
    total = value.hour // hours * hours
    return value.replace(hour=total, minute=0, second=0, microsecond=0)


def minutes_between(earlier: datetime | None, later: datetime | None) -> float | None:
    earlier, later = as_utc(earlier), as_utc(later)
    if earlier is None or later is None:
        return None
    return (later - earlier).total_seconds() / 60.0


def lead_time_days(calculation_date: date | datetime, departure_date: date) -> int:
    """``lead_time_days`` = дата начала поездки − дата расчета (SCOPE-R C §7)."""
    if isinstance(calculation_date, datetime):
        calculation_date = (as_utc(calculation_date) or utcnow()).date()
    return (departure_date - calculation_date).days


def nights_between(departure: date, return_date: date) -> int:
    return (return_date - departure).days


def date_range(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def parse_datetime(value: Any) -> datetime | None:
    """Терпимый парсер ISO-8601 из ответов источников."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    # «2026-08-13 14:05» и «2026-08-13T14:05:00»
    for candidate in (text, text.replace(" ", "T")):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def parse_date(value: Any) -> date | None:
    parsed = parse_datetime(value)
    return parsed.date() if parsed else None


# --------------------------------------------------------------------------- #
# Деньги
# --------------------------------------------------------------------------- #

_MONEY_QUANT = Decimal("0.01")


def to_decimal(value: Any) -> Decimal | None:
    """Безопасное приведение к Decimal. Возвращает ``None`` для мусора."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, (int, float)):
        try:
            dec = Decimal(str(value))
        except InvalidOperation:
            return None
        return dec if dec.is_finite() else None
    if isinstance(value, str):
        cleaned = value.replace("\xa0", "").replace(" ", "").replace(",", ".")
        cleaned = re.sub(r"[^0-9.\-]", "", cleaned)
        if not cleaned or cleaned in {"-", ".", "-."}:
            return None
        try:
            dec = Decimal(cleaned)
        except InvalidOperation:
            return None
        return dec if dec.is_finite() else None
    return None


def money(value: Any) -> Decimal | None:
    """Приводит значение к денежному Decimal с 2 знаками (half-up)."""
    dec = to_decimal(value)
    if dec is None:
        return None
    return dec.quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP)


def round_display(value: Decimal | float | None, ndigits: int = 0) -> float | None:
    """Округление для UI/экспорта: не создаем ложной точности (Assumption 10)."""
    if value is None:
        return None
    dec = to_decimal(value)
    if dec is None:
        return None
    quant = Decimal(1) if ndigits <= 0 else Decimal(1).scaleb(-ndigits)
    return float(dec.quantize(quant, rounding=ROUND_HALF_UP))


# --------------------------------------------------------------------------- #
# Идентификаторы и хеши
# --------------------------------------------------------------------------- #


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


def _canonical(value: Any) -> Any:
    """Готовит структуру к стабильной сериализации."""
    if isinstance(value, dict):
        return {str(k): _canonical(value[k]) for k in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_canonical(item) for item in value), key=repr)
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def canonical_json(payload: Any) -> str:
    """Каноническая JSON-сериализация — основа всех воспроизводимых хешей."""
    return json.dumps(_canonical(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_hash(payload: Any) -> str:
    """SHA-256 канонического представления."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def stable_uuid(payload: Any) -> uuid.UUID:
    """Детерминированный UUID5 от канонического представления."""
    return uuid.uuid5(NAMESPACE_TCO, canonical_json(payload))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- #
# Текст
# --------------------------------------------------------------------------- #

_PROPERTY_NOISE = {
    "отель",
    "гостиница",
    "hotel",
    "hostel",
    "хостел",
    "апартаменты",
    "апарт",
    "apartment",
    "apartments",
    "гостевой",
    "дом",
    "guest",
    "house",
    "санаторий",
    "sanatorium",
    "resort",
    "spa",
    "мини",
    "the",
}


def normalize_text(value: str | None) -> str:
    """Нижний регистр, NFKD, только буквы/цифры/пробел."""
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", value).lower()
    text = text.replace("ё", "е")
    text = re.sub(r"[^0-9a-zа-я\s]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def property_key(name: str | None, address: str | None = None) -> str:
    """Ключ объекта размещения для межисточникового сопоставления.

    Из названия убираются типовые слова («отель», «гостиница»), из адреса —
    берется номер дома и первое значимое слово улицы. Это компромисс: точное
    сопоставление объектов требует геокодирования, которое вне рамок MVP.
    """
    tokens = [t for t in normalize_text(name).split() if t not in _PROPERTY_NOISE]
    key = " ".join(sorted(tokens))
    if address:
        addr_tokens = normalize_text(address).split()
        numbers = [t for t in addr_tokens if t.isdigit()][:1]
        words = [t for t in addr_tokens if not t.isdigit() and len(t) > 3][:1]
        if words or numbers:
            key += "|" + " ".join(words + numbers)
    return key


def truncate(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    return value if len(value) <= limit else value[: limit - 1] + "…"


def csv_safe(value: Any) -> Any:
    """Защита от CSV injection (SCOPE-R E §7).

    Значения, начинающиеся с ``= + - @``, табуляции или CR, префиксуются
    апострофом, чтобы Excel/LibreOffice не интерпретировали их как формулу.
    """
    if not isinstance(value, str):
        return value
    if value and value[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value
