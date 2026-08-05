"""Схема правил Calculation Profile.

Профиль — единственное место, где живут числовые пороги методики. Движок не
содержит «зашитых» констант: любое значение ниже конфигурируемо и
версионируется вместе с профилем.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tco.core.enums import ProfileStatus


class OutlierRules(BaseModel):
    """Правила маркировки выбросов (SCOPE-R P §7)."""

    model_config = ConfigDict(extra="forbid")

    method: Literal["IQR", "NONE"] = "IQR"
    iqr_multiplier: float = Field(1.5, ge=0.0, le=10.0)
    #: Минимальное число предложений, при котором вообще имеет смысл искать выбросы.
    min_sample_size: int = Field(4, ge=3, le=100)
    #: PER_SOURCE — IQR считается внутри каждого источника (по умолчанию, так как
    #: агрегация тоже начинается с источника); GLOBAL — по объединенной выборке.
    scope: Literal["PER_SOURCE", "GLOBAL"] = "PER_SOURCE"
    #: Предохранитель: если правило отбраковывает больше этой доли выборки,
    #: маркировка выбросов для этой выборки отменяется целиком.
    max_outlier_ratio: float = Field(0.4, ge=0.0, le=1.0)


class SourceEligibilityRules(BaseModel):
    """Допуск источника к участию в расчете компонента (SCOPE-R P §8)."""

    model_config = ConfigDict(extra="forbid")

    min_valid_offers: int = Field(5, ge=1, le=1000)
    max_data_age_minutes: int = Field(60, ge=1, le=100000)
    max_invalid_offer_ratio: float = Field(0.30, ge=0.0, le=1.0)
    max_unclassified_fare_ratio: float = Field(0.40, ge=0.0, le=1.0)
    #: Засчитывать ли в долю неклассифицированных те признаки, которых нет в
    #: контракте источника (``Source.unreported_attributes``). ``False`` не
    #: смягчает фильтр сценария — предложение с неизвестным признаком
    #: по-прежнему не выдается за соответствующее запросу; снимается только
    #: наказание источника за то, чего он в принципе не сообщает.
    count_unreported_as_unclassified: bool = True
    #: Минимум предложений, оставшихся после дедупликации и фильтрации.
    min_offers_after_dedup: int = Field(3, ge=1, le=1000)
    #: То же для ЖД. Порог ниже, потому что предложений там мало по природе
    #: рынка: на направлении ходит два-три поезда, а одинаковые по цене
    #: вагонные группы схлопываются как дубликаты. С общим порогом в три
    #: перевозчик выпадал из расчета целиком, и плацкартная медиана строилась
    #: на одном агенте — то есть на цене с наценкой.
    min_offers_after_dedup_rail: int = Field(2, ge=1, le=1000)
    #: Требовать успешного технического завершения запроса.
    require_successful_call: bool = True
    #: Минимальный Source Confidence (0-100). ``None`` — не проверять.
    min_source_confidence: float | None = Field(None, ge=0.0, le=100.0)


class AggregationRules(BaseModel):
    """Правила агрегации внутри и между источниками (SCOPE-R P §9)."""

    model_config = ConfigDict(extra="forbid")

    #: Основной показатель внутри источника.
    per_source_statistic: Literal["MEDIAN", "P25", "TRIMMED_MEAN", "WINSORIZED_MEAN"] = "MEDIAN"
    trim_ratio: float = Field(0.1, ge=0.0, le=0.45)
    #: Как объединять медианы источников при 3+ источниках.
    cross_source_method: Literal["MEDIAN_OF_MEDIANS", "MEAN_OF_MEDIANS"] = "MEDIAN_OF_MEDIANS"
    #: При двух источниках методика предписывает среднее двух медиан.
    two_source_method: Literal["MEAN", "MEDIAN"] = "MEAN"
    #: Метод расчета перцентилей: линейная интерполяция (numpy 'linear').
    percentile_method: Literal["LINEAR", "NEAREST_RANK"] = "LINEAR"
    #: Порог, выше которого межисточниковое расхождение считается существенным.
    high_disagreement_threshold: float = Field(0.30, ge=0.0, le=5.0)
    allow_single_source: bool = True


class FilterRules(BaseModel):
    """Правила фильтрации предложений по профилю."""

    model_config = ConfigDict(extra="forbid")

    #: Отбраковывать предложения в валюте, отличной от базовой (конвертации нет).
    require_base_currency: bool = True
    min_price: float = Field(1.0, ge=0.0)
    max_price: float | None = Field(None, ge=0.0)
    #: Предложение без подтвержденной вместимости не включается в расчет.
    require_capacity_confirmation: bool = True
    #: Неклассифицированный багаж не попадает в CABIN_BAGGAGE / CHECKED_BAGGAGE.
    strict_baggage_classification: bool = True
    #: Требовать точного совпадения звездности; иначе допускается «не ниже».
    stars_exact_match: bool = True
    #: Питание: предложение проходит, если оно не хуже запрошенного.
    meal_at_least_requested: bool = True
    #: Максимальное число пересадок в одном плече (``None`` — без ограничения).
    max_stops: int | None = Field(None, ge=0, le=5)
    #: Учитывать только предложения с бесплатной отменой, если так задано в сценарии.
    enforce_cancellation_filter: bool = True
    #: Допускать синтетические (песочница) источники в расчет.
    allow_synthetic_sources: bool = False


class QualityWeights(BaseModel):
    """Веса Quality Score (SCOPE-R P §12). Сумма нормализуется к 1."""

    model_config = ConfigDict(extra="forbid")

    component_completeness: float = Field(0.30, ge=0.0, le=1.0)
    source_count: float = Field(0.20, ge=0.0, le=1.0)
    offer_count: float = Field(0.15, ge=0.0, le=1.0)
    source_agreement: float = Field(0.15, ge=0.0, le=1.0)
    freshness: float = Field(0.10, ge=0.0, le=1.0)
    connector_stability: float = Field(0.10, ge=0.0, le=1.0)

    def as_dict(self) -> dict[str, float]:
        return self.model_dump()

    def normalized(self) -> dict[str, float]:
        raw = self.as_dict()
        total = sum(raw.values())
        if total <= 0:
            raise ValueError("Сумма весов Quality Score должна быть положительной")
        return {key: value / total for key, value in raw.items()}


class QualityRules(BaseModel):
    """Параметры расчета Quality Score."""

    model_config = ConfigDict(extra="forbid")

    weights: QualityWeights = Field(default_factory=QualityWeights)
    partial_success_threshold: float = Field(60.0, ge=0.0, le=100.0)
    #: Целевое число источников на компонент для полного балла.
    target_source_count: int = Field(2, ge=1, le=10)
    #: Целевое число валидных предложений на компонент для полного балла.
    target_offer_count: int = Field(20, ge=1, le=1000)
    #: Балл согласованности при единственном источнике (расхождение неизвестно).
    single_source_agreement_score: float = Field(0.5, ge=0.0, le=1.0)
    #: Стабильность коннектора, если исторических метрик еще нет.
    default_connector_stability: float = Field(0.8, ge=0.0, le=1.0)
    #: Окно расчета исторической стабильности коннекторов.
    stability_window_days: int = Field(30, ge=1, le=365)


class ConfidenceRules(BaseModel):
    """Пороги Scenario Confidence (DELTA §8.3)."""

    model_config = ConfigDict(extra="forbid")

    high_min_quality: float = Field(80.0, ge=0.0, le=100.0)
    medium_min_quality: float = Field(60.0, ge=0.0, le=100.0)
    low_min_quality: float = Field(40.0, ge=0.0, le=100.0)
    #: Для HIGH требуется столько источников на каждый обязательный компонент.
    high_min_sources_per_component: int = Field(2, ge=1, le=10)
    #: Для HIGH расхождение источников не должно превышать порог.
    high_max_disagreement: float = Field(0.15, ge=0.0, le=5.0)
    #: Минимальный Source Confidence использованных источников для HIGH.
    high_min_source_confidence: float = Field(60.0, ge=0.0, le=100.0)
    #: Доля неклассифицированных предложений, снижающая уверенность.
    max_unclassified_ratio: float = Field(0.40, ge=0.0, le=1.0)


class LimitRules(BaseModel):
    """Временные лимиты и ограничения объема."""

    model_config = ConfigDict(extra="forbid")

    source_soft_timeout_seconds: float = Field(20.0, gt=0, le=300)
    source_hard_timeout_seconds: float = Field(30.0, gt=0, le=600)
    on_demand_total_timeout_seconds: float = Field(60.0, gt=0, le=900)
    max_retries: int = Field(2, ge=0, le=10)
    #: Максимум нормализованных предложений на источник и тип (защита от лавины).
    max_offers_per_source: int = Field(300, ge=1, le=10000)
    result_cache_ttl_minutes: int = Field(45, ge=1, le=1440)


class TransportPricingRules(BaseModel):
    """Правила пересчета транспортных цен в стоимость всех пассажиров."""

    model_config = ConfigDict(extra="forbid")

    #: Коэффициент детского ЖД-тарифа. 1.0 — скидка не моделируется
    #: (честный дефолт; см. LIMITATIONS).
    rail_child_fare_ratio: float = Field(1.0, ge=0.0, le=1.0)
    #: Возраст, до которого применяется детский тариф.
    rail_child_max_age: int = Field(9, ge=0, le=17)
    #: Максимум комбинаций «туда × обратно» при сборке round-trip из one-way.
    max_roundtrip_combinations: int = Field(64, ge=1, le=2000)
    #: Сколько лучших вариантов в каждом направлении участвуют в комбинировании.
    roundtrip_legs_per_direction: int = Field(8, ge=1, le=50)


class ProfileRules(BaseModel):
    """Полный набор правил профиля расчета."""

    model_config = ConfigDict(extra="forbid")

    filters: FilterRules = Field(default_factory=FilterRules)
    eligibility: SourceEligibilityRules = Field(default_factory=SourceEligibilityRules)
    outliers: OutlierRules = Field(default_factory=OutlierRules)
    aggregation: AggregationRules = Field(default_factory=AggregationRules)
    quality: QualityRules = Field(default_factory=QualityRules)
    confidence: ConfidenceRules = Field(default_factory=ConfidenceRules)
    limits: LimitRules = Field(default_factory=LimitRules)
    transport_pricing: TransportPricingRules = Field(default_factory=TransportPricingRules)
    #: Ограничение набора источников профилем (пусто — все допущенные).
    allowed_source_codes: list[str] = Field(default_factory=list)
    excluded_source_codes: list[str] = Field(default_factory=list)
    rounding_digits: int = Field(0, ge=0, le=2)

    @model_validator(mode="after")
    def _check_timeouts(self) -> ProfileRules:
        if self.limits.source_hard_timeout_seconds < self.limits.source_soft_timeout_seconds:
            raise ValueError("hard timeout не может быть меньше soft timeout")
        if self.confidence.high_min_quality < self.confidence.medium_min_quality:
            raise ValueError("high_min_quality не может быть меньше medium_min_quality")
        if self.confidence.medium_min_quality < self.confidence.low_min_quality:
            raise ValueError("medium_min_quality не может быть меньше low_min_quality")
        overlap = set(self.allowed_source_codes) & set(self.excluded_source_codes)
        if overlap:
            raise ValueError(f"Источник одновременно разрешен и исключен: {sorted(overlap)}")
        return self

    @classmethod
    def parse(cls, payload: dict | None) -> ProfileRules:
        """Разбор правил из JSONB профиля с понятной ошибкой при рассинхроне схем."""
        return cls.model_validate(payload or {})


class ProfileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$")
    name: str = Field(min_length=2, max_length=255)
    description: str | None = Field(None, max_length=2048)
    version: str | None = Field(None, max_length=32)
    rules: ProfileRules = Field(default_factory=ProfileRules)


class ProfileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    code: str
    name: str
    description: str | None
    version: str
    version_seq: int
    status: ProfileStatus
    rules: dict
    activated_at: str | None = None
    archived_at: str | None = None
    created_at: str | None = None
    created_by: str | None = None
