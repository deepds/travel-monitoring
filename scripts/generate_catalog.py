"""Генератор управляемых каталогов сценариев.

Формирует два файла в формате, который принимает штатный импорт
(``POST /api/v1/admin/scenarios/import``, колонки ``CSV_COLUMNS``):

* ``catalog/monitoring_scenarios.csv`` — не менее 100 активных сценариев
  мониторинга (Definition of Done п.4);
* ``catalog/challenge_set.csv`` — 20–30 контрольных сценариев, включая
  граничные и заведомо проблемные случаи (SCOPE-R E §2).

Полный декартов набор маршрутов сознательно не создается: SCOPE-R C §2
предписывает управляемый каталог приоритетных направлений.

Даты абсолютные (SCOPE-R P §14): rolling-сценарии остаются в P1. Поэтому
каталог периодически перегенерируется — запуск идемпотентен относительно
``--base-date``.

Использование::

    python scripts/generate_catalog.py                 # от сегодняшней даты
    python scripts/generate_catalog.py --base-date 2026-09-01
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tco.services.scenarios import CSV_COLUMNS  # noqa: E402

CATALOG_DIR = REPO_ROOT / "catalog"

#: Города, у которых нет железнодорожной связи с материковой сетью.
NO_RAIL = {"UUS"}

CITY_NAMES = {
    "MOW": "Москва",
    "LED": "Санкт-Петербург",
    "KRR": "Краснодар",
    "AER": "Сочи",
    "KGD": "Калининград",
    "UUS": "Южно-Сахалинск",
}


@dataclass(frozen=True, slots=True)
class Route:
    """Приоритетное направление наблюдения."""

    origin: str
    destination: str
    priority: int
    tags: tuple[str, ...] = ()
    #: Оба направления или только прямое.
    both_ways: bool = True

    def pairs(self) -> list[tuple[str, str]]:
        forward = [(self.origin, self.destination)]
        return forward + [(self.destination, self.origin)] if self.both_ways else forward


#: Управляемый список приоритетных направлений.
ROUTES: tuple[Route, ...] = (
    Route("MOW", "LED", 10, ("business", "high-traffic")),
    Route("MOW", "AER", 10, ("leisure", "high-traffic")),
    Route("MOW", "KRR", 20, ("business", "south")),
    Route("MOW", "KGD", 20, ("leisure", "exclave")),
    Route("MOW", "UUS", 30, ("far-east", "long-haul")),
    Route("LED", "AER", 20, ("leisure", "south")),
    Route("LED", "KRR", 30, ("south",)),
    Route("LED", "KGD", 30, ("exclave",)),
    Route("KRR", "AER", 40, ("regional", "short-haul")),
    Route("AER", "KGD", 50, ("cross-region",), both_ways=False),
)


@dataclass(slots=True)
class Variant:
    """Вариация параметров поверх маршрута."""

    label: str
    transport: str
    fare: str | None = None
    rail_class: str | None = None
    accommodation: str = "HOTEL"
    stars: str = "ANY"
    meal: str = "ANY"
    cancellation: str = "ANY"
    adults: int = 2
    children: tuple[int, ...] = ()
    nights: int = 5
    tags: tuple[str, ...] = ()


#: Профили наблюдения. Покрывают оба вида транспорта, три авиатарифа,
#: оба класса ЖД, четыре типа размещения и оба состава туристов.
VARIANTS: tuple[Variant, ...] = (
    Variant(
        "avia-cheap-h3",
        "AVIA",
        fare="CHEAPEST",
        accommodation="HOTEL",
        stars="3",
        nights=5,
        tags=("basic",),
    ),
    Variant(
        "avia-cabin-h4",
        "AVIA",
        fare="CABIN_BAGGAGE",
        accommodation="HOTEL",
        stars="4",
        meal="BREAKFAST",
        nights=6,
        tags=("comfort",),
    ),
    Variant(
        "avia-checked-h4-family",
        "AVIA",
        fare="CHECKED_BAGGAGE",
        accommodation="HOTEL",
        stars="4",
        meal="BREAKFAST",
        adults=2,
        children=(7,),
        nights=7,
        tags=("family",),
    ),
    Variant(
        "avia-cheap-apart",
        "AVIA",
        fare="CHEAPEST",
        accommodation="APARTMENT",
        stars="NOT_APPLICABLE",
        meal="NO_MEALS",
        nights=4,
        tags=("budget",),
    ),
    Variant(
        "rail-compartment-h3",
        "RAIL",
        rail_class="COMPARTMENT",
        accommodation="HOTEL",
        stars="3",
        nights=4,
        tags=("rail",),
    ),
    Variant(
        "rail-reserved-hostel",
        "RAIL",
        rail_class="RESERVED_SEAT",
        accommodation="HOSTEL",
        stars="NOT_APPLICABLE",
        meal="NO_MEALS",
        nights=3,
        tags=("rail", "budget"),
    ),
)

#: Горизонты бронирования: ближний, средний и дальний.
LEAD_TIMES: tuple[int, ...] = (30, 60, 90)


def _row(
    *,
    origin: str,
    destination: str,
    departure: date,
    nights: int,
    variant: Variant,
    priority: int,
    tags: tuple[str, ...],
    notes: str,
) -> dict[str, object]:
    return_date = departure + timedelta(days=nights)
    return {
        # ``code`` не задается: сервис импорта построит его из отпечатка,
        # что гарантирует стабильность при повторной генерации.
        "code": "",
        "name": "",
        "origin_city_code": origin,
        "destination_city_code": destination,
        "departure_date": departure.isoformat(),
        "return_date": return_date.isoformat(),
        "adults": variant.adults,
        "children_ages": ";".join(str(age) for age in variant.children),
        "transport_type": variant.transport,
        "flight_fare_type": variant.fare or "",
        "rail_class": variant.rail_class or "",
        "accommodation_type": variant.accommodation,
        "stars": variant.stars,
        "meal_type": variant.meal,
        "cancellation_filter": variant.cancellation,
        "active_from": "",
        # Сценарий автоматически деактивируется после окончания поездки.
        "active_until": return_date.isoformat(),
        "priority": priority,
        "tags": ";".join(tags),
        "notes": notes,
    }


def build_monitoring(base: date) -> list[dict[str, object]]:
    """Строит каталог мониторинга: маршруты × варианты × горизонты."""
    rows: list[dict[str, object]] = []

    for route in ROUTES:
        for origin, destination in route.pairs():
            for index, variant in enumerate(VARIANTS):
                if variant.transport == "RAIL" and (
                    origin in NO_RAIL or destination in NO_RAIL
                ):
                    # ЖД-сценарий для острова невалиден by design — в каталог
                    # мониторинга он не попадает, но остается в challenge set
                    # как проверка отбраковки.
                    continue

                # Горизонт чередуется, чтобы наблюдать зависимость цены от
                # глубины бронирования, не раздувая каталог.
                lead = LEAD_TIMES[index % len(LEAD_TIMES)]
                departure = base + timedelta(days=lead)
                rows.append(
                    _row(
                        origin=origin,
                        destination=destination,
                        departure=departure,
                        nights=variant.nights,
                        variant=variant,
                        priority=route.priority,
                        tags=("monitoring", *route.tags, *variant.tags, f"lead{lead}"),
                        notes=(
                            f"{CITY_NAMES[origin]} → {CITY_NAMES[destination]}, "
                            f"профиль «{variant.label}», горизонт {lead} дн."
                        ),
                    )
                )
    return rows


@dataclass(slots=True)
class ChallengeCase:
    """Контрольный сценарий с ожидаемым поведением системы."""

    case: str
    origin: str
    destination: str
    variant: Variant
    lead_days: int
    expectation: str
    tags: tuple[str, ...] = field(default_factory=tuple)


def challenge_cases() -> list[ChallengeCase]:
    """20–30 контрольных сценариев (SCOPE-R E §2).

    Покрывают: разные города и даты, авиа и ЖД, все три авиатарифа, оба
    класса ЖД, типы размещения, 3★/4★/5★ и «без звезд», семейный шаблон,
    отсутствие данных, единственный источник и сильное расхождение.
    """
    hotel3 = Variant("h3", "AVIA", fare="CHEAPEST", accommodation="HOTEL", stars="3", nights=5)
    hotel4 = Variant(
        "h4", "AVIA", fare="CABIN_BAGGAGE", accommodation="HOTEL", stars="4",
        meal="BREAKFAST", nights=5,
    )
    hotel5 = Variant(
        "h5", "AVIA", fare="CHECKED_BAGGAGE", accommodation="HOTEL", stars="5",
        meal="BREAKFAST", cancellation="FREE_CANCELLATION", nights=4,
    )
    unrated = Variant(
        "unrated", "AVIA", fare="CHEAPEST", accommodation="HOTEL", stars="UNRATED", nights=4
    )
    apartment = Variant(
        "apart", "AVIA", fare="CHEAPEST", accommodation="APARTMENT",
        stars="NOT_APPLICABLE", meal="NO_MEALS", nights=4,
    )
    hostel = Variant(
        "hostel", "AVIA", fare="CHEAPEST", accommodation="HOSTEL",
        stars="NOT_APPLICABLE", meal="NO_MEALS", nights=3,
    )
    guest_house = Variant(
        "guest", "AVIA", fare="CHEAPEST", accommodation="GUEST_HOUSE",
        stars="NOT_APPLICABLE", nights=5,
    )
    sanatorium = Variant(
        "sanatorium", "AVIA", fare="CHEAPEST", accommodation="SANATORIUM",
        stars="3", meal="FULL_BOARD", nights=7,
    )
    family = Variant(
        "family", "AVIA", fare="CABIN_BAGGAGE", accommodation="HOTEL", stars="4",
        meal="BREAKFAST", adults=2, children=(7,), nights=7,
    )
    family_two_kids = Variant(
        "family2", "AVIA", fare="CHECKED_BAGGAGE", accommodation="HOTEL", stars="4",
        meal="BREAKFAST", adults=2, children=(4, 11), nights=6,
    )
    solo = Variant(
        "solo", "AVIA", fare="CHECKED_BAGGAGE", accommodation="HOTEL", stars="4",
        meal="BREAKFAST", adults=1, nights=3,
    )
    rail_kupe = Variant(
        "kupe", "RAIL", rail_class="COMPARTMENT", accommodation="HOTEL", stars="3", nights=4
    )
    rail_plaz = Variant(
        "plaz", "RAIL", rail_class="RESERVED_SEAT", accommodation="HOTEL", stars="3", nights=4
    )
    rail_family = Variant(
        "rail-family", "RAIL", rail_class="COMPARTMENT", accommodation="APARTMENT",
        stars="NOT_APPLICABLE", adults=2, children=(7,), nights=5,
    )
    free_cancel = Variant(
        "free-cancel", "AVIA", fare="CHEAPEST", accommodation="HOTEL", stars="4",
        cancellation="FREE_CANCELLATION", nights=4,
    )

    return [
        # --- Базовое покрытие городов и транспорта ----------------------- #
        ChallengeCase("CS01", "MOW", "LED", rail_kupe, 45,
                      "ЖД купе, оба источника транспорта доступны: ожидается SUCCESS с 2 источниками",
                      ("rail", "two-source")),
        ChallengeCase("CS02", "MOW", "LED", rail_plaz, 45,
                      "Плацкарт агрегируется отдельно от купе: медиана должна быть заметно ниже CS01",
                      ("rail", "class-separation")),
        ChallengeCase("CS03", "MOW", "AER", hotel3, 30,
                      "Массовое летнее направление, 3★: базовый эталон сравнения с ручной проверкой",
                      ("avia", "baseline")),
        ChallengeCase("CS04", "MOW", "AER", hotel4, 60,
                      "4★ с завтраком и ручной кладью: проверка фильтра питания и багажа",
                      ("avia", "meal", "baggage")),
        ChallengeCase("CS05", "MOW", "AER", hotel5, 90,
                      "5★ со свободной отменой: проверка фильтра отмены и малой выборки",
                      ("avia", "cancellation", "small-sample")),
        ChallengeCase("CS06", "LED", "AER", hotel4, 45,
                      "Второй крупный город отправления: проверка независимости от MOW",
                      ("avia",)),
        ChallengeCase("CS07", "MOW", "KRR", hotel3, 30,
                      "Краснодар: региональное направление с ЖД-альтернативой",
                      ("avia",)),
        ChallengeCase("CS08", "MOW", "KGD", hotel4, 60,
                      "Эксклав: авиа основной способ, ЖД идет транзитом через третьи страны",
                      ("avia", "exclave")),
        ChallengeCase("CS09", "MOW", "UUS", hotel4, 90,
                      "Дальний Восток: длинное плечо, высокая цена, малое число перевозчиков",
                      ("avia", "long-haul")),
        ChallengeCase("CS10", "KRR", "AER", rail_kupe, 30,
                      "Короткое ЖД-плечо: проверка корректности при малой длительности поездки",
                      ("rail", "short-haul")),

        # --- Тарифы и классы ---------------------------------------------- #
        ChallengeCase("CS11", "MOW", "LED", solo, 21,
                      "Один взрослый, багажный тариф: проверка пересчета цены на 1 пассажира",
                      ("avia", "single-traveler", "baggage")),
        ChallengeCase("CS12", "LED", "MOW", rail_kupe, 14,
                      "Ближний горизонт 14 дней: цена должна быть выше дальнего горизонта",
                      ("rail", "lead-time")),
        ChallengeCase("CS13", "MOW", "AER", free_cancel, 40,
                      "Свободная отмена как жесткий фильтр: неклассифицированные условия исключаются",
                      ("avia", "cancellation")),

        # --- Типы размещения и звездность ---------------------------------- #
        ChallengeCase("CS14", "MOW", "AER", apartment, 35,
                      "Апартаменты: звездность неприменима, фильтр по звездам не применяется",
                      ("accommodation", "stars-na")),
        ChallengeCase("CS15", "LED", "KRR", hostel, 35,
                      "Хостел без питания: минимальная ценовая полка",
                      ("accommodation", "budget")),
        ChallengeCase("CS16", "MOW", "KRR", guest_house, 50,
                      "Гостевой дом: тип размещения с ограниченным предложением",
                      ("accommodation", "small-sample")),
        ChallengeCase("CS17", "MOW", "AER", sanatorium, 70,
                      "Санаторий с полным пансионом: редкое сочетание типа и питания, "
                      "выборка заведомо узкая — допустим PARTIAL_SUCCESS",
                      ("accommodation", "meal", "small-sample")),
        ChallengeCase("CS18", "LED", "AER", unrated, 40,
                      "Гостиница без официальной классификации: UNRATED не равно «звезды неизвестны»",
                      ("accommodation", "unrated")),

        # --- Состав туристов ------------------------------------------------ #
        ChallengeCase("CS19", "MOW", "AER", family, 55,
                      "Семейный шаблон 2+1 (7 лет): вместимость номера обязана быть подтверждена",
                      ("family", "capacity")),
        ChallengeCase("CS20", "MOW", "KGD", family_two_kids, 65,
                      "Массив возрастов детей 2+2: проверка поддержки нескольких детей моделью",
                      ("family", "capacity", "multi-child")),
        ChallengeCase("CS21", "LED", "MOW", rail_family, 45,
                      "Семья на поезде в апартаментах: комбинация редких параметров",
                      ("family", "rail")),

        # --- Граничные и негативные случаи ------------------------------------ #
        ChallengeCase("CS22", "MOW", "UUS", rail_kupe, 60,
                      "ЖД на Сахалин: остров без ЖД-связи — ожидается UNSUPPORTED без внешних запросов",
                      ("negative", "unsupported", "no-rail")),
        ChallengeCase("CS23", "MOW", "MOW", hotel3, 30,
                      "Совпадение городов отправления и назначения — ожидается UNSUPPORTED",
                      ("negative", "unsupported", "same-city")),
        ChallengeCase("CS24", "MOW", "AER", hotel3, -10,
                      "Дата в прошлом — ожидается UNSUPPORTED, внешние API не вызываются",
                      ("negative", "unsupported", "past-date")),
        ChallengeCase("CS25", "MOW", "AER", hotel3, 400,
                      "Дата за пределами горизонта всех источников — ожидается UNSUPPORTED "
                      "на этапе валидации, внешние запросы не выполняются",
                      ("negative", "unsupported", "horizon")),
        ChallengeCase("CS26", "AER", "KGD", hotel4, 75,
                      "Редкое межрегиональное направление: вероятен единственный источник "
                      "и статус COMPLETE_SINGLE_SOURCE",
                      ("single-source", "small-sample")),
        ChallengeCase("CS27", "KGD", "AER", hostel, 80,
                      "Хостел на редком направлении: вероятен NO_DATA по компоненту проживания "
                      "и PARTIAL_SUCCESS",
                      ("no-data", "partial")),
        ChallengeCase("CS28", "MOW", "UUS", hotel5, 100,
                      "5★ на дальнем направлении: ожидается высокое межисточниковое расхождение "
                      "и снижение Quality Score",
                      ("disagreement", "small-sample")),
        ChallengeCase("CS29", "LED", "KGD", family, 50,
                      "Семья в эксклав: сочетание подтверждения вместимости и узкой выборки",
                      ("family", "capacity", "small-sample")),
        ChallengeCase("CS30", "KRR", "MOW", rail_plaz, 25,
                      "Обратное ЖД-направление в плацкарте: проверка симметрии маршрута",
                      ("rail", "reverse")),
    ]


def build_challenge(base: date) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for case in challenge_cases():
        departure = base + timedelta(days=case.lead_days)
        rows.append(
            _row(
                origin=case.origin,
                destination=case.destination,
                departure=departure,
                nights=case.variant.nights,
                variant=case.variant,
                priority=1,
                tags=("challenge", case.case, *case.tags),
                notes=f"[{case.case}] {case.expectation}",
            )
        )
    return rows


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-date",
        type=date.fromisoformat,
        default=date.today(),
        help="Опорная дата, от которой отсчитываются горизонты (YYYY-MM-DD)",
    )
    parser.add_argument("--output-dir", type=Path, default=CATALOG_DIR)
    args = parser.parse_args()

    monitoring = build_monitoring(args.base_date)
    challenge = build_challenge(args.base_date)

    monitoring_path = args.output_dir / "monitoring_scenarios.csv"
    challenge_path = args.output_dir / "challenge_set.csv"
    write_csv(monitoring_path, monitoring)
    write_csv(challenge_path, challenge)

    print(f"Опорная дата: {args.base_date.isoformat()}")
    print(f"{monitoring_path.relative_to(REPO_ROOT)}: {len(monitoring)} сценариев мониторинга")
    print(f"{challenge_path.relative_to(REPO_ROOT)}: {len(challenge)} контрольных сценариев")

    if len(monitoring) < 100:
        print(
            f"ОШИБКА: каталог мониторинга содержит {len(monitoring)} сценариев, "
            "требуется не менее 100 (Definition of Done п.4)",
            file=sys.stderr,
        )
        return 1
    if not 20 <= len(challenge) <= 30:
        print(
            f"ОШИБКА: challenge set содержит {len(challenge)} сценариев, требуется 20–30",
            file=sys.stderr,
        )
        return 1

    # Краткая сводка покрытия — чтобы каталог не деградировал незаметно.
    transports = {row["transport_type"] for row in monitoring}
    accommodations = {row["accommodation_type"] for row in monitoring}
    routes = {(row["origin_city_code"], row["destination_city_code"]) for row in monitoring}
    cities = {row["origin_city_code"] for row in monitoring} | {
        row["destination_city_code"] for row in monitoring
    }
    print(
        f"Покрытие мониторинга: городов {len(cities)}, маршрутов {len(routes)}, "
        f"транспорт {sorted(transports)}, размещение {sorted(accommodations)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
