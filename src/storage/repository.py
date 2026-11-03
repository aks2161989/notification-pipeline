"""Data access repository for pipeline queries and atomic state updates."""

from datetime import datetime, timezone, timedelta
from typing import Optional, List
from sqlalchemy import select, func, and_
from sqlalchemy.orm import Session, joinedload

from src.models.orm import (
    ProjectORM,
    PMORM,
    FellowORM,
    VideoRecordORM,
    ReviewEventORM,
    NotificationLogORM,
)
from src.models.domain import VideoStatus, NotificationDeliveryStatus, EventType


class PipelineRepository:
    """Repository encapsulating data queries and mutations."""

    def __init__(self, session: Session):
        self.session = session

    # -------------------------------------------------------------------------
    # Projects & Fellows
    # -------------------------------------------------------------------------
    def get_project(self, project_id: int) -> Optional[ProjectORM]:
        return self.session.get(ProjectORM, project_id)

    def list_projects(self) -> List[ProjectORM]:
        return self.session.scalars(select(ProjectORM)).all()

    def get_fellow(self, fellow_id: int) -> Optional[FellowORM]:
        return self.session.get(FellowORM, fellow_id)

    def list_fellows(self) -> List[FellowORM]:
        return self.session.scalars(select(FellowORM)).all()

    # -------------------------------------------------------------------------
    # Videos
    # -------------------------------------------------------------------------
    def get_video(self, video_id: int) -> Optional[VideoRecordORM]:
        stmt = (
            select(VideoRecordORM)
            .options(joinedload(VideoRecordORM.fellow))
            .where(VideoRecordORM.video_id == video_id)
        )
        return self.session.scalars(stmt).first()

    def get_video_by_yt_id(self, youtube_video_id: str) -> Optional[VideoRecordORM]:
        stmt = (
            select(VideoRecordORM)
            .options(joinedload(VideoRecordORM.fellow))
            .where(VideoRecordORM.youtube_video_id == youtube_video_id)
        )
        return self.session.scalars(stmt).first()

    def get_videos_by_status(self, status: VideoStatus | str) -> List[VideoRecordORM]:
        status_val = status.value if isinstance(status, VideoStatus) else status
        stmt = (
            select(VideoRecordORM)
            .options(joinedload(VideoRecordORM.fellow))
            .where(VideoRecordORM.current_status == status_val)
        )
        return self.session.scalars(stmt).all()

    def list_all_videos(self) -> List[VideoRecordORM]:
        stmt = select(VideoRecordORM).options(joinedload(VideoRecordORM.fellow))
        return self.session.scalars(stmt).all()

    def update_video_status(
        self,
        video_id: int,
        status: VideoStatus | str,
        published_on: Optional[datetime] = None,
        note: Optional[str] = None,
    ) -> Optional[VideoRecordORM]:
        video = self.get_video(video_id)
        if not video:
            return None
        
        status_val = status.value if isinstance(status, VideoStatus) else status
        video.current_status = status_val
        if published_on is not None:
            video.published_on = published_on
        if note is not None:
            video.status_note = note
        self.session.flush()
        return video

    # -------------------------------------------------------------------------
    # Review Events
    # -------------------------------------------------------------------------
    def record_review_event(
        self,
        video_id: int,
        event_type: EventType | str,
        reviewer: str,
        notes: Optional[str] = None,
        occurred_on: Optional[datetime] = None,
    ) -> ReviewEventORM:
        event_val = event_type.value if isinstance(event_type, EventType) else event_type
        event = ReviewEventORM(
            video_id=video_id,
            event_type=event_val,
            reviewer=reviewer,
            occurred_on=occurred_on or datetime.now(timezone.utc),
            notes=notes,
        )
        self.session.add(event)
        self.session.flush()
        return event

    def get_events_for_video(self, video_id: int) -> List[ReviewEventORM]:
        stmt = (
            select(ReviewEventORM)
            .where(ReviewEventORM.video_id == video_id)
            .order_by(ReviewEventORM.occurred_on.asc())
        )
        return self.session.scalars(stmt).all()

    # -------------------------------------------------------------------------
    # Notifications & Idempotency Guards
    # -------------------------------------------------------------------------
    def get_active_or_sent_notification(self, video_id: int) -> Optional[NotificationLogORM]:
        """Check if a notification is in-flight (sending) or already sent.
        
        This is the primary idempotency guard preventing duplicate email dispatch.
        """
        stmt = (
            select(NotificationLogORM)
            .where(
                and_(
                    NotificationLogORM.video_id == video_id,
                    NotificationLogORM.delivery_status.in_([
                        NotificationDeliveryStatus.SENDING.value,
                        NotificationDeliveryStatus.SENT.value,
                    ]),
                )
            )
            .order_by(NotificationLogORM.notification_id.desc())
        )
        return self.session.scalars(stmt).first()

    def get_notifications_for_video(self, video_id: int) -> List[NotificationLogORM]:
        stmt = (
            select(NotificationLogORM)
            .where(NotificationLogORM.video_id == video_id)
            .order_by(NotificationLogORM.notification_id.desc())
        )
        return self.session.scalars(stmt).all()

    def get_stranded_sending_notifications(self, older_than_minutes: int = 15) -> List[NotificationLogORM]:
        """Detect notifications stranded in SENDING state (potential process crashes)."""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=older_than_minutes)
        stmt = (
            select(NotificationLogORM)
            .join(NotificationLogORM.video)
            .where(
                and_(
                    NotificationLogORM.delivery_status == NotificationDeliveryStatus.SENDING.value,
                    # Fallback to current time if sent_on is not yet set
                    func.coalesce(NotificationLogORM.sent_on, cutoff) <= cutoff,
                )
            )
        )
        return self.session.scalars(stmt).all()

    def get_retryable_failed_notifications(self, max_attempts: int = 3) -> List[NotificationLogORM]:
        """Fetch failed notifications that have not exceeded the retry threshold."""
        stmt = (
            select(NotificationLogORM)
            .options(joinedload(NotificationLogORM.video).joinedload(VideoRecordORM.fellow))
            .where(
                and_(
                    NotificationLogORM.delivery_status == NotificationDeliveryStatus.FAILED.value,
                    NotificationLogORM.attempt_count < max_attempts,
                )
            )
        )
        return self.session.scalars(stmt).all()

    def create_notification(
        self,
        video_id: int,
        recipient_email: str,
        delivery_status: NotificationDeliveryStatus | str = NotificationDeliveryStatus.PENDING,
        reason: Optional[str] = None,
    ) -> NotificationLogORM:
        status_val = delivery_status.value if isinstance(delivery_status, NotificationDeliveryStatus) else delivery_status
        notif = NotificationLogORM(
            video_id=video_id,
            recipient_email=recipient_email,
            delivery_status=status_val,
            reason=reason,
            sent_on=datetime.now(timezone.utc) if status_val == NotificationDeliveryStatus.SENT.value else None,
            attempt_count=1 if status_val in (NotificationDeliveryStatus.SENDING.value, NotificationDeliveryStatus.SENT.value) else 0,
        )
        self.session.add(notif)
        self.session.flush()
        return notif

    def update_notification_status(
        self,
        notification_id: int,
        delivery_status: NotificationDeliveryStatus | str,
        reason: Optional[str] = None,
        increment_attempt: bool = False,
    ) -> Optional[NotificationLogORM]:
        notif = self.session.get(NotificationLogORM, notification_id)
        if not notif:
            return None
        
        status_val = delivery_status.value if isinstance(delivery_status, NotificationDeliveryStatus) else delivery_status
        notif.delivery_status = status_val
        if reason:
            notif.reason = reason
        if status_val == NotificationDeliveryStatus.SENT.value and not notif.sent_on:
            notif.sent_on = datetime.now(timezone.utc)
        if increment_attempt:
            notif.attempt_count += 1
        self.session.flush()
        return notif

    # -------------------------------------------------------------------------
    # Dashboard / Reporting Metrics
    # -------------------------------------------------------------------------
    def get_metrics(self) -> dict:
        """Return aggregate counts across all pipeline tables."""
        total_projects = self.session.scalar(select(func.count(ProjectORM.project_id))) or 0
        total_fellows = self.session.scalar(select(func.count(FellowORM.fellow_id))) or 0
        total_videos = self.session.scalar(select(func.count(VideoRecordORM.video_id))) or 0
        queued_videos = self.session.scalar(
            select(func.count(VideoRecordORM.video_id)).where(VideoRecordORM.current_status == "queued")
        ) or 0
        published_videos = self.session.scalar(
            select(func.count(VideoRecordORM.video_id)).where(VideoRecordORM.current_status == "published")
        ) or 0
        sent_notifications = self.session.scalar(
            select(func.count(NotificationLogORM.notification_id)).where(NotificationLogORM.delivery_status == "sent")
        ) or 0
        failed_notifications = self.session.scalar(
            select(func.count(NotificationLogORM.notification_id)).where(NotificationLogORM.delivery_status == "failed")
        ) or 0

        return {
            "total_projects": total_projects,
            "total_fellows": total_fellows,
            "total_videos": total_videos,
            "queued_videos": queued_videos,
            "published_videos": published_videos,
            "sent_notifications": sent_notifications,
            "failed_notifications": failed_notifications,
        }
