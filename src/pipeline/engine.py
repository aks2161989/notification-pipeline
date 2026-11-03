"""Reconciliation engine coordinating YouTube polling, idempotency guards, and state transitions."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, List, Callable
import logging

from src.config import settings, PipelineMode
from src.models.domain import (
    VideoStatus,
    NotificationDeliveryStatus,
    EventType,
)
from src.storage.database import get_session
from src.storage.repository import PipelineRepository
from src.adapters.youtube_adapter import BaseYouTubeAdapter, get_youtube_adapter
from src.adapters.email_adapter import NotificationDispatcher, DispatchResult
from src.pipeline.lock import ExecutionLock, LockAcquisitionError

logger = logging.getLogger("notification_pipeline")


@dataclass
class VideoProcessingResult:
    """Outcome of processing an individual video."""
    video_id: int
    youtube_video_id: str
    action_taken: str        # 'PUBLISHED_NOTIFIED', 'SKIPPED_DUPLICATE', 'SKIPPED_NOT_PUBLIC', 'FAILED', 'DRY_RUN'
    fellow_email: Optional[str] = None
    notification_id: Optional[int] = None
    detail: Optional[str] = None


@dataclass
class ReconciliationReport:
    """Summary report for an execution run."""
    run_timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    mode: PipelineMode = PipelineMode.DRY_RUN
    videos_inspected: int = 0
    new_publications_detected: int = 0
    notifications_sent: int = 0
    duplicates_prevented: int = 0
    notifications_skipped: int = 0
    failures: int = 0
    processed_items: List[VideoProcessingResult] = field(default_factory=list)


class PipelineEngine:
    """Core workflow engine enforcing atomic state transitions and idempotency."""

    def __init__(
        self,
        youtube_adapter: Optional[BaseYouTubeAdapter] = None,
        dispatcher: Optional[NotificationDispatcher] = None,
        lock: Optional[ExecutionLock] = None,
        session_factory: Optional[Callable] = None,
    ):
        self.youtube_adapter = youtube_adapter or get_youtube_adapter()
        self.dispatcher = dispatcher or NotificationDispatcher()
        self.lock = lock or ExecutionLock()
        self.session_factory = session_factory or get_session

    def run_reconciliation(
        self,
        mode: Optional[PipelineMode] = None,
        redirect_to: Optional[str] = None,
        bypass_lock: bool = False,
    ) -> ReconciliationReport:
        """Execute scheduled reconciliation run with mutual exclusion lock."""
        eff_mode = mode or settings.PIPELINE_MODE
        report = ReconciliationReport(mode=eff_mode)

        # 1. Acquire execution concurrency lock
        if not bypass_lock:
            try:
                self.lock.acquire()
            except LockAcquisitionError as e:
                logger.warning(f"Aborting execution: {e}")
                report.processed_items.append(VideoProcessingResult(
                    video_id=0,
                    youtube_video_id="N/A",
                    action_taken="ABORTED_LOCK_COLLISION",
                    detail=str(e),
                ))
                return report

        try:
            with self.session_factory() as session:
                repo = PipelineRepository(session)
                
                # 2. Query videos currently in 'queued' status
                queued_videos = repo.get_videos_by_status(VideoStatus.QUEUED)
                report.videos_inspected = len(queued_videos)

                for video in queued_videos:
                    item_res = self._process_single_video(
                        video=video,
                        repo=repo,
                        mode=eff_mode,
                        redirect_to=redirect_to,
                    )
                    report.processed_items.append(item_res)

                    if item_res.action_taken == "PUBLISHED_NOTIFIED":
                        report.new_publications_detected += 1
                        report.notifications_sent += 1
                    elif item_res.action_taken == "DRY_RUN":
                        report.new_publications_detected += 1
                        report.notifications_skipped += 1
                    elif item_res.action_taken == "SKIPPED_DUPLICATE":
                        report.duplicates_prevented += 1
                    elif item_res.action_taken == "SKIPPED_CANARY" or item_res.action_taken == "SKIPPED_INACTIVE":
                        report.notifications_skipped += 1
                    elif item_res.action_taken == "FAILED":
                        report.failures += 1

        finally:
            if not bypass_lock:
                self.lock.release()

        return report

    def _process_single_video(
        self,
        video,
        repo: PipelineRepository,
        mode: PipelineMode,
        redirect_to: Optional[str] = None,
    ) -> VideoProcessingResult:
        yt_id = video.youtube_video_id
        if not yt_id:
            return VideoProcessingResult(
                video_id=video.video_id,
                youtube_video_id="NONE",
                action_taken="FAILED",
                detail="Missing YouTube Video ID in video record",
            )

        # 1. Check YouTube publication status via adapter
        is_public = self.youtube_adapter.is_video_public(yt_id)
        if not is_public:
            return VideoProcessingResult(
                video_id=video.video_id,
                youtube_video_id=yt_id,
                action_taken="SKIPPED_NOT_PUBLIC",
                detail="Video is still in Queue (draft/unlisted/private)",
            )

        # 2. Idempotency Guard: Check if notification row exists with 'sending' or 'sent'
        existing_notif = repo.get_active_or_sent_notification(video.video_id)
        if existing_notif:
            return VideoProcessingResult(
                video_id=video.video_id,
                youtube_video_id=yt_id,
                action_taken="SKIPPED_DUPLICATE",
                fellow_email=existing_notif.recipient_email,
                notification_id=existing_notif.notification_id,
                detail=f"Notification already recorded with delivery_status='{existing_notif.delivery_status}'",
            )

        # 3. Resolve fellow & project metadata
        fellow = video.fellow
        if not fellow:
            return VideoProcessingResult(
                video_id=video.video_id,
                youtube_video_id=yt_id,
                action_taken="FAILED",
                detail="Video record is not linked to any Fellow",
            )

        project_name = fellow.project.project_name if fellow.project else "Humanitarians AI"

        # 4. Two-Phase State Machine
        # STEP 4A: Pre-commit SENDING record to DB *before* network call
        notif = repo.create_notification(
            video_id=video.video_id,
            recipient_email=fellow.email,
            delivery_status=NotificationDeliveryStatus.SENDING,
            reason=f"Video became public on YouTube ({yt_id})",
        )

        # Update video status to PUBLISHED
        now = datetime.now(timezone.utc)
        repo.update_video_status(
            video_id=video.video_id,
            status=VideoStatus.PUBLISHED,
            published_on=now,
        )
        repo.record_review_event(
            video_id=video.video_id,
            event_type=EventType.PUBLISHED,
            reviewer="youtube_poller",
            notes=f"Auto-detected publication on YouTube (ID: {yt_id})",
            occurred_on=now,
        )

        # STEP 4B: Dispatch email notification
        dispatch_result: DispatchResult = self.dispatcher.dispatch(
            video_id=video.video_id,
            fellow_id=fellow.fellow_id,
            fellow_name=fellow.full_name,
            fellow_email=fellow.email,
            fellow_status=fellow.status,
            video_title=video.title or f"Video {video.video_id}",
            youtube_video_id=yt_id,
            project_name=project_name,
            mode=mode,
            redirect_to=redirect_to,
        )

        # STEP 4C: Update notification state to SENT, FAILED, or SKIPPED
        repo.update_notification_status(
            notification_id=notif.notification_id,
            delivery_status=dispatch_result.delivery_status,
            reason=dispatch_result.reason,
        )

        if mode == PipelineMode.DRY_RUN:
            action = "DRY_RUN"
        elif dispatch_result.delivery_status == NotificationDeliveryStatus.SENT:
            action = "PUBLISHED_NOTIFIED"
        elif dispatch_result.delivery_status == NotificationDeliveryStatus.SKIPPED:
            action = "SKIPPED_CANARY" if "cohort" in dispatch_result.reason else "SKIPPED_INACTIVE"
        else:
            action = "FAILED"

        return VideoProcessingResult(
            video_id=video.video_id,
            youtube_video_id=yt_id,
            action_taken=action,
            fellow_email=dispatch_result.actual_recipient,
            notification_id=notif.notification_id,
            detail=dispatch_result.reason,
        )
