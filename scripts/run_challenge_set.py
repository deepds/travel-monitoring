"""Прогон challenge set и формирование отчета (SCOPE-R E §2).

Для каждого контрольного сценария выполняется полный расчет, после чего
фактический результат сопоставляется с ожидаемым поведением, закодированным
в тегах каталога.

Проверки делятся на два вида:

* **жесткие** — нарушение означает дефект (например, сценарий, помеченный
  ``unsupported``, обязан завершиться без обращения к внешним источникам);
* **наблюдательные** — фиксируют факт, но не считаются провалом, потому что
  зависят от состояния рынка (например, ``single-source`` или ``no-data``:
  наличие данных у источника в конкретный день не гарантировано).

Контрольный сценарий может совпасть по отпечатку с сценарием мониторинга —
это штатная ситуация: одинаковый набор параметров есть один и тот же
``TravelScenario``. Скрипт разрешает сценарии по отпечатку (find-or-create),
поэтому совпадение не искажает отчет.

Использование::

    python scripts/run_challenge_set.py --profile sandbox     # офлайн-прогон
    python scripts/run_challenge_set.py --profile baseline    # реальные источники
    python scripts/run_challenge_set.py --limit 5 --dry-run   # только валидация
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import select  # noqa: E402

from tco.core.enums import ComponentStatus, RunStatus, RunType  # noqa: E402
from tco.core.utils import utcnow  # noqa: E402
from tco.db.models.profile import CalculationProfile  # noqa: E402
from tco.db.session import session_scope  # noqa: E402
from tco.services.calculation import active_profile, calculate_scenario, validate  # noqa: E402
from tco.services.scenarios import create_scenario, draft_from_row  # noqa: E402

CHALLENGE_CSV = REPO_ROOT / "catalog" / "challenge_set.csv"
REPORT_MD = REPO_ROOT / "docs" / "CHALLENGE_SET_RESULTS.md"
REPORT_JSON = REPO_ROOT / "var" / "challenge_set_results.json"

#: Теги, задающие жесткое ожидание.
HARD_EXPECTATIONS = {"unsupported"}
#: Теги, задающие наблюдательное ожидание.
SOFT_EXPECTATIONS = {"no-data", "partial", "single-source", "disagreement", "small-sample"}


@dataclass(slots=True)
class CaseResult:
    case: str
    scenario_code: str
    route: str
    departure: str
    transport: str
    accommodation: str
    tags: list[str]
    expectation: str

    validated: bool = True
    validation_errors: list[str] = field(default_factory=list)
    status: str = "NOT_RUN"
    component_statuses: list[str] = field(default_factory=list)
    quality_score: float | None = None
    confidence_level: str | None = None
    total_estimated_cost: float | None = None
    transport_median: float | None = None
    hotel_median: float | None = None
    transport_sources: int = 0
    hotel_sources: int = 0
    transport_disagreement: float | None = None
    hotel_disagreement: float | None = None
    valid_offers: int = 0
    excluded_offers: int = 0
    outliers: int = 0
    sources: list[str] = field(default_factory=list)
    duration_ms: int = 0
    from_cache: bool = False
    verdict: str = "PASS"
    verdict_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {key: getattr(self, key) for key in self.__slots__}


def _evaluate(result: CaseResult) -> None:
    """Сопоставляет факт с ожиданием, закодированным в тегах."""
    tags = set(result.tags)

    # --- Жесткие проверки ------------------------------------------------- #
    if tags & HARD_EXPECTATIONS:
        if result.validated:
            result.verdict = "FAIL"
            result.verdict_reason = (
                "Сценарий должен был быть отклонен валидацией до внешних запросов, "
                f"но прошел ее и завершился со статусом {result.status}"
            )
        else:
            result.verdict = "PASS"
            result.verdict_reason = (
                "Отклонен валидацией без обращения к источникам: "
                + "; ".join(result.validation_errors)
            )
        return

    if not result.validated:
        result.verdict = "FAIL"
        result.verdict_reason = (
            "Сценарий неожиданно не прошел валидацию: " + "; ".join(result.validation_errors)
        )
        return

    if result.status == RunStatus.FAILED.value:
        result.verdict = "FAIL"
        result.verdict_reason = "Сбор данных завершился ошибкой по всем источникам"
        return

    # Итог обязан отсутствовать, если отсутствует компонент — и наоборот.
    missing = {
        ComponentStatus.PARTIAL_TRANSPORT_MISSING.value,
        ComponentStatus.PARTIAL_HOTEL_MISSING.value,
    } & set(result.component_statuses)
    if missing and result.total_estimated_cost is not None:
        result.verdict = "FAIL"
        result.verdict_reason = (
            "Итоговая стоимость рассчитана при отсутствующем компоненте: " + ", ".join(sorted(missing))
        )
        return
    if not missing and result.status == RunStatus.SUCCESS.value and result.total_estimated_cost is None:
        result.verdict = "FAIL"
        result.verdict_reason = "Статус SUCCESS без итоговой стоимости"
        return

    # --- Наблюдательные проверки -------------------------------------------- #
    notes: list[str] = []
    if result.status in (RunStatus.NO_DATA.value, RunStatus.PARTIAL_SUCCESS.value):
        expected = bool(tags & {"no-data", "partial", "small-sample"})
        notes.append(
            f"Статус {result.status}" + (" (ожидаемо для этого случая)" if expected else "")
        )
        if not expected:
            result.verdict = "ATTENTION"
    if ComponentStatus.COMPLETE_SINGLE_SOURCE.value in result.component_statuses:
        expected = bool(tags & {"single-source", "small-sample"})
        notes.append("Компонент рассчитан по одному источнику")
        if not expected and result.verdict == "PASS":
            result.verdict = "ATTENTION"
    for label, value in (
        ("транспорт", result.transport_disagreement),
        ("проживание", result.hotel_disagreement),
    ):
        if value is not None and value > 0.30:
            notes.append(f"Высокое расхождение источников ({label}): {value:.0%}")
            if result.verdict == "PASS":
                result.verdict = "ATTENTION"

    result.verdict_reason = "; ".join(notes) or "Результат соответствует ожиданиям"


def _case_id(tags: list[str], fallback: str) -> str:
    for tag in tags:
        if tag.startswith("CS") and tag[2:].isdigit():
            return tag
    return fallback


def run(profile_code: str | None, limit: int | None, dry_run: bool, force: bool) -> int:
    rows = list(csv.DictReader(io.StringIO(CHALLENGE_CSV.read_text(encoding="utf-8"))))
    if limit:
        rows = rows[:limit]

    results: list[CaseResult] = []
    started = utcnow()

    for index, row in enumerate(rows, start=1):
        tags = [tag for tag in (row.get("tags") or "").split(";") if tag]
        case = _case_id(tags, f"ROW{index:02d}")
        notes = row.get("notes") or ""
        expectation = notes.split("] ", 1)[-1] if "] " in notes else notes

        with session_scope() as session:
            profile = (
                session.scalars(
                    select(CalculationProfile)
                    .where(CalculationProfile.code == profile_code)
                    .order_by(CalculationProfile.version_seq.desc())
                ).first()
                if profile_code
                else None
            )
            if profile_code and profile is None:
                print(f"Профиль {profile_code} не найден", file=sys.stderr)
                return 2

            draft = draft_from_row(row, source_file="challenge_set.csv")
            scenario, _created = create_scenario(session, draft, profile=profile)
            profile = profile or active_profile(session, scenario)

            result = CaseResult(
                case=case,
                scenario_code=scenario.code,
                route=f"{scenario.origin_city.name} → {scenario.destination_city.name}",
                departure=scenario.departure_date.isoformat(),
                transport=scenario.transport_type,
                accommodation=f"{scenario.accommodation_type}/{scenario.stars}",
                tags=tags,
                expectation=expectation,
            )

            validation = validate(session, scenario, profile)
            result.validated = validation.is_valid
            result.validation_errors = [
                f"{issue.code}: {issue.message}" for issue in validation.errors
            ]

            if not validation.is_valid or dry_run:
                if dry_run and validation.is_valid:
                    result.status = "DRY_RUN"
                _evaluate(result)
                results.append(result)
                print(f"[{index:2}/{len(rows)}] {case} {result.verdict:9} {result.route}")
                continue

            call_started = time.perf_counter()
            outcome = calculate_scenario(
                session,
                scenario,
                profile=profile,
                run_type=RunType.MANUAL,
                force_refresh=force,
                created_by="challenge-set",
            )
            result.duration_ms = int((time.perf_counter() - call_started) * 1000)
            result.from_cache = outcome.from_cache

            run_obj = outcome.run
            if run_obj is not None:
                result.status = run_obj.status
                result.component_statuses = list(run_obj.component_statuses or [])
                result.quality_score = run_obj.quality_score
                result.confidence_level = run_obj.confidence_level
                result.total_estimated_cost = (
                    float(run_obj.total_estimated_cost)
                    if run_obj.total_estimated_cost is not None
                    else None
                )
                result.transport_median = (
                    float(run_obj.transport_median) if run_obj.transport_median else None
                )
                result.hotel_median = (
                    float(run_obj.hotel_median) if run_obj.hotel_median else None
                )
                result.transport_sources = run_obj.transport_source_count
                result.hotel_sources = run_obj.hotel_source_count
                result.transport_disagreement = run_obj.transport_disagreement
                result.hotel_disagreement = run_obj.hotel_disagreement
                result.valid_offers = run_obj.valid_offer_count
                result.excluded_offers = run_obj.excluded_offer_count
                result.outliers = run_obj.outlier_offer_count
                result.sources = list(run_obj.source_codes or [])

            _evaluate(result)
            results.append(result)
            print(
                f"[{index:2}/{len(rows)}] {case} {result.verdict:9} {result.status:16} "
                f"Q={result.quality_score or 0:5.1f} {result.route}"
            )

    duration = (utcnow() - started).total_seconds()
    _write_reports(results, profile_code, duration, dry_run)

    failures = [item for item in results if item.verdict == "FAIL"]
    attention = [item for item in results if item.verdict == "ATTENTION"]
    print(
        f"\nИтого: {len(results)} сценариев, "
        f"PASS={len(results) - len(failures) - len(attention)}, "
        f"ATTENTION={len(attention)}, FAIL={len(failures)}, {duration:.1f} с"
    )
    print(f"Отчет: {REPORT_MD.relative_to(REPO_ROOT)}")
    return 1 if failures else 0


def _write_reports(
    results: list[CaseResult], profile_code: str | None, duration: float, dry_run: bool
) -> None:
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(
        json.dumps(
            {
                "generated_at": utcnow().isoformat(),
                "profile": profile_code or "active",
                "dry_run": dry_run,
                "duration_seconds": round(duration, 1),
                "results": [item.as_dict() for item in results],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    passed = [item for item in results if item.verdict == "PASS"]
    attention = [item for item in results if item.verdict == "ATTENTION"]
    failed = [item for item in results if item.verdict == "FAIL"]

    lines: list[str] = [
        "# Результаты challenge set",
        "",
        "> Файл формируется автоматически: `python scripts/run_challenge_set.py`.",
        "> Ручные правки будут перезаписаны при следующем прогоне.",
        "",
        f"- **Дата прогона:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- **Профиль расчета:** `{profile_code or 'активный'}`",
        f"- **Режим:** {'только валидация (dry-run)' if dry_run else 'полный расчет'}",
        f"- **Длительность:** {duration:.1f} с",
        f"- **Итог:** PASS {len(passed)} · ATTENTION {len(attention)} · FAIL {len(failed)}"
        f" из {len(results)}",
        "",
        "## Как читать вердикты",
        "",
        "| Вердикт | Значение |",
        "|---|---|",
        "| `PASS` | Поведение соответствует ожидаемому |",
        "| `ATTENTION` | Отклонение, объяснимое состоянием рынка: требует взгляда аналитика, "
        "но не является дефектом |",
        "| `FAIL` | Нарушение обязательного правила методики — дефект |",
        "",
        "## Сводная таблица",
        "",
        "| Кейс | Вердикт | Статус | Маршрут | Дата | Транспорт | Q-Score | Уверенность |"
        " Источники Т/П | Итого, ₽ |",
        "|---|---|---|---|---|---|---:|---|---|---:|",
    ]

    for item in results:
        total = f"{item.total_estimated_cost:,.0f}".replace(",", " ") if item.total_estimated_cost else "—"
        quality = f"{item.quality_score:.1f}" if item.quality_score is not None else "—"
        lines.append(
            f"| {item.case} | `{item.verdict}` | `{item.status}` | {item.route} | "
            f"{item.departure} | {item.transport} | {quality} | "
            f"{item.confidence_level or '—'} | {item.transport_sources}/{item.hotel_sources} | {total} |"
        )

    lines += ["", "## Детализация по кейсам", ""]
    for item in results:
        lines += [
            f"### {item.case} — {item.route} ({item.transport}, {item.accommodation})",
            "",
            f"- **Ожидание:** {item.expectation}",
            f"- **Вердикт:** `{item.verdict}` — {item.verdict_reason}",
            f"- **Статус расчета:** `{item.status}`"
            + (
                f", компонентные статусы: {', '.join(item.component_statuses)}"
                if item.component_statuses
                else ""
            ),
        ]
        if item.validation_errors:
            lines.append(f"- **Ошибки валидации:** {'; '.join(item.validation_errors)}")
        if item.status not in ("NOT_RUN", "DRY_RUN") and item.validated:
            lines += [
                f"- **Компоненты:** транспорт {_fmt(item.transport_median)} "
                f"({item.transport_sources} ист.), проживание {_fmt(item.hotel_median)} "
                f"({item.hotel_sources} ист.)",
                f"- **Расхождение источников:** транспорт {_pct(item.transport_disagreement)}, "
                f"проживание {_pct(item.hotel_disagreement)}",
                f"- **Предложения:** учтено {item.valid_offers}, исключено {item.excluded_offers}, "
                f"выбросов {item.outliers}",
                f"- **Источники:** {', '.join(item.sources) or '—'}",
                f"- **Quality Score:** {_fmt_score(item.quality_score)}, "
                f"уверенность: {item.confidence_level or '—'}",
                f"- **Время расчета:** {item.duration_ms} мс"
                + (" (из кэша)" if item.from_cache else ""),
            ]
        lines.append("")

    REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")


def _fmt(value: float | None) -> str:
    return f"{value:,.0f} ₽".replace(",", " ") if value else "—"


def _pct(value: float | None) -> str:
    return f"{value:.1%}" if value is not None else "—"


def _fmt_score(value: float | None) -> str:
    return f"{value:.1f}" if value is not None else "—"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        default=None,
        help="Код профиля расчета (например, sandbox или baseline). "
        "По умолчанию — активный профиль.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Ограничить число кейсов")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только валидация сценариев, без обращения к источникам",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Игнорировать кэш и собрать данные заново",
    )
    args = parser.parse_args()
    return run(args.profile, args.limit, args.dry_run, args.force)


if __name__ == "__main__":
    raise SystemExit(main())
