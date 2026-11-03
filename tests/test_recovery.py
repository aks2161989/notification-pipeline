"""Unit tests for Phase 4 operational reliability: health monitoring, stranded jobs, and retries."""

from datetime import datetime, timezone, timedelta
from contextlib import contextmanager
import pytest
from unittest.mock import MagicMock
from sqlalchemy.orm import sessionmaker

from src.models.domain import VideoStatus, NotificationDeliveryStatus
from src.models.orm import ProjectORM, FellowORM, VideoRecordORM, NotificationLogORM
from src.pipeline.recovery import OperationalHealthService, RetryService
from src.adapters.email_adapter import NotificationDispatcher, BaseEmailSink
from src.config import PipelineMode


@pytest.fixture
def test_session_factory(in_memory_engine):
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=in_memory_engine)

    @contextmanager
    def _factory():
        s = TestSessionLocal()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()
    return _factory


def test_doctor_detects_stranded_sending(test_session_factory):
    """Verify health monitor catches notifications stuck in SENDING state."""
    with test_session_factory() as session:
        project = ProjectORM(project_id=1, project_name="Madison")
        fellow = FellowORM(fellow_id=1, project_id=1, full_name="John", email="john@example.com")
        video = VideoRecordORM(video_id=1, fellow_id=1, title="Test", youtube_video_id="yt1")
        # Stranded sending record from 20 minutes ago
        stranded_notif = NotificationLogORM(
            notification_id=100,
            video_id=1,
            recipient_email="john@example.com",
            delivery_status=NotificationDeliveryStatus.SENDING.value,
            sent_on=datetime.now(timezone.utc) - timedelta(minutes=20),
        )
        session.add_all([project, fellow, video, stranded_notif])

    service = OperationalHealthService(session_factory=test_session_factory)
    report = service.check_health()

    assert any(i.category == "STRANDED_JOB" and i.record_id == 100 for i in report.issues)


def test_doctor_detects_exhausted_retries(test_session_factory):
    """Verify health monitor flags permanently failed notifications."""
    with test_session_factory() as session:
        project = ProjectORM(project_id=2, project_name="NeuroVEP")
        fellow = FellowORM(fellow_id=2, project_id=2, full_name="Maya", email="maya@example.com")
        video = VideoRecordORM(video_id=2, fellow_id=2, title="Test 2", youtube_video_id="yt2")
        failed_notif = NotificationLogORM(
            notification_id=200,
            video_id=2,
            recipient_email="maya@example.com",
            delivery_status=NotificationDeliveryStatus.FAILED.value,
            attempt_count=3,
            reason="Repeated SMTP timeout",
        )
        session.add_all([project, fellow, video, failed_notif])

    service = OperationalHealthService(session_factory=test_session_factory)
    report = service.check_health()

    assert report.healthy is False  # CRITICAL issues make it unhealthy
    assert any(i.category == "RETRY_EXHAUSTED" and i.record_id == 200 for i in report.issues)


def test_retry_service_successful_retry(test_session_factory):
    """Verify RetryService dispatches failed records and transitions them to SENT."""
    with test_session_factory() as session:
        project = ProjectORM(project_id=3, project_name="Lyrical")
        fellow = FellowORM(fellow_id=3, project_id=3, full_name="Marcus", email="marcus@example.com")
        video = VideoRecordORM(video_id=3, fellow_id=3, title="Test 3", youtube_video_id="yt3")
        failed_notif = NotificationLogORM(
            notification_id=300,
            video_id=3,
            recipient_email="marcus@example.com",
            delivery_status=NotificationDeliveryStatus.FAILED.value,
            attempt_count=1,
            reason="Temporary timeout",
        )
        session.add_all([project, fellow, video, failed_notif])

    mock_sink = MagicMock(spec=BaseEmailSink)
    mock_sink.deliver.return_value = True
    dispatcher = NotificationDispatcher(sinks=[mock_sink])

    retry_service = RetryService(dispatcher=dispatcher, session_factory=test_session_factory)
    report = retry_service.retry_failed(max_attempts=3, mode=PipelineMode.PROD)

    assert report.retried_count == 1
    assert report.succeeded_count == 1
    assert mock_sink.deliver.call_count == 1

    # Verify DB update
    with test_session_factory() as session:
        notif = session.get(NotificationLogORM, 300)
        assert notif.delivery_status == NotificationDeliveryStatus.SENT.value
        assert notif.attempt_count == 2
        assert "retry" in notif.reason.lower()
