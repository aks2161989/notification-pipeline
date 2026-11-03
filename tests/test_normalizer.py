"""Unit tests for ingestion data normalization, identifier extractors, and DB check constraints."""

import pytest
from sqlalchemy.exc import IntegrityError

from src.storage.normalizer import (
    normalize_email,
    extract_youtube_id,
    extract_drive_file_id,
    normalize_status,
    clean_text,
)
from src.models.orm import VideoRecordORM, NotificationLogORM, FellowORM, ProjectORM


def test_normalize_email_hygiene():
    """Verify email whitespace trimming, lowercasing, and zero-width character removal."""
    assert normalize_email("  Jainil.Shah@Northeastern.EDU  ") == "shah.jainil@northeastern.edu" or \
           normalize_email("  Jainil.Shah@Northeastern.EDU  ") == "jainil.shah@northeastern.edu"
    assert normalize_email(None) == ""
    assert normalize_email("USER@EXAMPLE.COM\u200b") == "user@example.com"


def test_extract_youtube_id_formats():
    """Verify YouTube video ID extraction across various URL formats and raw IDs."""
    expected = "RzW17zhV4Nw"

    # Raw 11-char ID
    assert extract_youtube_id("RzW17zhV4Nw") == expected
    assert extract_youtube_id("  RzW17zhV4Nw  ") == expected

    # Standard watch URL with query parameters
    assert extract_youtube_id("https://www.youtube.com/watch?v=RzW17zhV4Nw") == expected
    assert extract_youtube_id("https://www.youtube.com/watch?v=RzW17zhV4Nw&feature=shared&t=15") == expected

    # Short URL (youtu.be)
    assert extract_youtube_id("https://youtu.be/RzW17zhV4Nw") == expected
    assert extract_youtube_id("https://youtu.be/RzW17zhV4Nw?t=20") == expected

    # Embed URL
    assert extract_youtube_id("https://www.youtube.com/embed/RzW17zhV4Nw") == expected

    # Empty / None
    assert extract_youtube_id(None) is None
    assert extract_youtube_id("") is None


def test_extract_drive_file_id_formats():
    """Verify Google Drive file ID extraction from URLs and raw tokens."""
    expected = "1ovAQ1N19bqlFgjhSuC8Aw4wnvSKzbTsFxt5qtOYzaY4"

    # Direct file/d/<id>/view URL
    url1 = f"https://drive.google.com/file/d/{expected}/view?usp=sharing"
    assert extract_drive_file_id(url1) == expected

    # Open query param URL
    url2 = f"https://drive.google.com/open?id={expected}"
    assert extract_drive_file_id(url2) == expected

    # Raw ID
    assert extract_drive_file_id("drive_file_101") == "drive_file_101"
    assert extract_drive_file_id(None) is None


def test_normalize_status():
    """Verify status normalization converts to lowercase snake_case."""
    assert normalize_status("In Review") == "in_review"
    assert normalize_status("Needs-Modification") == "needs_modification"
    assert normalize_status("  PUBLISHED  ") == "published"
    assert normalize_status(None, default="submitted") == "submitted"


def test_db_check_constraint_rejects_invalid_video_status(db_session):
    """Verify SQL CheckConstraint enforces valid video statuses at database level."""
    project = ProjectORM(project_id=1, project_name="Madison")
    fellow = FellowORM(fellow_id=1, project_id=1, full_name="John", email="john@example.com")
    db_session.add_all([project, fellow])
    db_session.commit()

    # Attempt to insert an invalid status violating CHECK constraint
    invalid_video = VideoRecordORM(
        video_id=999,
        fellow_id=1,
        title="Invalid Status Video",
        current_status="BOGUS_STATUS",
    )
    db_session.add(invalid_video)
    
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_db_check_constraint_rejects_invalid_notification_status(db_session):
    """Verify SQL CheckConstraint enforces valid notification delivery statuses."""
    project = ProjectORM(project_id=1, project_name="Madison")
    fellow = FellowORM(fellow_id=1, project_id=1, full_name="John", email="john@example.com")
    video = VideoRecordORM(video_id=1, fellow_id=1, title="Test", current_status="submitted")
    db_session.add_all([project, fellow, video])
    db_session.commit()

    # Attempt to insert an invalid notification status
    invalid_notif = NotificationLogORM(
        notification_id=999,
        video_id=1,
        recipient_email="john@example.com",
        delivery_status="INVALID_STATE",
    )
    db_session.add(invalid_notif)

    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
