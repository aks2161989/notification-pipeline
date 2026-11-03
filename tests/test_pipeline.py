"""Integration and unit tests for Phase 3 reconciliation engine, locking, and idempotency."""

from datetime import datetime, timezone
from contextlib import contextmanager
import pytest
from unittest.mock import MagicMock
from sqlalchemy.orm import sessionmaker

from src.models.domain import VideoStatus, NotificationDeliveryStatus, EventType
from src.models.orm import ProjectORM, FellowORM, VideoRecordORM, NotificationLogORM
from src.pipeline.lock import ExecutionLock, LockAcquisitionError
from src.pipeline.engine import PipelineEngine
from src.adapters.youtube_adapter import MockYouTubeAdapter, YouTubeVideoMetadata
from src.adapters.email_adapter import NotificationDispatcher, DispatchResult, BaseEmailSink
from src.config import PipelineMode


def test_execution_lock_mutual_exclusion(tmp_path):
    """Verify that two concurrent runners cannot hold the lock simultaneously."""
    lock_file = tmp_path / ".test.lock"
    lock1 = ExecutionLock(lock_file=lock_file, timeout_seconds=60)
    lock2 = ExecutionLock(lock_file=lock_file, timeout_seconds=60)

    # First worker acquires
    assert lock1.acquire() is True

    # Second worker attempts acquisition while active -> must raise LockAcquisitionError
    with pytest.raises(LockAcquisitionError) as exc_info:
        lock2.acquire()
    assert "Active execution lock held" in str(exc_info.value)

    # First worker releases
    lock1.release()

    # Second worker can now acquire
    assert lock2.acquire() is True
    lock2.release()


def test_execution_lock_stale_reclaim(tmp_path):
    """Verify that a stale lock exceeding the TTL is safely reclaimed."""
    lock_file = tmp_path / ".test.lock"
    # Write a simulated abandoned lock with old timestamp
    lock_file.write_text("pid=99999\nhostname=crashed-host\ntimestamp=1000.0\n", encoding="utf-8")

    # New runner with short timeout should detect stale lock and reclaim
    lock = ExecutionLock(lock_file=lock_file, timeout_seconds=10)
    assert lock.acquire() is True
    lock.release()


def test_idempotency_state_machine_and_duplicate_prevention(in_memory_engine, tmp_path):
    """Verify two-phase commit and strict idempotency guard preventing duplicate notifications."""
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=in_memory_engine)

    @contextmanager
    def test_session_factory():
        s = TestSessionLocal()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    # Seed test project, fellow, and video in queued state
    with test_session_factory() as session:
        project = ProjectORM(project_id=1, project_name="Madison", project_type="internal")
        fellow = FellowORM(
            fellow_id=1, project_id=1, full_name="Jainil Shah", email="shah.jainil@northeastern.edu", status="active"
        )
        video = VideoRecordORM(
            video_id=10,
            fellow_id=1,
            title="Madison Architecture",
            youtube_video_id="yt_test_id",
            current_status=VideoStatus.QUEUED.value,
        )
        session.add_all([project, fellow, video])

    # Setup mock YouTube adapter with video set to public
    yt = MockYouTubeAdapter()
    yt.register_video(YouTubeVideoMetadata(
        video_id="yt_test_id",
        title="Madison Architecture",
        privacy_status="public",
        upload_status="processed",
    ))

    # Spy on email delivery
    mock_sink = MagicMock(spec=BaseEmailSink)
    mock_sink.deliver.return_value = True
    dispatcher = NotificationDispatcher(sinks=[mock_sink])

    engine = PipelineEngine(
        youtube_adapter=yt,
        dispatcher=dispatcher,
        lock=ExecutionLock(lock_file=tmp_path / ".lock"),
        session_factory=test_session_factory,
    )

    # First Run: Video is public, should transition to PUBLISHED and dispatch 1 notification
    report1 = engine.run_reconciliation(mode=PipelineMode.PROD)
    assert report1.new_publications_detected == 1
    assert report1.notifications_sent == 1
    assert report1.duplicates_prevented == 0
    assert mock_sink.deliver.call_count == 1

    # Verify database state after first run
    with test_session_factory() as session:
        from src.storage.repository import PipelineRepository
        repo = PipelineRepository(session)
        video_after = repo.get_video(10)
        assert video_after.current_status == VideoStatus.PUBLISHED.value
        assert video_after.published_on is not None

        notifs = repo.get_notifications_for_video(10)
        assert len(notifs) == 1
        assert notifs[0].delivery_status == NotificationDeliveryStatus.SENT.value

        # Second Run (simulating next scheduled cron trigger)
        # Even if video was in queued status or re-evaluated, idempotency guard must catch it
        repo.update_video_status(10, status=VideoStatus.QUEUED)

    report2 = engine.run_reconciliation(mode=PipelineMode.PROD)
    assert report2.duplicates_prevented == 1
    assert report2.notifications_sent == 0
    # Crucial assertion: external sink was NOT called again!
    assert mock_sink.deliver.call_count == 1


def test_failure_handling_and_retry_state(in_memory_engine, tmp_path):
    """Verify that an email failure updates state to FAILED without crashing pipeline."""
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=in_memory_engine)

    @contextmanager
    def test_session_factory():
        s = TestSessionLocal()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    with test_session_factory() as session:
        project = ProjectORM(project_id=2, project_name="NeuroVEP", project_type="internal")
        fellow = FellowORM(
            fellow_id=2, project_id=2, full_name="Maya Lin", email="m.lin@northeastern.edu", status="active"
        )
        video = VideoRecordORM(
            video_id=20,
            fellow_id=2,
            title="NeuroVEP Analysis",
            youtube_video_id="yt_fail_test",
            current_status=VideoStatus.QUEUED.value,
        )
        session.add_all([project, fellow, video])

    yt = MockYouTubeAdapter()
    yt.register_video(YouTubeVideoMetadata(
        video_id="yt_fail_test",
        title="NeuroVEP Analysis",
        privacy_status="public",
        upload_status="processed",
    ))

    # Faulty sink simulating SMTP connection drop
    failing_sink = MagicMock(spec=BaseEmailSink)
    failing_sink.deliver.side_effect = ConnectionError("SMTP relay connection timeout")
    dispatcher = NotificationDispatcher(sinks=[failing_sink])

    engine = PipelineEngine(
        youtube_adapter=yt,
        dispatcher=dispatcher,
        lock=ExecutionLock(lock_file=tmp_path / ".lock"),
        session_factory=test_session_factory,
    )

    report = engine.run_reconciliation(mode=PipelineMode.PROD)
    assert report.failures == 1
    assert report.notifications_sent == 0

    # Verify DB recorded failure reason rather than silently dropping
    with test_session_factory() as session:
        from src.storage.repository import PipelineRepository
        repo = PipelineRepository(session)
        notifs = repo.get_notifications_for_video(20)
        assert len(notifs) == 1
        assert notifs[0].delivery_status == NotificationDeliveryStatus.FAILED.value
        assert "SMTP relay connection timeout" in notifs[0].reason
