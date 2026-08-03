"""Версии компонентов платформы.

Каждый ``ScenarioRun`` сохраняет версии, при которых он был получен, поэтому
изменение любой из констант ниже обязано сопровождаться записью в
``docs/CALCULATION_METHODOLOGY.md`` (раздел «Версионирование»).

Правила инкремента:

* ``ENGINE_VERSION`` — меняется при изменении логики агрегации, статусов,
  Quality Score, Scenario Confidence;
* ``NORMALIZATION_VERSION`` — меняется при изменении маппинга полей источников
  в нормализованную модель (в т.ч. классификации багажа/питания/отмены);
* ``SCHEMA_VERSION`` — версия нормализованного контракта данных
  (``docs/DATA_CONTRACT.md``);
* ``CONFIDENCE_FORMULA_VERSION`` — версия формулы Source Confidence.
"""

from __future__ import annotations

APP_NAME = "Travel Cost Observatory"
APP_VERSION = "1.0.0"

API_VERSION = "v1"

ENGINE_VERSION = "1.0.0"
NORMALIZATION_VERSION = "1.0.0"
SCHEMA_VERSION = "1.0.0"
CONFIDENCE_FORMULA_VERSION = "1.0.0"

#: Официальное название рассчитываемого показателя. Используется в UI и экспорте.
METRIC_TITLE_RU = "Расчетная типовая стоимость путешествия"

#: Обязательная оговорка, сопровождающая любой вывод стоимости.
METRIC_DISCLAIMER_RU = (
    "Показатель является расчетной оценкой на основе доступных рыночных предложений. "
    "Это не оферта, не средний чек и не фактическая стоимость поездки. "
    "Расходы на развлечения, питание вне объекта размещения, трансферы и локальные "
    "траты не учитываются."
)


def version_payload() -> dict[str, str]:
    """Полный набор версий — отдается ``GET /api/v1/version`` и пишется в run."""
    return {
        "app": APP_VERSION,
        "api": API_VERSION,
        "engine": ENGINE_VERSION,
        "normalization": NORMALIZATION_VERSION,
        "schema": SCHEMA_VERSION,
        "confidence_formula": CONFIDENCE_FORMULA_VERSION,
    }
