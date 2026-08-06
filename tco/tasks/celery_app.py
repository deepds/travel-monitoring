"""Celery-приложение и расписание Beat.

Очереди разделены намеренно: сбор данных (``collect``) конкурирует за сеть,
расчет (``compute``) — за CPU, обслуживание (``maintenance``) не должно
вытеснять пользовательские On-demand запросы (``ondemand``).
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from tco.core.config import get_settings
from tco.core.logging import configure_logging

settings = get_settings()

celery_app = Celery("tco", broker=settings.broker_url, backend=settings.result_backend)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone=settings.timezone,
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=200,
    result_expires=7 * 24 * 3600,
    broker_connection_retry_on_startup=True,
    task_always_eager=settings.celery_task_always_eager,
    task_eager_propagates=settings.celery_task_always_eager,
    task_default_queue="compute",
    task_routes={
        "tco.collect.*": {"queue": "collect"},
        "tco.snapshot.*": {"queue": "collect"},
        "tco.calculate.*": {"queue": "compute"},
        "tco.ondemand.*": {"queue": "ondemand"},
        "tco.monitoring.*": {"queue": "compute"},
        "tco.maintenance.*": {"queue": "maintenance"},
        "tco.metrics.*": {"queue": "maintenance"},
        "tco.export.*": {"queue": "maintenance"},
        "tco.source.*": {"queue": "maintenance"},
    },
    task_annotations={
        "*": {"max_retries": settings.connector_max_retries},
        # Предел времени на задачу сбора.
        #
        # У самого HTTP-вызова таймауты заданы, повторы ограничены, ограничитель
        # темпа работает по бюджету — и все равно 6 августа воркер трижды за
        # сутки вставал намертво: поток внутри задачи не возвращался, дочерний
        # процесс оставался занят с открытой транзакцией в базе, свободных не
        # оставалось, и очередь переставала разбираться. Контейнер при этом
        # числился здоровым, в логе стояла тишина.
        #
        # Предел не лечит причину зависания, но снимает его последствие: задачу
        # снимают, процесс освобождается, воркер продолжает сам. Без него
        # единственным лекарством был перезапуск руками.
        #
        # Величина взята с запасом от законной длительности: сбор ЖД делает до
        # 16 обращений за картой мест примерно по 8,5 секунды плюс до трех
        # попыток по 30 секунд на каждый запрос. Когда источник отвечает
        # таймаутами, тот же перебор занимает полчаса — предел обрывает его.
        #
        # Имена задач перечислены полностью, без шаблона: `task_annotations`
        # понимает только точное имя или `*`. Шаблоны вида `tco.collect.*`
        # работают в `task_routes`, и написанное по их образцу правило здесь
        # молча не применяется — проверено на стенде, где предел не сработал.
        "tco.collect.collect_transport_offers": {
            "soft_time_limit": 300,
            "time_limit": 360,
        },
        "tco.collect.collect_accommodation_offers": {
            "soft_time_limit": 300,
            "time_limit": 360,
        },
    },
)

#: Расписание планового наблюдения выводится из ``SNAPSHOT_INTERVAL_HOURS``.
#: При интервале в 1 час это ежечасный запуск, при 6 — четыре снимка в сутки.
_interval_hours = max(1, settings.snapshot_interval_hours)
_snapshot_hours = (
    "*" if _interval_hours == 1 else ",".join(str(h) for h in range(0, 24, _interval_hours))
)
#: Задача живет не дольше своего окна: просроченный запуск бессмысленно
#: выполнять, когда уже наступило следующее наблюдение.
_snapshot_expires = max(300, _interval_hours * 3600 - 300)

celery_app.conf.beat_schedule = {
    # Наблюдение разведено по частоте тегом `cadence:daily`.
    #
    # По общему интервалу — направления, ради которых платформа и заводилась.
    # Раз в сутки — все остальное: сетка витрины (она на порядок больше, и в
    # часовом прогоне давала бы 42 тысячи обращений к источникам в сутки) и
    # направления вне пятерки ключевых городов, где суточного шага достаточно.
    # Суточный прогон, в свою очередь, разделен по компонентам — см. ниже.
    "monitoring-snapshot": {
        "task": "tco.monitoring.refresh_all_monitoring_scenarios",
        "schedule": crontab(minute="5", hour=_snapshot_hours),
        "options": {"queue": "compute", "expires": _snapshot_expires},
        "kwargs": {"without_tag": "cadence:daily"},
    },
    # Суточный прогон разнесен по компонентам, каждой — свое окно с запасом.
    #
    # Одним залпом это 1758 сценариев, и источник такого не выдерживает: на
    # прогоне 6 августа размыкатель цепи открылся в середине, и проживание —
    # оно собирается только у Туту — осталось без сбора по 395 сценариям из
    # 465. Тот же набор, собранный отдельно, прошел без единого срабатывания
    # размыкателя: 1154 успешных обращения.
    #
    # Окна взяты с запасом от наблюдавшейся длительности: если прогон
    # затянется, следующий не встанет ему в спину, а вся ночная работа
    # заканчивается до десяти утра, оставляя четырнадцать часов на повтор.
    #
    # Прогоны попадают в одно окно идемпотентности, но получают разные задачи:
    # область отбора вместе с видом проезда входит в ключ.
    "grid-rail": {
        "task": "tco.monitoring.refresh_all_monitoring_scenarios",
        "schedule": crontab(minute="0", hour="1"),
        "options": {"queue": "compute", "expires": 2 * 3600},
        "kwargs": {"with_tag": "cadence:daily", "transport_type": "RAIL"},
    },
    "grid-avia": {
        "task": "tco.monitoring.refresh_all_monitoring_scenarios",
        "schedule": crontab(minute="30", hour="3"),
        "options": {"queue": "compute", "expires": 2 * 3600},
        "kwargs": {"with_tag": "cadence:daily", "transport_type": "AVIA"},
    },
    "grid-accommodation": {
        "task": "tco.monitoring.refresh_all_monitoring_scenarios",
        "schedule": crontab(minute="0", hour="6"),
        "options": {"queue": "compute", "expires": 2 * 3600},
        "kwargs": {"with_tag": "showcase-accommodation"},
    },
    # Досбор — штатный шаг, а не аварийная мера: он добирает то, что не
    # собралось ночью, пока причина еще действует редко. Сценарии, где источник
    # ответил таймаутом или сработал размыкатель, попадают сюда сами —
    # размыкатель к девяти утра остывает (по умолчанию 900 секунд).
    "backfill-missing": {
        "task": "tco.monitoring.backfill_missing_observations",
        "schedule": crontab(minute="0", hour="9"),
        "options": {"queue": "compute", "expires": 4 * 3600},
    },
    # Раньше планового сбора: сетка должна быть достроена к моменту, когда
    # начнется наблюдение, иначе самая дальняя дата горизонта пропустит сутки.
    "observation-grid-daily": {
        "task": "tco.monitoring.maintain_observation_grid",
        "schedule": crontab(minute="30", hour="0"),
        "options": {"queue": "maintenance"},
    },
    "source-health-check-hourly": {
        "task": "tco.source.health_check_all_sources",
        "schedule": crontab(minute="20"),
        "options": {"queue": "maintenance", "expires": 1800},
    },
    "source-confidence-daily": {
        "task": "tco.metrics.calculate_source_confidence_all",
        "schedule": crontab(minute="40", hour="2"),
        "options": {"queue": "maintenance"},
    },
    "refresh-source-horizons-daily": {
        "task": "tco.source.refresh_source_horizons",
        "schedule": crontab(minute="50", hour="3"),
        "options": {"queue": "maintenance"},
    },
    "retention-cleanup-daily": {
        "task": "tco.maintenance.cleanup_expired_data",
        "schedule": crontab(minute="15", hour="4"),
        "options": {"queue": "maintenance"},
    },
    "cache-cleanup-hourly": {
        "task": "tco.maintenance.cleanup_expired_cache",
        "schedule": crontab(minute="35"),
        "options": {"queue": "maintenance"},
    },
    "detect-stalled-jobs": {
        "task": "tco.maintenance.detect_stalled_jobs",
        "schedule": crontab(minute="*/10"),
        "options": {"queue": "maintenance", "expires": 300},
    },
}

celery_app.autodiscover_tasks(["tco.tasks"], force=True)

# Задачи объявлены через ``@shared_task`` и разрешаются в ``current_app``,
# который в Celery хранится в thread-local. FastAPI исполняет синхронные
# эндпоинты в пуле потоков, поэтому без явного ``set_default`` часть запросов
# попадала бы в дефолтное приложение Celery с брокером ``amqp://localhost``
# вместо настроенного. ``set_default`` задает приложение глобально.
celery_app.set_default()


@celery_app.on_after_configure.connect
def _setup_logging(sender, **_kwargs):  # noqa: ANN001, ANN202 - сигнал Celery
    configure_logging()
