"""Жизненный цикл задачи-пачки планового прогона.

Пачка после диспетчеризации не делает ничего сама: работу выполняют дочерние
задачи по одной на сценарий. Пока дети не отмечались в родителе, у пачки
замирал ``heartbeat_at``, и через четверть часа ``detect_stalled_jobs``
объявлял живой прогон зависшим — на стенде это давало 25 ложных ``TIMED_OUT``
в сутки, включая суточный прогон сетки, который в этот момент работал.

Здесь же проверяется область видимости в ключе идемпотентности: суточный
прогон сетки, часовой прогон каталога и ручной досбор попадают в одно часовое
окно, и с общим ключом второй из них молча не делал бы ничего.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from tco.core.enums import JobStatus, JobType
from tco.core.utils import utcnow
from tco.engine.fingerprint import job_idempotency_key
from tco.services import jobs as job_service

BUCKET_HOURS = 1


def make_batch(session, total: int) -> job_service.Job:
    handle = job_service.create_job(
        session,
        job_type=JobType.MONITORING_BATCH,
        idempotency_key=f"batch-{uuid.uuid4().hex}",
        params={"scenario_count": total},
        created_by="scheduler",
    )
    job_service.transition(session, handle.job, JobStatus.RUNNING, message="Запуск")
    job_service.set_progress(session, handle.job, current=0, total=total)
    return handle.job


def make_child(session, batch: job_service.Job) -> job_service.Job:
    handle = job_service.create_job(
        session,
        job_type=JobType.MONITORING_SCENARIO,
        idempotency_key=f"child-{uuid.uuid4().hex}",
        parent_job_id=batch.id,
        created_by="scheduler",
    )
    job_service.transition(session, handle.job, JobStatus.RUNNING)
    return handle.job


class TestBatchClosesItself:
    def test_closes_when_every_child_finished(self, session):
        batch = make_batch(session, total=3)
        children = [make_child(session, batch) for _ in range(3)]

        for child in children:
            job_service.transition(session, child, JobStatus.SUCCESS)

        session.refresh(batch)
        assert batch.status == JobStatus.SUCCESS.value
        assert batch.progress_current == 3
        assert batch.finished_at is not None

    def test_stays_running_until_the_last_child(self, session):
        batch = make_batch(session, total=3)
        children = [make_child(session, batch) for _ in range(3)]

        job_service.transition(session, children[0], JobStatus.SUCCESS)
        job_service.transition(session, children[1], JobStatus.SUCCESS)

        session.refresh(batch)
        assert batch.status == JobStatus.RUNNING.value
        assert batch.progress_current == 2

    def test_failed_child_counts_as_finished(self, session):
        """Иначе одна упавшая задача оставляла бы пачку незакрытой навсегда."""
        batch = make_batch(session, total=2)
        ok, broken = make_child(session, batch), make_child(session, batch)

        job_service.transition(session, ok, JobStatus.SUCCESS)
        job_service.transition(session, broken, JobStatus.FAILED, error_code="BOOM")

        session.refresh(batch)
        assert batch.status == JobStatus.SUCCESS.value
        assert batch.result["children_by_status"][JobStatus.FAILED.value] == 1

    def test_late_child_does_not_reopen_a_closed_batch(self, session):
        batch = make_batch(session, total=1)
        job_service.transition(session, make_child(session, batch), JobStatus.SUCCESS)
        session.refresh(batch)
        closed_at = batch.finished_at

        job_service.report_child_finished(session, batch.id)

        session.refresh(batch)
        assert batch.status == JobStatus.SUCCESS.value
        assert batch.progress_current == 1
        assert batch.finished_at == closed_at

    def test_child_revived_after_false_timeout_counts_once(self, session):
        """Ошибочно признанную зависшей задачу конвейер доводит до SUCCESS.

        Терминальный статус ей при этом ставится дважды, и без защиты пачка
        досчитывалась бы до итога на половине сценариев.
        """
        batch = make_batch(session, total=2)
        child = make_child(session, batch)

        job_service.transition(session, child, JobStatus.TIMED_OUT, error_code="STALLED")
        job_service.transition(session, child, JobStatus.SUCCESS)

        session.refresh(batch)
        assert batch.progress_current == 1
        assert batch.status == JobStatus.RUNNING.value

    def test_reused_child_counts_for_the_batch_running_it(self, session):
        """Принудительный прогон переиспользует задачу сценария вместе с ней самой.

        Ссылка на пачку в ней при этом остается прежней, и без перевода на
        текущий прогон новая пачка не досчитывалась бы до итога — то есть
        повисала бы ровно так, как до исправления.
        """
        first, second = make_batch(session, total=1), make_batch(session, total=1)
        child = make_child(session, first)
        job_service.transition(session, child, JobStatus.SUCCESS)

        child.parent_job_id = second.id
        session.flush()
        job_service.transition(session, child, JobStatus.RUNNING)
        job_service.transition(session, child, JobStatus.SUCCESS)

        session.refresh(first)
        session.refresh(second)
        assert first.status == JobStatus.SUCCESS.value
        assert second.status == JobStatus.SUCCESS.value

    def test_stolen_child_lets_the_previous_batch_finish(self, session):
        """Незавершенный сценарий, ушедший другому прогону, отмечается в прежнем."""
        first, second = make_batch(session, total=1), make_batch(session, total=1)
        child = make_child(session, first)

        previous_parent = child.parent_job_id
        child.parent_job_id = second.id
        session.flush()
        job_service.report_child_finished(session, previous_parent)
        job_service.transition(session, child, JobStatus.SUCCESS)

        session.refresh(first)
        session.refresh(second)
        assert first.status == JobStatus.SUCCESS.value
        assert second.status == JobStatus.SUCCESS.value

    def test_scenario_without_own_job_is_still_counted(self, session):
        """Невалидный сценарий выбывает, не заведя задачи, и отмечается сам."""
        batch = make_batch(session, total=2)
        job_service.transition(session, make_child(session, batch), JobStatus.SUCCESS)

        job_service.report_child_finished(session, batch.id)

        session.refresh(batch)
        assert batch.status == JobStatus.SUCCESS.value


class TestStalledDetector:
    def test_live_batch_survives_the_detector(self, session):
        batch = make_batch(session, total=5)
        batch.heartbeat_at = utcnow() - timedelta(hours=1)
        session.flush()

        job_service.transition(session, make_child(session, batch), JobStatus.SUCCESS)
        stalled = job_service.detect_stalled_jobs(session, stale_after_seconds=900)

        session.refresh(batch)
        assert batch.id not in {item.id for item in stalled}
        assert batch.status == JobStatus.RUNNING.value

    def test_waiting_in_queue_is_not_stalling(self, session):
        """Сценарий ждет очереди сбора час и все это время жив."""
        batch = make_batch(session, total=5)
        child = make_child(session, batch)
        job_service.transition(session, child, JobStatus.QUEUED)
        child.heartbeat_at = utcnow() - timedelta(hours=1)
        session.flush()

        job_service.detect_stalled_jobs(session, stale_after_seconds=900)

        session.refresh(child)
        assert child.status == JobStatus.QUEUED.value

    def test_lost_queued_job_is_detected_by_the_end_of_a_shift(self, session):
        batch = make_batch(session, total=5)
        child = make_child(session, batch)
        job_service.transition(session, child, JobStatus.QUEUED)
        child.heartbeat_at = utcnow() - timedelta(hours=5)
        session.flush()

        stalled = job_service.detect_stalled_jobs(session, stale_after_seconds=900)

        assert child.id in {item.id for item in stalled}
        assert child.status == JobStatus.TIMED_OUT.value

    def test_truly_stalled_batch_is_still_detected(self, session):
        """Признак зависания не должен пропасть вместе с ложными срабатываниями."""
        batch = make_batch(session, total=5)
        batch.heartbeat_at = utcnow() - timedelta(hours=1)
        session.flush()

        stalled = job_service.detect_stalled_jobs(session, stale_after_seconds=900)

        assert batch.id in {item.id for item in stalled}
        assert batch.status == JobStatus.TIMED_OUT.value


class TestBatchIdempotencyScope:
    @staticmethod
    def key(scope: str) -> str:
        return job_idempotency_key(
            fingerprint=f"monitoring-batch:{scope}",
            requested_at=utcnow(),
            bucket_hours=BUCKET_HOURS,
            profile_version="active",
            run_type="BATCH",
        )

    @pytest.mark.parametrize(
        ("left", "right"),
        [
            ("with=cadence:daily;without=;limit=;by=scheduler", "with=;without=cadence:daily;limit=;by=scheduler"),
            ("with=;without=cadence:daily;limit=;by=scheduler", "with=;without=cadence:daily;limit=;by=cli"),
            ("with=;without=;limit=;by=cli", "with=;without=;limit=5;by=cli"),
        ],
    )
    def test_different_scopes_get_different_keys(self, left, right):
        """Суточный прогон, часовой и ручной досбор попадают в одно окно."""
        assert self.key(left) != self.key(right)

    def test_same_scope_in_one_window_is_idempotent(self):
        scope = "with=;without=cadence:daily;limit=;by=scheduler"
        assert self.key(scope) == self.key(scope)
