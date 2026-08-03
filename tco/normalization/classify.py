"""Классификаторы сырых значений источников.

Все правила собраны в одном месте и покрыты тестами: именно здесь возникает
большая часть расхождений между источниками. Изменение любого правила требует
повышения ``NORMALIZATION_VERSION``.

Общий принцип: если признак нельзя определить надежно, возвращается ``UNKNOWN``,
и предложение получает статус неполной классификации. Догадки запрещены —
неклассифицированный багаж не попадает в багажные тарифы (SCOPE-R C §5).
"""

from __future__ import annotations

import re

from tco.core.enums import (
    AccommodationType,
    BaggageType,
    CancellationType,
    MealType,
    RailClass,
    RefundabilityType,
)
from tco.core.utils import normalize_text

# --------------------------------------------------------------------------- #
# Багаж
# --------------------------------------------------------------------------- #

#: Явные указания на отсутствие регистрируемого багажа.
_NO_CHECKED_PATTERNS = (
    "без багажа",
    "багаж не включен",
    "багаж не включён",
    "без регистрируемого багажа",
    "no baggage",
    "no checked",
    "without baggage",
    "hand luggage only",
    "0 мест",
    "багаж 0",
)

#: Явные указания на включенную ручную кладь.
_CABIN_PATTERNS = ("ручная кладь", "ручную кладь", "cabin", "carry-on", "carry on", "hand luggage")

#: Явные указания на включенный регистрируемый багаж.
_CHECKED_PATTERNS = (
    "багаж включен",
    "багаж включён",
    "с багажом",
    "checked baggage",
    "baggage included",
    "включен багаж",
)

#: Формулировки, означающие «источник не знает».
_UNKNOWN_PATTERNS = (
    "уточняется",
    "уточните",
    "по запросу",
    "не указан",
    "неизвестно",
    "unknown",
    "n/a",
    "-",
)

_WEIGHT_RE = re.compile(r"(\d{1,3})\s*(кг|kg)")
_PLACES_RE = re.compile(r"(\d{1,2})\s*(мест|место|pc|pcs|piece)")


def classify_baggage(raw: str | None, fare_family: str | None = None) -> BaggageType:
    """Определяет тип багажа по сырой строке источника.

    >>> classify_baggage("Без багажа, ручная кладь 10 кг")
    <BaggageType.CABIN_ONLY: 'CABIN_ONLY'>
    >>> classify_baggage("Багаж 20 кг")
    <BaggageType.CHECKED: 'CHECKED'>
    >>> classify_baggage("Уточняется у перевозчика")
    <BaggageType.UNKNOWN: 'UNKNOWN'>
    >>> classify_baggage(None)
    <BaggageType.UNKNOWN: 'UNKNOWN'>
    """
    text = normalize_text(raw)
    if not text:
        return BaggageType.UNKNOWN
    if any(pattern in text for pattern in _UNKNOWN_PATTERNS):
        return BaggageType.UNKNOWN

    has_no_checked = any(pattern in normalize_text(raw) for pattern in _NO_CHECKED_PATTERNS)
    has_cabin = any(pattern in text for pattern in _CABIN_PATTERNS)
    has_checked_word = any(pattern in text for pattern in _CHECKED_PATTERNS)

    if has_no_checked:
        # «Без багажа, ручная кладь 10 кг» — ручная кладь подтверждена.
        return BaggageType.CABIN_ONLY if has_cabin else BaggageType.CABIN_ONLY
    if has_checked_word:
        return BaggageType.CHECKED

    # Вес/места без слова «ручная кладь» трактуются как регистрируемый багаж.
    weight = _WEIGHT_RE.search(text)
    places = _PLACES_RE.search(text)
    if weight and not has_cabin:
        return BaggageType.CHECKED if int(weight.group(1)) >= 15 else BaggageType.CABIN_ONLY
    if places and not has_cabin:
        return BaggageType.CHECKED if int(places.group(1)) >= 1 else BaggageType.CABIN_ONLY
    if has_cabin:
        return BaggageType.CABIN_ONLY

    fare_text = normalize_text(fare_family)
    if fare_text:
        if any(word in fare_text for word in ("бюджет", "лайт", "light", "basic", "промо")):
            return BaggageType.CABIN_ONLY
        if any(word in fare_text for word in ("оптимум", "стандарт", "comfort", "flex", "плюс")):
            # Тарифное семейство — слабый сигнал, надежным подтверждением не является.
            return BaggageType.UNKNOWN
    return BaggageType.UNKNOWN


def baggage_satisfies(baggage: BaggageType, fare_type: str) -> bool:
    """Проходит ли предложение фильтр авиатарифа.

    ``UNKNOWN`` допускается только в ``CHEAPEST``.
    """
    if fare_type == "CHEAPEST":
        return True
    if fare_type == "CABIN_BAGGAGE":
        return baggage in (BaggageType.CABIN_ONLY, BaggageType.CHECKED)
    if fare_type == "CHECKED_BAGGAGE":
        return baggage == BaggageType.CHECKED
    return False


# --------------------------------------------------------------------------- #
# Возвратность
# --------------------------------------------------------------------------- #


def classify_refundability(raw: str | None) -> RefundabilityType:
    text = normalize_text(raw)
    if not text:
        return RefundabilityType.UNKNOWN
    if any(word in text for word in ("невозвратн", "non refundable", "nonrefundable", "no refund")):
        return RefundabilityType.NON_REFUNDABLE
    if "условно" in text or "со штрафом" in text or "with penalty" in text or "сбором" in text:
        return RefundabilityType.CONDITIONAL
    if "возвратн" in text or "refundable" in text or "возврат" in text:
        return RefundabilityType.REFUNDABLE
    return RefundabilityType.UNKNOWN


# --------------------------------------------------------------------------- #
# Питание
# --------------------------------------------------------------------------- #

_MEAL_RULES: tuple[tuple[MealType, tuple[str, ...]], ...] = (
    (MealType.ALL_INCLUSIVE, ("все включено", "всё включено", "all inclusive", "ai", "ultra")),
    (MealType.FULL_BOARD, ("полный пансион", "full board", "трехразов", "fb", "3 разов")),
    (MealType.HALF_BOARD, ("полупансион", "half board", "hb", "завтрак и ужин", "двухразов")),
    (
        MealType.BREAKFAST,
        ("завтрак", "breakfast", "bb", "bed and breakfast", "с завтраком", "континентальн"),
    ),
    (
        MealType.NO_MEALS,
        ("без питания", "room only", "ro", "питание не включено", "нет питания", "без завтрака"),
    ),
)


def classify_meal(raw: str | None) -> MealType:
    """Определяет тип питания.

    >>> classify_meal("Завтрак включен")
    <MealType.BREAKFAST: 'BREAKFAST'>
    >>> classify_meal("Без питания")
    <MealType.NO_MEALS: 'NO_MEALS'>
    >>> classify_meal("")
    <MealType.UNKNOWN: 'UNKNOWN'>
    """
    text = normalize_text(raw)
    if not text:
        return MealType.UNKNOWN
    # Прямое совпадение с кодом enum (источник уже отдал нормализованное значение).
    upper = (raw or "").strip().upper()
    if MealType.has(upper):
        return MealType(upper)
    for meal, patterns in _MEAL_RULES:
        if any(pattern in text for pattern in patterns):
            return meal
    return MealType.UNKNOWN


def meal_satisfies(offer_meal: MealType, requested: MealType, at_least: bool = True) -> bool:
    """Соответствие питания фильтру сценария.

    ``ANY`` пропускает все, включая ``UNKNOWN``. Для конкретного запроса
    ``UNKNOWN`` не проходит: неподтвержденное питание не выдается за нужное.
    """
    from tco.core.enums import MEAL_RANK

    if requested == MealType.ANY:
        return True
    if offer_meal == MealType.UNKNOWN:
        return False
    if not at_least:
        return offer_meal == requested
    return MEAL_RANK.get(offer_meal, -1) >= MEAL_RANK.get(requested, 99)


# --------------------------------------------------------------------------- #
# Условия отмены
# --------------------------------------------------------------------------- #


def classify_cancellation(raw: str | None) -> CancellationType:
    """Определяет условие отмены.

    >>> classify_cancellation("Бесплатная отмена до 24 часов")
    <CancellationType.FREE_CANCELLATION: 'FREE_CANCELLATION'>
    >>> classify_cancellation("NON_REFUNDABLE")
    <CancellationType.NON_REFUNDABLE: 'NON_REFUNDABLE'>
    """
    if raw is None:
        return CancellationType.UNKNOWN
    upper = raw.strip().upper()
    if upper == "FULLY_REFUNDABLE":
        return CancellationType.FREE_CANCELLATION
    if upper == "REFUNDABLE_WITH_PENALTY":
        return CancellationType.CONDITIONAL
    if CancellationType.has(upper):
        return CancellationType(upper)

    text = normalize_text(raw)
    if not text:
        return CancellationType.UNKNOWN
    if any(word in text for word in ("невозвратн", "non refundable", "без возврата", "no refund")):
        return CancellationType.NON_REFUNDABLE
    if "штраф" in text or "penalty" in text or "частичн" in text:
        return CancellationType.CONDITIONAL
    if any(
        word in text
        for word in ("бесплатная отмена", "free cancellation", "отмена бесплатно", "возврат 100")
    ):
        return CancellationType.FREE_CANCELLATION
    return CancellationType.UNKNOWN


def cancellation_satisfies(offer: CancellationType, requested: str) -> bool:
    if requested == "ANY":
        return True
    return offer == CancellationType.FREE_CANCELLATION


# --------------------------------------------------------------------------- #
# Тип размещения и звездность
# --------------------------------------------------------------------------- #

_ACCOMMODATION_RULES: tuple[tuple[AccommodationType, tuple[str, ...]], ...] = (
    (AccommodationType.SANATORIUM, ("санатор", "sanatorium", "пансионат", "здравниц")),
    (AccommodationType.HOSTEL, ("хостел", "hostel", "капсульн")),
    (
        AccommodationType.APARTMENT,
        ("апартамент", "апарт", "apartment", "apart", "студия", "квартир", "flat"),
    ),
    (
        AccommodationType.GUEST_HOUSE,
        ("гостевой дом", "guest house", "guesthouse", "усадьб", "коттедж", "вилла", "b&b"),
    ),
    (AccommodationType.HOTEL, ("отель", "гостиниц", "hotel", "resort", "мотель", "motel", "inn")),
)


def classify_accommodation_type(raw: str | None, fallback: AccommodationType | None = None) -> AccommodationType:
    """Определяет тип размещения по сырому значению источника."""
    if raw:
        upper = raw.strip().upper()
        if AccommodationType.has(upper):
            return AccommodationType(upper)
    text = normalize_text(raw)
    if text:
        for accommodation_type, patterns in _ACCOMMODATION_RULES:
            if any(pattern in text for pattern in patterns):
                return accommodation_type
    return fallback or AccommodationType.OTHER


def stars_satisfies(
    offer_stars: int | None,
    stars_unrated: bool,
    requested: str,
    accommodation_type: AccommodationType,
    exact: bool = True,
) -> bool:
    """Соответствие звездности фильтру сценария.

    Для типов размещения, где звезды неприменимы, фильтр по числу звезд
    не применяется.
    """
    from tco.core.enums import STARRED_ACCOMMODATION_TYPES

    if requested in ("ANY", "NOT_APPLICABLE"):
        return True
    if requested == "UNRATED":
        return stars_unrated or offer_stars is None
    if accommodation_type not in STARRED_ACCOMMODATION_TYPES:
        # Звезды неприменимы — конкретную категорию требовать нельзя.
        return False
    if not requested.isdigit():
        return True
    if offer_stars is None:
        return False
    return offer_stars == int(requested) if exact else offer_stars >= int(requested)


def stars_status(offer_stars: int | None, stars_unrated: bool, accommodation_type: AccommodationType) -> str:
    """Статус звездности: RATED / UNRATED / NOT_APPLICABLE / UNKNOWN."""
    from tco.core.enums import STARRED_ACCOMMODATION_TYPES

    if accommodation_type not in STARRED_ACCOMMODATION_TYPES:
        return "NOT_APPLICABLE"
    if stars_unrated:
        return "UNRATED"
    if offer_stars is not None and 1 <= offer_stars <= 5:
        return "RATED"
    return "UNKNOWN"


# --------------------------------------------------------------------------- #
# Классы ЖД
# --------------------------------------------------------------------------- #

_RAIL_CLASS_RULES: tuple[tuple[RailClass, tuple[str, ...]], ...] = (
    (RailClass.COMPARTMENT, ("compartment", "купе", "куп")),
    (RailClass.RESERVED_SEAT, ("reservedseat", "reserved seat", "плацкарт", "плац")),
)


def classify_rail_class(raw: str | None) -> RailClass | None:
    """Определяет класс вагона. ``None`` — класс вне поддерживаемых MVP.

    Люкс, СВ и сидячие вагоны сознательно не поддерживаются: SCOPE-R
    определяет только плацкарт и купе.
    """
    if not raw:
        return None
    upper = raw.strip().upper()
    if RailClass.has(upper):
        return RailClass(upper)
    text = normalize_text(raw).replace(" ", "")
    for rail_class, patterns in _RAIL_CLASS_RULES:
        if any(pattern.replace(" ", "") in text for pattern in patterns):
            return rail_class
    return None
