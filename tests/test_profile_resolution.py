"""Выбор методики расчета.

Активных методик в системе несколько: базовая для каталога направлений и своя
у витрины. Отсюда два свойства, которые нельзя терять: закрепленный за
сценарием профиль должен применяться, а методика по умолчанию — не зависеть от
того, какую активировали последней.

Оба однажды нарушались молча: сетка витрины считалась базовой методикой, и
правила по классу вагона и возвратности просто не применялись, а в расчете
стоял чужой profile_code.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from tco.core.enums import ProfileStatus
from tco.core.utils import utcnow
from tco.db.models.profile import CalculationProfile
from tco.db.models.scenario import TravelScenario
from tco.services.calculation import active_profile


def profile_by_code(session, code: str) -> CalculationProfile | None:
    return session.scalars(
        select(CalculationProfile)
        .where(CalculationProfile.code == code)
        .order_by(CalculationProfile.version_seq.desc())
    ).first()


@pytest.fixture()
def showcase(session) -> CalculationProfile:
    profile = profile_by_code(session, "mass-market")
    if profile is None:
        pytest.skip("Профиль mass-market не засеян в тестовой базе")
    return profile


@pytest.fixture()
def scenario(session) -> TravelScenario:
    item = session.scalars(
        select(TravelScenario).where(TravelScenario.deleted_at.is_(None))
    ).first()
    if item is None:
        pytest.skip("В тестовой базе нет сценариев")
    return item


class TestDefaultProfile:
    def test_default_is_baseline(self, session):
        assert active_profile(session).code == "baseline"

    def test_new_active_profile_does_not_take_over(self, session, showcase):
        """Витрина активна, но методикой по умолчанию не становится."""
        showcase.status = ProfileStatus.ACTIVE.value
        showcase.activated_at = utcnow()
        session.flush()

        assert active_profile(session).code == "baseline"


class TestPinnedProfile:
    def test_pinned_active_profile_wins(self, session, scenario, showcase):
        showcase.status = ProfileStatus.ACTIVE.value
        showcase.activated_at = utcnow()
        scenario.calculation_profile_id = showcase.id
        session.flush()

        assert active_profile(session, scenario).code == "mass-market"

    def test_pinned_draft_falls_back_to_default(self, session, scenario, showcase):
        """Недействующая методика не применяется — но и не остается незамеченной."""
        showcase.status = ProfileStatus.DRAFT.value
        scenario.calculation_profile_id = showcase.id
        session.flush()

        assert active_profile(session, scenario).code == "baseline"

    def test_scenario_without_pin_uses_default(self, session, scenario):
        scenario.calculation_profile_id = None
        session.flush()

        assert active_profile(session, scenario).code == "baseline"
