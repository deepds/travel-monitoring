"""Скользящая сетка наблюдений для витрины вариантов отдыха.

Витрина показывает цену по датам отправления на горизонт вперед. Это значит,
что наблюдать нужно не фиксированную дату, а расстояние до нее: сценарий с
жестко заданным 18 сентября каждый день становится ближе, и кривая «поехать
через N дней» из таких наблюдений не собирается.

Отсюда сетка: набор сценариев на каждый день горизонта, который ежедневно
досоздается на дальнем конце и снимается с наблюдения на ближнем. Отдельной
сущности для этого не заводится — сетка живет обычными сценариями, помеченными
тегом, и пользуется всем существующим механизмом сбора и расчета.

Состав на один день горизонта:

* проезд — 20 направленных маршрутов между пятью городами: ЖД плечом в одну
  сторону, авиа двумя рядами сразу (круговой тариф на каноническую
  длительность и плечо в одну сторону), без проживания;
* проживание — пять городов в трех категориях звездности, **бронь на одну
  ночь**, без транспорта.

Итого 75 сценариев на дату, 2250 на горизонт в 30 дней, плюс 45 контрольных
пятидневных броней — всего 2295.

ПОЧЕМУ БРОНЬ НА ОДНУ НОЧЬ, А НЕ НА ПЯТЬ

Пятидневная бронь дает цену ночи усреднением: заезд в среду означает, что в
брони среда, четверг, пятница, суббота и воскресенье сразу. На графике по
датам заезда это размазывает недельную волну — дорогие выходные разносятся на
пять соседних точек, — и вопрос «когда в городе дорого» такой график не
отвечает. Однодневная бронь дает одну точку на одну ночь и один день недели.

Пятидневные брони остались контрольными: витрине они не нужны — цена поездки
считается как ночь × число ночей, — но именно они показывают, насколько эта
оценка расходится с ценой реальной брони. Отели продают короткие брони дороже,
и величину расхождения нельзя предполагать, ее надо наблюдать. Трех дат из
тридцати на город для этого достаточно: 45 сценариев вместо 450.

Коэффициент пересчета одного в другое не вводится намеренно. Это та же ошибка,
от которой проект отказался с предкорзинными ценами Туту: собственная оценка,
выданная за наблюдение.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from itertools import permutations

from sqlalchemy import select
from sqlalchemy.orm import Session

from tco.core.enums import (
    AccommodationType,
    CancellationFilter,
    FlightFareType,
    MealType,
    ProfileStatus,
    RailClass,
    ScenarioType,
    StarsFilter,
    TransportType,
)
from tco.core.utils import utcnow
from tco.db.models.profile import CalculationProfile
from tco.db.models.scenario import TravelScenario
from tco.services.scenario_lifecycle import soft_delete_scenario
from tco.services.scenarios import DAILY_CADENCE_TAG, ScenarioDraft, create_scenario

#: Пять ключевых городов витрины.
SHOWCASE_CITIES: tuple[str, ...] = ("MOW", "LED", "AER", "KUF", "KZN")

#: Горизонт наблюдения в днях.
HORIZON_DAYS = 30

#: Длительность поездки для транспорта. Одна на всю сетку: перебирать еще и
#: длительность значило бы умножить объем наблюдений на число вариантов, а
#: витрина сравнивает направления между собой, а не поездки разной длины.
CANONICAL_NIGHTS = 5

#: Длительность основной брони проживания. Одна ночь — потому что одна точка
#: графика должна означать одну ночь и один день недели (см. модуль).
STAY_NIGHTS = 1

#: Смещения от сегодняшнего дня, на которые ставятся контрольные пятидневные
#: брони. Три из тридцати и намеренно разных дней недели: расхождение между
#: «ценой ночи в длинной брони» и «ценой одной ночевки» само зависит от дня
#: недели, и три одинаковых дня его не измерили бы.
CONTROL_STAY_OFFSETS: tuple[int, ...] = (7, 15, 26)

#: Теги, по которым расписание разводит прогоны по времени. Один залп из 1695
#: сценариев источник не выдерживает — именно это открыло размыкатель цепи
#: 6 августа.
RAIL_TAG = "showcase-rail"
AVIA_TAG = "showcase-avia"
#: Односторонние авиаплечи собираются своим окном: их столько же, сколько
#: круговых, и вместе они дали бы залп вдвое больше прежнего.
AVIA_ONE_WAY_TAG = "showcase-avia-1w"
STAY_TAG = "showcase-stay-1n"
CONTROL_STAY_TAG = "showcase-stay-5n"

#: Категории размещения. Третья звезда — массовый сегмент и основа графиков,
#: четвертая и пятая нужны выбору категории в первом блоке.
SHOWCASE_STARS: tuple[StarsFilter, ...] = (
    StarsFilter.S3,
    StarsFilter.S4,
    StarsFilter.S5,
)

#: Состав тургруппы: один человек. Семьи витрина не собирает.
SHOWCASE_ADULTS = 1

#: Тег, по которому сетка отличается от прочего каталога.
GRID_TAG = "showcase-grid"

#: Профиль расчета витрины.
GRID_PROFILE_CODE = "mass-market"


@dataclass(slots=True)
class GridReport:
    """Что сделала обслуживающая задача."""

    created: int = 0
    existing: int = 0
    retired: int = 0
    #: Сколько сценариев переведено на действующую версию методики витрины.
    reprofiled: int = 0
    #: Сколько существующих получили недостающие теги расписания.
    retagged: int = 0
    #: Сколько снято с наблюдения как выбывшие из состава сетки.
    dropped: int = 0
    horizon_until: date | None = None
    profile_missing: bool = False

    def as_dict(self) -> dict:
        return {
            "created": self.created,
            "existing": self.existing,
            "retired": self.retired,
            "reprofiled": self.reprofiled,
            "retagged": self.retagged,
            "dropped": self.dropped,
            "horizon_until": self.horizon_until.isoformat() if self.horizon_until else None,
            "profile_missing": self.profile_missing,
        }


def _accommodation_origin(city: str) -> str:
    """Город отправления для сценария, наблюдающего только проживание.

    Проживание от города отправления не зависит, но модель сценария требует
    обе точки маршрута и не допускает их совпадения. Поэтому берется любой
    другой город витрины — на наблюдение он не влияет и нужен только чтобы
    сценарий был корректен. Пара фиксирована, иначе отпечаток сценария менялся
    бы от запуска к запуску и сетка каждый день создавалась бы заново.
    """
    return "LED" if city == "MOW" else "MOW"


def _transport_drafts(origin: str, destination: str, departure: date) -> list[ScenarioDraft]:
    """Наблюдения проезда на одну дату отправления.

    ЖЕЛЕЗНАЯ ДОРОГА НАБЛЮДАЕТСЯ ПЛЕЧОМ, А НЕ ПОЕЗДКОЙ ТУДА-ОБРАТНО.

    Цена плеча у ЖД существует: источник отдает ее своим полем, и билет
    покупается на каждое направление отдельно. Поэтому круговое наблюдение там
    ничего не добавляло, а стоило вдвое дороже по обращениям — два поиска и до
    16 запросов карты мест вместо одного набора. Двадцать направленных
    маршрутов на тридцать дат покрывают любой интервал: он складывается из
    плеча «туда» на одну дату и плеча «обратно» на другую.

    АВИА НАБЛЮДАЕТСЯ ДВУМЯ РЯДАМИ СРАЗУ.

    Круговой тариф неделим — это одно число за поездку, и разложить его на
    плечи нельзя, не выдумывая. Но продается он только на конкретную пару дат,
    поэтому на произвольном интервале его просто нет. Односторонние плечи, в
    свою очередь, покрывают любой интервал, но стоят в сумме дороже кругового.

    Оба ряда описывают рынок правдиво и отвечают на разные вопросы, поэтому
    наблюдаются оба: «одним билетом» и «двумя билетами». Разница между ними —
    цена гибкости дат, и она же служит проверкой: сумма двух билетов дешевле
    кругового означает дефект выборки, а не находку.
    """
    ret = departure + timedelta(days=CANONICAL_NIGHTS)
    common = {
        "origin_city_code": origin,
        "destination_city_code": destination,
        "departure_date": departure,
        "adults": SHOWCASE_ADULTS,
        "accommodation_type": None,
        "stars": StarsFilter.NOT_APPLICABLE,
        "meal_type": MealType.ANY,
        "cancellation_filter": CancellationFilter.ANY,
        "scenario_type": ScenarioType.MONITORING,
    }
    return [
        # ЖД: плечо «туда». Совпадение дат означает поездку в одну сторону.
        ScenarioDraft(
            return_date=departure,
            transport_type=TransportType.RAIL,
            flight_fare_type=None,
            rail_class=RailClass.COMPARTMENT,
            tags=[GRID_TAG, DAILY_CADENCE_TAG, "showcase-transport", RAIL_TAG],
            **common,
        ),
        # Авиа: круговой тариф на каноническую длительность.
        ScenarioDraft(
            return_date=ret,
            transport_type=TransportType.AVIA,
            flight_fare_type=FlightFareType.CHEAPEST,
            rail_class=None,
            tags=[GRID_TAG, DAILY_CADENCE_TAG, "showcase-transport", AVIA_TAG],
            **common,
        ),
        # Авиа: плечо «туда», из которого складывается любой интервал.
        ScenarioDraft(
            return_date=departure,
            transport_type=TransportType.AVIA,
            flight_fare_type=FlightFareType.CHEAPEST,
            rail_class=None,
            tags=[
                GRID_TAG,
                DAILY_CADENCE_TAG,
                "showcase-transport",
                AVIA_ONE_WAY_TAG,
            ],
            **common,
        ),
    ]


def _accommodation_drafts(
    city: str, check_in: date, *, nights: int, tag: str
) -> list[ScenarioDraft]:
    check_out = check_in + timedelta(days=nights)
    return [
        ScenarioDraft(
            origin_city_code=_accommodation_origin(city),
            destination_city_code=city,
            departure_date=check_in,
            return_date=check_out,
            adults=SHOWCASE_ADULTS,
            transport_type=None,
            flight_fare_type=None,
            rail_class=None,
            accommodation_type=AccommodationType.HOTEL,
            stars=stars,
            meal_type=MealType.ANY,
            cancellation_filter=CancellationFilter.ANY,
            scenario_type=ScenarioType.MONITORING,
            # Прежний общий тег сохранен: по нему сетку отличают журналы и уже
            # выгруженные слепки. Новый разводит прогоны по времени.
            tags=[GRID_TAG, DAILY_CADENCE_TAG, "showcase-accommodation", tag],
        )
        for stars in SHOWCASE_STARS
    ]


def grid_drafts(*, today: date, horizon_days: int = HORIZON_DAYS) -> list[ScenarioDraft]:
    """Полный состав сетки на заданный горизонт."""
    drafts: list[ScenarioDraft] = []
    for offset in range(1, horizon_days + 1):
        day = today + timedelta(days=offset)
        for origin, destination in permutations(SHOWCASE_CITIES, 2):
            drafts.extend(_transport_drafts(origin, destination, day))
        for city in SHOWCASE_CITIES:
            drafts.extend(
                _accommodation_drafts(city, day, nights=STAY_NIGHTS, tag=STAY_TAG)
            )

    # Контрольные пятидневные брони — на три даты из тридцати, не на все.
    for offset in CONTROL_STAY_OFFSETS:
        if offset > horizon_days:
            continue
        day = today + timedelta(days=offset)
        for city in SHOWCASE_CITIES:
            drafts.extend(
                _accommodation_drafts(
                    city, day, nights=CANONICAL_NIGHTS, tag=CONTROL_STAY_TAG
                )
            )
    return drafts


def maintain_grid(
    session: Session,
    *,
    today: date | None = None,
    horizon_days: int = HORIZON_DAYS,
) -> GridReport:
    """Досоздает сетку на горизонт вперед и снимает истекшие даты.

    Повторный запуск в тот же день ничего не меняет: сценарий опознается по
    отпечатку, и ``create_scenario`` возвращает существующий.
    """
    today = today or utcnow().date()
    report = GridReport(horizon_until=today + timedelta(days=horizon_days))

    # Именно действующая версия и именно последняя: у кода бывает несколько
    # версий, и без отбора выбиралась бы произвольная — то есть методика витрины
    # менялась бы молча от порядка строк в таблице.
    profile = session.scalars(
        select(CalculationProfile)
        .where(CalculationProfile.code == GRID_PROFILE_CODE)
        .where(CalculationProfile.status == ProfileStatus.ACTIVE.value)
        .order_by(CalculationProfile.version_seq.desc())
    ).first()
    # Без своего профиля сетка считалась бы по базовой методике — с другими
    # правилами по классу вагона и возвратности, то есть показывала бы не то,
    # что просили. Лучше не создавать ее вовсе и сказать об этом явно.
    if profile is None:
        report.profile_missing = True
        return report

    wanted: set[str] = set()
    for draft in grid_drafts(today=today, horizon_days=horizon_days):
        scenario, created = create_scenario(session, draft, profile=profile, created_by="grid")
        # Признак ставится и существующим: сетка опознается по отпечатку, и
        # сценарий, заведенный до появления поля, иначе остался бы неотмеченным
        # и продолжил бы попадать в агрегаты рынка.
        scenario.is_showcase_grid = True
        wanted.add(scenario.fingerprint)
        # Теги существующего дополняются, а не переписываются: по ним расписание
        # разводит прогоны по времени, и сценарий, заведенный до появления
        # нового тега, иначе не попал бы ни в одно окно и молча выпал бы из
        # наблюдения. В отпечаток теги не входят, поэтому дополнение безопасно.
        missing_tags = [tag for tag in draft.tags if tag not in (scenario.tags or [])]
        if missing_tags:
            scenario.tags = list(scenario.tags or []) + missing_tags
            report.retagged += 1
        if created:
            report.created += 1
        else:
            report.existing += 1

    # Закрепление за методикой обновляется у всей действующей сетки, а не только
    # у сегодняшних черновиков: новая версия профиля иначе не дошла бы до уже
    # заведенных сценариев. Черновиками не покрыты даты, вышедшие из горизонта,
    # но еще не снятые с наблюдения, — на переходе версий они остались бы за
    # архивным профилем, а он подменяется методикой по умолчанию молча: расчет
    # состоится, но по чужим правилам, и по цифрам это не видно.
    stale = session.scalars(
        select(TravelScenario)
        .where(TravelScenario.deleted_at.is_(None))
        .where(TravelScenario.is_showcase_grid.is_(True))
        .where(TravelScenario.calculation_profile_id != profile.id)
    ).all()
    for scenario in stale:
        scenario.calculation_profile_id = profile.id
    report.reprofiled = len(stale)

    report.dropped = retire_out_of_grid(session, wanted, today=today, horizon_days=horizon_days)
    report.retired = retire_expired(session, today=today)
    return report


def retire_out_of_grid(
    session: Session,
    wanted: set[str],
    *,
    today: date,
    horizon_days: int = HORIZON_DAYS,
) -> int:
    """Снимает с наблюдения сценарии сетки, выбывшие из ее состава.

    Нужно при изменении самого состава — например, когда пятидневные брони
    проживания перестали наблюдаться на каждую дату и остались только
    контрольными. Без этого прежние 450 сценариев продолжали бы собираться
    молча: они активны, помечены сеткой и попадают в суточный прогон, а бюджет
    обращений к источнику при этом удваивается.

    Отбор ограничен горизонтом: даты за его пределами снимает ``retire_expired``
    по своему правилу, и трогать их здесь незачем.
    """
    # Пустой состав снял бы с наблюдения всю сетку целиком. Это не бывает
    # штатно и означало бы ошибку в вызывающем коде, а не выбывшие сценарии.
    if not wanted:
        return 0

    horizon = today + timedelta(days=horizon_days)
    obsolete = session.scalars(
        select(TravelScenario)
        .where(TravelScenario.deleted_at.is_(None))
        .where(TravelScenario.is_showcase_grid.is_(True))
        .where(TravelScenario.departure_date >= today)
        .where(TravelScenario.departure_date <= horizon)
        .where(TravelScenario.fingerprint.not_in(wanted))
    ).all()

    for scenario in obsolete:
        soft_delete_scenario(scenario)
    return len(obsolete)


def retire_expired(session: Session, *, today: date | None = None) -> int:
    """Снимает с наблюдения сценарии сетки, дата отправления которых прошла."""
    today = today or utcnow().date()
    past = session.scalars(
        select(TravelScenario)
        .where(TravelScenario.deleted_at.is_(None))
        .where(TravelScenario.is_showcase_grid.is_(True))
        .where(TravelScenario.departure_date < today)
    ).all()

    for scenario in past:
        soft_delete_scenario(scenario)
    return len(past)
