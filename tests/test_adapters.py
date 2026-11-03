"""Unit tests for Phase 2 adapters (YouTube, Drive, Email Sinks, Rollout Modes)."""

import json
from pathlib import Path
from src.adapters.youtube_adapter import MockYouTubeAdapter, YouTubeVideoMetadata
from src.adapters.drive_adapter import MockDriveAdapter
from src.adapters.email_adapter import (
    EmailFormatter,
    NotificationDispatcher,
    FileOutboxSink,
    DispatchResult,
)
from src.config import PipelineMode
from src.models.domain import NotificationDeliveryStatus


def test_mock_youtube_adapter_lifecycle():
    """Verify YouTube adapter status reporting and publication simulation."""
    yt = MockYouTubeAdapter()
    
    # RzW17zhV4Nw starts unlisted in Queue
    assert not yt.is_video_public("RzW17zhV4Nw")
    meta = yt.get_video_status("RzW17zhV4Nw")
    assert meta.privacy_status == "unlisted"
    assert meta.playlist_name == "Queue"

    # Simulate reviewer hitting publish
    assert yt.simulate_publish("RzW17zhV4Nw")
    assert yt.is_video_public("RzW17zhV4Nw")
    meta_after = yt.get_video_status("RzW17zhV4Nw")
    assert meta_after.privacy_status == "public"
    assert meta_after.published_at is not None

    # Simulate unpublish
    yt.simulate_unpublish("RzW17zhV4Nw")
    assert not yt.is_video_public("RzW17zhV4Nw")


def test_mock_drive_adapter_metadata():
    """Verify Google Drive file metadata retrieval."""
    drive = MockDriveAdapter()
    file_meta = drive.get_file_metadata("drive_file_101")
    assert file_meta is not None
    assert file_meta.name == "Madison_Architecture_Final.mp4"
    assert file_meta.submitter_email == "shah.jainil@northeastern.edu"
    assert file_meta.mime_type == "video/mp4"


def test_email_formatter_templates():
    """Verify email formatting, subjects, and headers across rollout modes."""
    notif = EmailFormatter.render(
        video_id=1,
        fellow_id=1,
        fellow_name="Jainil Shah",
        fellow_email="shah.jainil@northeastern.edu",
        video_title="Madison Architecture",
        youtube_video_id="RzW17zhV4Nw",
        project_name="Madison",
        mode=PipelineMode.DRY_RUN,
    )
    assert "[DRY-RUN]" in notif.subject
    assert "https://www.youtube.com/watch?v=RzW17zhV4Nw" in notif.youtube_url
    assert "Madison Architecture" in notif.body_text
    assert "<h1>Humanitarians AI</h1>" in notif.body_html
    assert notif.recipient_email == "shah.jainil@northeastern.edu"
    assert notif.headers["X-Original-Recipient"] == "shah.jainil@northeastern.edu"


def test_dispatcher_dry_run_mode(tmp_path):
    """Verify DRY_RUN mode generates previews but skips live send."""
    sink = FileOutboxSink(outbox_dir=tmp_path)
    dispatcher = NotificationDispatcher(sinks=[sink])

    result = dispatcher.dispatch(
        video_id=10,
        fellow_id=1,
        fellow_name="Jainil Shah",
        fellow_email="shah.jainil@northeastern.edu",
        fellow_status="active",
        video_title="Test Video",
        youtube_video_id="yt123",
        project_name="Madison",
        mode=PipelineMode.DRY_RUN,
    )
    assert result.success is True
    assert result.delivery_status == NotificationDeliveryStatus.SKIPPED
    assert "Dry-run mode evaluated" in result.reason

    # Verify outbox files were saved
    html_files = list(tmp_path.glob("*.html"))
    json_files = list(tmp_path.glob("*.json"))
    assert len(html_files) == 1
    assert len(json_files) == 1

    payload = json.loads(json_files[0].read_text(encoding="utf-8"))
    assert payload["intended_recipient"] == "shah.jainil@northeastern.edu"
    assert payload["mode"] == "DRY_RUN"


def test_dispatcher_redirected_mode(tmp_path):
    """Verify REDIRECTED mode redirects recipient address."""
    sink = FileOutboxSink(outbox_dir=tmp_path)
    dispatcher = NotificationDispatcher(sinks=[sink])

    result = dispatcher.dispatch(
        video_id=20,
        fellow_id=2,
        fellow_name="Maya Lin",
        fellow_email="m.lin@northeastern.edu",
        fellow_status="active",
        video_title="NeuroVEP Analysis",
        youtube_video_id="yt456",
        project_name="NeuroVEP",
        mode=PipelineMode.REDIRECTED,
        redirect_to="team.lead@example.com",
    )
    assert result.success is True
    assert result.delivery_status == NotificationDeliveryStatus.SENT
    assert result.actual_recipient == "team.lead@example.com"

    json_files = list(tmp_path.glob("*.json"))
    payload = json.loads(json_files[0].read_text(encoding="utf-8"))
    assert payload["recipient_email"] == "team.lead@example.com"
    assert payload["intended_recipient"] == "m.lin@northeastern.edu"


def test_dispatcher_inactive_fellow_skipped():
    """Verify that inactive fellows are cleanly skipped with reason logged."""
    dispatcher = NotificationDispatcher(sinks=[])
    result = dispatcher.dispatch(
        video_id=30,
        fellow_id=3,
        fellow_name="Inactive Fellow",
        fellow_email="inactive@northeastern.edu",
        fellow_status="inactive",
        video_title="Old Video",
        youtube_video_id="yt789",
        project_name="Medhavi",
        mode=PipelineMode.PROD,
    )
    assert result.success is False
    assert result.delivery_status == NotificationDeliveryStatus.SKIPPED
    assert "inactive" in result.reason
