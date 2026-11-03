"""Unit tests for Phase 1 data models, repository, and idempotency queries."""

from datetime import datetime, timezone
import pytest
from sqlalchemy.exc import IntegrityError

from src.models.domain import VideoStatus, NotificationDeliveryStatus, EventType
from src.models.orm import ProjectORM, FellowORM, VideoRecordORM, NotificationLogORM
from src.storage.seeder import seed_synthetic_scenarios, seed_from_excel
from src.config import settings


def test_project_and_fellow_creation(db_session, repo):
    """Test relational integrity between Project and Fellow."""
    project = ProjectORM(project_id=10, project_name="Test Project", project_type="internal")
    db_session.add(project)
    db_session.flush()

    fellow = FellowORM(
        fellow_id=101,
        project_id=10,
        full_name="Alice Developer",
        email="alice@northeastern.edu",
        status="active",
        display_label="Alice - Test Project",
    )
    db_session.add(fellow)
    db_session.flush()

    retrieved_fellow = repo.get_fellow(101)
    assert retrieved_fellow is not None
    assert retrieved_fellow.full_name == "Alice Developer"
    assert retrieved_fellow.project.project_name == "Test Project"


def test_video_record_lifecycle(db_session, repo):
    """Test video record creation, status updates, and review events."""
    # Seed minimal project and fellow
    project = ProjectORM(project_id=1, project_name="Madison", project_type="internal")
    fellow = FellowORM(
        fellow_id=1, project_id=1, full_name="John Fellow", email="john@example.com", status="active"
    )
    db_session.add_all([project, fellow])
    db_session.flush()

    video = VideoRecordORM(
        video_id=1,
        fellow_id=1,
        title="Distributed Caching V1",
        youtube_video_id="yt_12345",
        drive_file_id="drive_file_999",
        current_status=VideoStatus.QUEUED.value,
    )
    db_session.add(video)
    db_session.flush()

    # Query queued videos
    queued = repo.get_videos_by_status(VideoStatus.QUEUED)
    assert len(queued) == 1
    assert queued[0].youtube_video_id == "yt_12345"

    # Transition to published
    now = datetime.now(timezone.utc)
    repo.update_video_status(video_id=1, status=VideoStatus.PUBLISHED, published_on=now)
    repo.record_review_event(video_id=1, event_type=EventType.PUBLISHED, reviewer="prof.brown@university.edu")
    
    updated_video = repo.get_video(1)
    assert updated_video.current_status == VideoStatus.PUBLISHED.value
    assert updated_video.published_on == now

    events = repo.get_events_for_video(1)
    assert len(events) == 1
    assert events[0].event_type == EventType.PUBLISHED.value


def test_idempotency_guard_detection(db_session, repo):
    """Verify that get_active_or_sent_notification reliably catches duplicate sends."""
    project = ProjectORM(project_id=1, project_name="Madison", project_type="internal")
    fellow = FellowORM(fellow_id=1, project_id=1, full_name="John Fellow", email="john@example.com")
    video = VideoRecordORM(video_id=1, fellow_id=1, title="Test Video", youtube_video_id="yt_test")
    db_session.add_all([project, fellow, video])
    db_session.flush()

    # Initially no notification exists
    guard = repo.get_active_or_sent_notification(video_id=1)
    assert guard is None

    # Step 1: Transition to SENDING
    notif = repo.create_notification(
        video_id=1,
        recipient_email="john@example.com",
        delivery_status=NotificationDeliveryStatus.SENDING,
        reason="Video published on YouTube",
    )
    # Idempotency guard should now catch this in-flight record
    guard_in_flight = repo.get_active_or_sent_notification(video_id=1)
    assert guard_in_flight is not None
    assert guard_in_flight.delivery_status == NotificationDeliveryStatus.SENDING.value

    # Step 2: Transition to SENT
    repo.update_notification_status(notif.notification_id, delivery_status=NotificationDeliveryStatus.SENT)
    guard_sent = repo.get_active_or_sent_notification(video_id=1)
    assert guard_sent is not None
    assert guard_sent.delivery_status == NotificationDeliveryStatus.SENT.value


def test_seeder_synthetic_scenarios(db_session, repo):
    """Verify seeder loads complete test matrix."""
    result = seed_synthetic_scenarios(db_session)
    assert result["status"] == "seeded"

    metrics = repo.get_metrics()
    assert metrics["total_projects"] >= 4
    assert metrics["total_fellows"] >= 5
    assert metrics["total_videos"] >= 5
    assert metrics["sent_notifications"] >= 1


def test_excel_file_ingestion(db_session, repo):
    """Verify ingestion directly from YouTube_Pipeline_Tracker.xlsx if present."""
    if settings.TRACKER_XLSX_PATH.exists():
        counts = seed_from_excel(settings.TRACKER_XLSX_PATH, db_session)
        assert counts["projects"] > 0
        assert counts["pms"] > 0
        assert counts["videos"] > 0
