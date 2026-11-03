"""Domain models and value objects for the notification pipeline."""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from pydantic import BaseModel, ConfigDict, EmailStr, Field


class VideoStatus(str, Enum):
    """Controlled lifecycle statuses for videos in the pipeline."""
    SUBMITTED = "submitted"
    IN_REVIEW = "in_review"
    NEEDS_MODIFICATION = "needs_modification"
    QUEUED = "queued"
    PUBLISHED = "published"


class NotificationDeliveryStatus(str, Enum):
    """Controlled delivery statuses for notifications."""
    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"


class EventType(str, Enum):
    """Controlled review event types."""
    SUBMITTED = "submitted"
    REVIEW_STARTED = "review_started"
    MODIFICATION_REQUESTED = "modification_requested"
    RESUBMITTED = "resubmitted"
    APPROVED = "approved"
    QUEUED = "queued"
    PUBLISHED = "published"


class Project(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: int
    project_name: str
    project_type: str = "internal"


class PM(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    pm_id: int
    project_id: int
    full_name: str
    email: EmailStr
    role: str = "pm"
    status: str = "active"


class Fellow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    fellow_id: int
    project_id: int
    full_name: str
    email: EmailStr
    status: str = "active"
    display_label: Optional[str] = None


class VideoRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    video_id: int
    fellow_id: Optional[int] = None
    title: Optional[str] = None
    youtube_link: Optional[str] = None
    drive_link: Optional[str] = None
    drive_file_id: Optional[str] = None
    youtube_video_id: Optional[str] = None
    current_status: VideoStatus = VideoStatus.SUBMITTED
    status_note: Optional[str] = None
    submitted_on: Optional[datetime] = None
    published_on: Optional[datetime] = None


class ReviewEvent(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_id: Optional[int] = None
    video_id: int
    event_type: EventType
    reviewer: str
    occurred_on: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    notes: Optional[str] = None


class NotificationLog(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    notification_id: Optional[int] = None
    video_id: int
    recipient_email: str
    sent_on: Optional[datetime] = None
    delivery_status: NotificationDeliveryStatus = NotificationDeliveryStatus.PENDING
    reason: Optional[str] = None
    attempt_count: int = 0
