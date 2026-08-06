"""Поведение при неполном контракте источника.

Источник может не отдавать признак в принципе (Туту не возвращает план
питания в выдаче отелей) и может успешно собрать один тип предложений,
провалив другой. Оба случая не должны обнулять пригодные данные — но и не
должны выдавать неизвестное за подтвержденное.
"""

from __future__ import annotations

from datetime import timedelta

from tco.core.enums import (
    CancellationType,
    ConnectorOutcome,
    MealType,
    OfferAttribute,
    OfferType,
    ValidityStatus,
)
from tco.core.utils import utcnow
from tco.db.models.offer import AccommodationOffer, Offer
from tco.engine.aggregation import SourceCollectionInfo, check_eligibility
from tco.engine.selection import unclassified_attributes
from tco.schemas.profile import ProfileRules


def _hotel_offer(*, meal: MealType, cancellation: CancellationType = CancellationType.FREE_CANCELLATION) -> Offer:
    """Предложение проживания с заданной классификацией признаков."""
    offer = Offer(
        offer_type=OfferType.ACCOMMODATION.value,
        validity_status=ValidityStatus.VALID.value,
        classification_status=(
            "CLASSIFIED" if meal is not MealType.UNKNOWN else "UNCLASSIFIED_MEAL"
        ),
    )
    offer.accommodation = AccommodationOffer(
        meal_type=meal.value,
        cancellation_type=cancellation.value,
        capacity_confirmed=True,
    )
    return offer


def _info(**overrides) -> SourceCollectionInfo:
    defaults = dict(
        source_code="tutu_mcp",
        source_name="Туту.ру (MCP)",
        outcome=ConnectorOutcome.SUCCESS,
        collected_at=utcnow(),
    )
    defaults.update(overrides)
    return SourceCollectionInfo(**defaults)


class TestUnreportedAttributes:
    """Признак, которого нет в контракте, — не то же самое, что сбой разбора."""

    def test_lists_only_actually_unknown_attributes(self):
        known = _hotel_offer(meal=MealType.BREAKFAST)
        unknown = _hotel_offer(meal=MealType.UNKNOWN)

        assert unclassified_attributes(known) == frozenset()
        assert unclassified_attributes(unknown) == frozenset({OfferAttribute.MEAL.value})

    def test_unreported_attribute_disqualifies_by_default(self):
        """Профиль 1.0.0: поведение прежнее, источник снимается с расчета."""
        rules = ProfileRules.parse({})
        offers = [_hotel_offer(meal=MealType.UNKNOWN) for _ in range(20)]

        eligible, reasons, _ = check_eligibility(
            _info(unreported_attributes=frozenset({OfferAttribute.MEAL.value})),
            offers,
            offers,
            rules,
        )

        assert eligible is False
        assert any("неклассифицированных" in reason for reason in reasons)

    def test_declared_gap_does_not_disqualify_when_rule_disabled(self):
        rules = ProfileRules.parse({"eligibility": {"count_unreported_as_unclassified": False}})
        offers = [_hotel_offer(meal=MealType.UNKNOWN) for _ in range(20)]

        eligible, reasons, _ = check_eligibility(
            _info(unreported_attributes=frozenset({OfferAttribute.MEAL.value})),
            offers,
            offers,
            rules,
        )

        assert eligible is True, reasons

    def test_undeclared_gap_still_disqualifies(self):
        """Послабление точечное: оно распространяется только на объявленное."""
        rules = ProfileRules.parse({"eligibility": {"count_unreported_as_unclassified": False}})
        offers = [
            _hotel_offer(meal=MealType.BREAKFAST, cancellation=CancellationType.UNKNOWN)
            for _ in range(20)
        ]
        for offer in offers:
            offer.classification_status = "UNCLASSIFIED_CANCELLATION"

        eligible, reasons, _ = check_eligibility(
            _info(unreported_attributes=frozenset({OfferAttribute.MEAL.value})),
            offers,
            offers,
            rules,
        )

        assert eligible is False
        assert any("неклассифицированных" in reason for reason in reasons)


class TestPerOfferTypeOutcome:
    """Неудача одного типа предложений не снимает с допуска другой компонент."""

    @staticmethod
    def _mixed_source() -> SourceCollectionInfo:
        collected = utcnow()
        merged = _info(outcome=ConnectorOutcome.TRANSPORT_ERROR, collected_at=collected)
        merged.by_offer_type = {
            OfferType.FLIGHT.value: _info(
                outcome=ConnectorOutcome.SUCCESS, collected_at=collected
            ),
            OfferType.ACCOMMODATION.value: _info(
                outcome=ConnectorOutcome.TRANSPORT_ERROR,
                collected_at=collected,
                error_code="CONNECTOR_ERROR",
            ),
        }
        return merged

    def test_successful_type_keeps_its_outcome(self):
        scoped = self._mixed_source().scoped_to({OfferType.FLIGHT.value})

        assert scoped.outcome is ConnectorOutcome.SUCCESS
        assert scoped.error_code is None

    def test_failed_type_keeps_its_failure(self):
        scoped = self._mixed_source().scoped_to({OfferType.ACCOMMODATION.value})

        assert scoped.outcome is ConnectorOutcome.TRANSPORT_ERROR
        assert scoped.error_code == "CONNECTOR_ERROR"

    def test_transport_stays_eligible_when_accommodation_fails(self):
        rules = ProfileRules.parse({})
        offers = [
            Offer(
                offer_type=OfferType.FLIGHT.value,
                validity_status=ValidityStatus.VALID.value,
                classification_status="CLASSIFIED",
            )
            for _ in range(20)
        ]

        scoped = self._mixed_source().scoped_to({OfferType.FLIGHT.value, OfferType.RAIL.value})
        eligible, reasons, _ = check_eligibility(scoped, offers, offers, rules)

        assert eligible is True, reasons

    def test_age_is_measured_by_oldest_part(self):
        now = utcnow()
        merged = _info(collected_at=now)
        merged.by_offer_type = {
            OfferType.FLIGHT.value: _info(collected_at=now - timedelta(minutes=40)),
            OfferType.RAIL.value: _info(collected_at=now),
        }

        scoped = merged.scoped_to({OfferType.FLIGHT.value, OfferType.RAIL.value})

        assert scoped.collected_at == now - timedelta(minutes=40)


class TestToolArgumentTypes:
    """Аргументы инструмента приводятся к типу, объявленному его схемой."""

    def test_scalar_is_wrapped_into_declared_array(self):
        from tco.connectors.tutu_mcp import _coerce

        prop = {"anyOf": [{"items": {"type": "integer"}, "type": "array"}, {"type": "null"}]}

        assert _coerce("4", "array", prop) == [4]

    def test_existing_list_is_coerced_elementwise(self):
        from tco.connectors.tutu_mcp import _coerce

        prop = {"items": {"type": "integer"}, "type": "array"}

        assert _coerce(["3", 4], "array", prop) == [3, 4]


class TestMealConfirmedByQuery:
    """Серверный фильтр по питанию подтверждает план так же, как поиск по составу гостей."""

    def test_requested_plan_is_confirmed_when_source_is_silent(self):
        from tco.connectors.tutu_mcp import _meal_text

        assert _meal_text({"meal_name": None}, "NO_MEALS") == "NO_MEALS"
        assert _meal_text({"meal_name": None}, "BREAKFAST") == "BREAKFAST"

    def test_without_request_meal_stays_unknown(self):
        from tco.connectors.tutu_mcp import _meal_text
        from tco.normalization.classify import classify_meal

        assert _meal_text({"meal_name": None}, "ANY") is None
        assert classify_meal(_meal_text({"meal_name": None}, "ANY")) is MealType.UNKNOWN

    def test_explicit_source_value_wins_over_request(self):
        from tco.connectors.tutu_mcp import _meal_text

        assert _meal_text({"breakfast_included": False}, "BREAKFAST") == "NO_MEALS"


class TestCircuitOpenLeavesATrace:
    """Пропуск по размыкателю цепи должен быть виден в данных, а не только в логе.

    Пропуск означает «мы не спросили», а не «источник ответил пусто». Без записи
    об этом расчет выходит NO_DATA, неотличимым от честного отсутствия
    предложений: на стенде так пропало проживание по 395 сценариям из 465 —
    767 строк в логе и ни следа в данных.
    """

    @staticmethod
    def selection(session, *, open_breaker: bool):
        from datetime import date, timedelta

        from sqlalchemy import select as sa_select

        from tco.core.enums import OfferType
        from tco.core.utils import utcnow
        from tco.db.models.source import Source
        from tco.services.snapshot_builder import eligible_sources

        source = session.scalars(
            sa_select(Source).where(Source.code == "tutu_mcp")
        ).first()
        assert source is not None, "нужен источник tutu_mcp из bootstrap"
        source.circuit_open_until = utcnow() + timedelta(hours=1) if open_breaker else None
        session.flush()

        departure = date.today() + timedelta(days=20)
        return eligible_sources(
            session,
            offer_types=(OfferType.ACCOMMODATION,),
            departure_date=departure,
            return_date=departure + timedelta(days=5),
            allow_synthetic=False,
        )

    def test_open_breaker_moves_source_to_skipped(self, session):
        selection = self.selection(session, open_breaker=True)

        skipped = {source.code for source, _ in selection.circuit_open}
        chosen = {source.code for source, _ in selection.pairs}

        assert "tutu_mcp" in skipped, "пропуск обязан быть отражен, а не проглочен"
        assert "tutu_mcp" not in chosen

    def test_closed_breaker_keeps_source_in_collection(self, session):
        selection = self.selection(session, open_breaker=False)

        chosen = {source.code for source, _ in selection.pairs}

        assert "tutu_mcp" in chosen
        assert not selection.circuit_open
