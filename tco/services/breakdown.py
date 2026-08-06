"""Разбор цены: денежная воронка и пересчет «что если».

Витрина показывает одно число там, где за ним стоят сотни предложений и
десяток правил отбора. Вопрос «почему получилась эта цифра» до сих пор
закрывался счетчиками отброшенного — сколько предложений выбыло на каждом
шаге. Этого мало: счетчик не отвечает, во сколько правило обошлось в рублях, а
именно об этом спрашивают, когда цифра не сходится с ожиданием.

Здесь два инструмента.

**Денежная воронка** показывает, как менялась бы медиана на каждом шаге
отбора. Видно, какое правило и на сколько подняло цену и какое срезало выборку
до неустойчивого размера.

**Пересчет «что если»** отвечает на вопрос «а если считать с возвратными
тарифами». Это выполнимо, потому что предложения не удаляются: каждое хранится
с пометкой, почему оно отброшено (``exclusion_reason``, ``exclusion_detail``),
и пересчет идет по сохраненным данным — к источникам обращений нет.

Результат пересчета помечен предварительным и официальную цифру не подменяет:
методика меняется только новой версией профиля, активная неизменяема.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from tco.core.enums import ComponentType, ExclusionReason, OfferType
from tco.core.utils import to_decimal
from tco.db.models.offer import Offer
from tco.engine.explain import EXCLUSION_TITLES, FILTER_REASON_TITLES
from tco.engine.statistics import percentile

#: Шаги воронки в том порядке, в каком их применяет отбор. Порядок именно
#: такой, а не произвольный: фильтр профиля работает до схлопывания тарифов,
#: поэтому рейс не теряется из-за дешевого возвратного тарифа.
FUNNEL_STEPS: tuple[tuple[str, str], ...] = (
    (ExclusionReason.INVALID.value, "Без невалидных предложений"),
    (ExclusionReason.PROFILE_FILTER.value, "После правил методики"),
    (ExclusionReason.TECHNICAL_DUPLICATE.value, "Без повторов внутри источника"),
    (ExclusionReason.FARE_VARIANT.value, "Один тариф на рейс"),
    (ExclusionReason.OUTLIER.value, "Без ценовых всплесков"),
    (ExclusionReason.SOURCE_NOT_ELIGIBLE.value, "Только допущенные источники"),
)


def _component_types(component: ComponentType) -> tuple[str, ...]:
    if component == ComponentType.ACCOMMODATION:
        return (OfferType.ACCOMMODATION.value,)
    return (OfferType.FLIGHT.value, OfferType.RAIL.value)


def _stats(prices: Sequence[Decimal]) -> dict[str, Any]:
    if not prices:
        return {"count": 0, "median": None, "min": None, "max": None, "p25": None, "p75": None}
    ordered = sorted(prices)
    return {
        "count": len(ordered),
        "median": float(percentile(ordered, 0.5, "LINEAR") or 0),
        "min": float(ordered[0]),
        "max": float(ordered[-1]),
        "p25": float(percentile(ordered, 0.25, "LINEAR") or 0),
        "p75": float(percentile(ordered, 0.75, "LINEAR") or 0),
    }


def _load_offers(
    session: Session, snapshot_id: Any, component: ComponentType
) -> list[Offer]:
    return list(
        session.scalars(
            select(Offer)
            .where(Offer.market_snapshot_id == snapshot_id)
            .where(Offer.offer_type.in_(_component_types(component)))
        ).all()
    )


def _detail_label(offer: Offer) -> str:
    """Человеческое имя причины, по которой предложение выбыло."""
    detail = offer.exclusion_detail or ""
    if offer.exclusion_reason == ExclusionReason.PROFILE_FILTER.value:
        # В детали фильтра лежит код правила — его словарь и переводит.
        return FILTER_REASON_TITLES.get(detail, detail or "правило методики")
    return EXCLUSION_TITLES.get(offer.exclusion_reason, offer.exclusion_reason)


def funnel(
    session: Session, *, snapshot_id: Any, component: ComponentType
) -> dict[str, Any]:
    """Как менялась бы цифра на каждом шаге отбора.

    Считается не «сколько отброшено», а «сколько стоило правило»: медиана до
    шага и после него. Ровно эту работу до сих пор делали запросами к базе
    руками — например, когда выяснялось, что фильтр класса вагона срезает
    половину купе и ничего не разделяет.
    """
    offers = _load_offers(session, snapshot_id, component)
    prices = {
        offer.id: to_decimal(offer.total_price)
        for offer in offers
        if to_decimal(offer.total_price) is not None
    }

    remaining = {offer.id for offer in offers if offer.id in prices}
    steps: list[dict[str, Any]] = [
        {
            "step": "COLLECTED",
            "title": "Собрано источниками",
            "removed": 0,
            "reasons": {},
            **_stats([prices[key] for key in remaining]),
        }
    ]

    previous_median = steps[0]["median"]
    for reason, title in FUNNEL_STEPS:
        dropped = [
            offer
            for offer in offers
            if offer.id in remaining and offer.exclusion_reason == reason
        ]
        if not dropped:
            continue
        for offer in dropped:
            remaining.discard(offer.id)
        stats = _stats([prices[key] for key in remaining])
        reasons: dict[str, int] = {}
        for offer in dropped:
            label = _detail_label(offer)
            reasons[label] = reasons.get(label, 0) + 1
        steps.append(
            {
                "step": reason,
                "title": title,
                "removed": len(dropped),
                "reasons": dict(sorted(reasons.items(), key=lambda item: -item[1])),
                # Цена правила в рублях: на сколько сдвинулась медиана.
                "median_delta": (
                    round(stats["median"] - previous_median, 2)
                    if stats["median"] is not None and previous_median is not None
                    else None
                ),
                **stats,
            }
        )
        previous_median = stats["median"]

    return {
        "component": component.value,
        "steps": steps,
        "final": steps[-1] if steps else None,
    }


def what_if(
    session: Session,
    *,
    snapshot_id: Any,
    component: ComponentType,
    ignore_filter_reasons: Sequence[str] = (),
    ignore_exclusion_reasons: Sequence[str] = (),
) -> dict[str, Any]:
    """Медиана снимка, если не применять выбранные правила.

    Ответ на вопросы вида «а если считать с возвратными тарифами» и «а если
    с апартаментами». Именно так проверялся фильтр классов купе — руками и за
    полдня; с этим пересчетом проверка занимает минуту, и ее может сделать сам
    заказчик.

    Обращений к источникам здесь нет и быть не может: считается по сохраненным
    предложениям снимка. Результат предварительный — официальная цифра
    меняется только новой версией методики.
    """
    offers = _load_offers(session, snapshot_id, component)
    ignored_filters = {item.upper() for item in ignore_filter_reasons}
    ignored_exclusions = {item.upper() for item in ignore_exclusion_reasons}

    def counts(offer: Offer) -> bool:
        reason = offer.exclusion_reason
        if reason == ExclusionReason.NONE.value:
            return True
        if reason in ignored_exclusions:
            return True
        if (
            reason == ExclusionReason.PROFILE_FILTER.value
            and (offer.exclusion_detail or "").upper() in ignored_filters
        ):
            return True
        return False

    official = [
        to_decimal(offer.total_price)
        for offer in offers
        if offer.exclusion_reason == ExclusionReason.NONE.value
    ]
    revised = [to_decimal(offer.total_price) for offer in offers if counts(offer)]

    official_stats = _stats([item for item in official if item is not None])
    revised_stats = _stats([item for item in revised if item is not None])
    delta = (
        round(revised_stats["median"] - official_stats["median"], 2)
        if revised_stats["median"] is not None and official_stats["median"] is not None
        else None
    )

    return {
        "component": component.value,
        "official": official_stats,
        "revised": revised_stats,
        "median_delta": delta,
        "ignored": {
            "filter_reasons": sorted(ignored_filters),
            "exclusion_reasons": sorted(ignored_exclusions),
        },
        "preliminary": True,
        "note": (
            "Предварительный пересчет по сохраненным предложениям. "
            "Официальная цифра считается действующей методикой и не меняется."
        ),
    }


def available_switches(
    session: Session, *, snapshot_id: Any, component: ComponentType
) -> list[dict[str, Any]]:
    """Какие правила вообще что-то отсекли в этом снимке.

    Переключатель, который ничего не изменит, показывать незачем: он
    подсказывает несуществующий запас.
    """
    rows = session.execute(
        select(Offer.exclusion_reason, Offer.exclusion_detail, Offer.id)
        .where(Offer.market_snapshot_id == snapshot_id)
        .where(Offer.offer_type.in_(_component_types(component)))
        .where(Offer.exclusion_reason != ExclusionReason.NONE.value)
    ).all()

    counts: dict[tuple[str, str | None], int] = {}
    for reason, detail, _ in rows:
        key = (reason, detail if reason == ExclusionReason.PROFILE_FILTER.value else None)
        counts[key] = counts.get(key, 0) + 1

    switches: list[dict[str, Any]] = []
    for (reason, detail), count in sorted(counts.items(), key=lambda item: -item[1]):
        if reason == ExclusionReason.PROFILE_FILTER.value:
            switches.append(
                {
                    "kind": "filter_reason",
                    "code": detail,
                    "title": FILTER_REASON_TITLES.get(detail or "", detail or ""),
                    "offers": count,
                }
            )
        else:
            switches.append(
                {
                    "kind": "exclusion_reason",
                    "code": reason,
                    "title": EXCLUSION_TITLES.get(reason, reason),
                    "offers": count,
                }
            )
    return switches
