"""Adapters package."""

from src.adapters.youtube_adapter import (
    BaseYouTubeAdapter,
    MockYouTubeAdapter,
    LiveYouTubeAdapter,
    YouTubeVideoMetadata,
    get_youtube_adapter,
)
from src.adapters.drive_adapter import (
    BaseDriveAdapter,
    MockDriveAdapter,
    LiveDriveAdapter,
    DriveFileMetadata,
    get_drive_adapter,
)
from src.adapters.email_adapter import (
    BaseEmailSink,
    ConsoleSink,
    FileOutboxSink,
    SMTPSink,
    EmailFormatter,
    NotificationDispatcher,
    EmailNotification,
    DispatchResult,
)

__all__ = [
    "BaseYouTubeAdapter",
    "MockYouTubeAdapter",
    "LiveYouTubeAdapter",
    "YouTubeVideoMetadata",
    "get_youtube_adapter",
    "BaseDriveAdapter",
    "MockDriveAdapter",
    "LiveDriveAdapter",
    "DriveFileMetadata",
    "get_drive_adapter",
    "BaseEmailSink",
    "ConsoleSink",
    "FileOutboxSink",
    "SMTPSink",
    "EmailFormatter",
    "NotificationDispatcher",
    "EmailNotification",
    "DispatchResult",
]
