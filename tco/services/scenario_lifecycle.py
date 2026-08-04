"""Удаление сценария: мягкое и с уничтожением накопленных данных.

Два режима принципиально разные и не должны путаться.

**Мягкое удаление** скрывает сценарий из каталога и снимает его с наблюдения.
Все накопленные снимки рынка и расчеты остаются: они относятся к прошлому и
их достоверность не зависит от того, наблюдаем ли мы этот маршрут дальше.
Это режим по умолчанию.

**Полное удаление** уничтожает историю наблюдений. Восстановить ее неоткуда:
источники не отдают цены задним числом, поэтому удаленный снимок рынка
утрачивается навсегда. Режим существует только для явного администраторского
решения (ошибочно заведенный сценарий, тестовые данные) и обязан
сопровождаться подсчетом того, что именно будет уничтожено.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from tco.core.logging import get_logger
from tco.core.utils import utcnow
from tco.db.models.job import Job
from tco.db.models.offer import Offer
from tco.db.models.raw import HtmlSnapshot, RawResponse
from tco.db.models.reference import ResultCacheEntry
from tco.db.models.run import ScenarioRun
from tco.db.models.scenario import TravelScenario
from tco.db.models.snapshot import MarketSnapshot
from tco.db.models.source import SourceMetric
from tco.storage.raw_store import RawStore, get_raw_store

logger = get_logger(__name__)


def scenario_footprint(session: Session, scenario: TravelScenario) -> dict[str, int]:
    """Что накоплено по сценарию.

    Показывается администратору до удаления: решение об уничтожении
    невосполнимых данных принимается с числами перед глазами, а не вслепую.
    """
    snapshot_ids = select(MarketSnapshot.id).where(MarketSnapshot.scenario_id == scenario.id)

    def count(stmt: Any) -> int:
        return int(session.scalar(select(func.count()).select_from(stmt.subquery())) or 0)

    return {
        "snapshots": count(snapshot_ids),
        "runs": count(select(ScenarioRun.id).where(ScenarioRun.scenario_id == scenario.id)),
        "offers": count(select(Offer.id).where(Offer.market_snapshot_id.in_(snapshot_ids))),
        "raw_responses": count(
            select(RawResponse.id).where(RawResponse.scenario_id == scenario.id)
        ),
        "html_snapshots": count(
            select(HtmlSnapshot.id).where(HtmlSnapshot.scenario_id == scenario.id)
        ),
    }


def soft_delete_scenario(scenario: TravelScenario) -> None:
    """Снимает сценарий с наблюдения, не трогая накопленное."""
    scenario.deleted_at = utcnow()
    scenario.is_active = False
    scenario.updated_at = scenario.deleted_at


def purge_scenario(
    session: Session, scenario: TravelScenario, *, raw_store: RawStore | None = None
) -> dict[str, int]:
    """Удаляет сценарий вместе со всеми накопленными по нему данными.

    Порядок продиктован внешними ключами: сначала то, что ссылается на снимки,
    затем сами снимки, и только потом сценарий. Предложения и итоги по
    источникам удаляются каскадом снимка.

    Задачи и записи аудита сохраняются: это операционный журнал, он должен
    пережить удаление объекта, иначе теряется след самого удаления. У задач
    только обнуляется ссылка на исчезнувший сценарий.
    """
    raw_store = raw_store or get_raw_store()
    stats = scenario_footprint(session, scenario)
    stats["storage_errors"] = 0

    # 1. Тела ответов в файловом хранилище. Ошибка удаления файла не должна
    #    останавливать операцию — иначе запись останется в БД без возможности
    #    повторить, — но обязана быть посчитана.
    refs = session.scalars(
        select(RawResponse.storage_ref).where(RawResponse.scenario_id == scenario.id)
    ).all()
    html_refs = session.scalars(
        select(HtmlSnapshot.storage_ref).where(HtmlSnapshot.scenario_id == scenario.id)
    ).all()
    screenshot_refs = session.scalars(
        select(HtmlSnapshot.screenshot_ref)
        .where(HtmlSnapshot.scenario_id == scenario.id)
        .where(HtmlSnapshot.screenshot_ref.is_not(None))
    ).all()
    for ref in (*refs, *html_refs, *screenshot_refs):
        if ref and not raw_store.delete(ref):
            stats["storage_errors"] += 1

    # 2. Записи артефактов, предложения и расчеты. Предложения удаляются по
    #    самому сценарию, а не только каскадом снимка: у предложения есть
    #    собственная ссылка на сценарий, и она переживет удаление снимка.
    session.execute(delete(HtmlSnapshot).where(HtmlSnapshot.scenario_id == scenario.id))
    session.execute(delete(RawResponse).where(RawResponse.scenario_id == scenario.id))
    session.execute(delete(Offer).where(Offer.scenario_id == scenario.id))
    session.execute(delete(ScenarioRun).where(ScenarioRun.scenario_id == scenario.id))

    # 3. Снимки рынка: оставшиеся предложения и итоги по источникам уходят
    #    каскадом внешнего ключа.
    session.execute(delete(MarketSnapshot).where(MarketSnapshot.scenario_id == scenario.id))

    # 4. Кэш результатов привязан к отпечатку сценария, а не к его id.
    session.execute(
        delete(ResultCacheEntry).where(
            ResultCacheEntry.scenario_fingerprint == scenario.fingerprint
        )
    )

    # 5. Журнал задач и технические метрики источников переживают удаление —
    #    обнуляется только ссылка. Метрики описывают поведение источника, а не
    #    маршрут: их удаление исказило бы историю Source Confidence, к которой
    #    сценарий отношения не имеет.
    session.execute(update(Job).where(Job.scenario_id == scenario.id).values(scenario_id=None))
    session.execute(
        update(SourceMetric).where(SourceMetric.scenario_id == scenario.id).values(scenario_id=None)
    )

    session.delete(scenario)
    session.flush()

    logger.info("Сценарий удален вместе с данными", code=scenario.code, **stats)
    return stats
