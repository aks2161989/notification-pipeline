"""Operational reliability: retry engine, health monitoring, and stranded record recovery."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
import logging

from src.config import settings, PipelineMode
from src.models.domain import NotificationDeliveryStatus, VideoStatus
from src.storage.database import get_session
from src.storage.repository import PipelineRepository
from src.adapters.email_adapter import NotificationDispatcher, DispatchResult

logger = logging.getLogger("notification_pipeline.recovery")


@dataclass
class HealthIssue:
    severity: str          # 'CRITICAL', 'WARNING', 'INFO'
    category: str          # 'DATA_QUALITY', 'STRANDED_JOB', 'RETRY_EXHAUSTED', 'LOCK'
    description: str
    record_id: Optional[int] = None


@dataclass
class HealthReport:
    healthy: bool
    inspected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    issues: List[HealthIssue] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RetryReport:
    retried_count: int = 0
    succeeded_count: int = 0
    exhausted_count: int = 0
    details: List[str] = field(default_factory=list)


class OperationalHealthService:
    """Monitors pipeline data health, stranded jobs, and data quality exceptions."""

    def __init__(self, session_factory=None):
        self.session_factory = session_factory or get_session

    def check_health(self) -> HealthReport:
        issues = []
        stats = {}

        with self.session_factory() as session:
            repo = PipelineRepository(session)
            metrics = repo.get_metrics()
            stats.update(metrics)

            # 1. Check for stranded SENDING notifications (process crashes)
            stranded = repo.get_stranded_sending_notifications(older_than_minutes=10)
            if stranded:
                for n in stranded:
                    issues.append(HealthIssue(
                        severity="WARNING",
                        category="STRANDED_JOB",
                        description=f"Notification #{n.notification_id} for Video #{n.video_id} has been stranded in SENDING state.",
                        record_id=n.notification_id,
                    ))

            # 2. Check for exhausted retries (failed >= 3 times)
            all_videos = repo.list_all_videos()
            for v in all_videos:
                notifs = repo.get_notifications_for_video(v.video_id)
                for n in notifs:
                    if n.delivery_status == NotificationDeliveryStatus.FAILED.value and n.attempt_count >= 3:
                        issues.append(HealthIssue(
                            severity="CRITICAL",
                            category="RETRY_EXHAUSTED",
                            description=f"Notification #{n.notification_id} for Video #{v.video_id} ({v.title}) exhausted 3 delivery attempts. Reason: {n.reason}",
                            record_id=n.notification_id,
                        ))

            # 3. Check for data quality anomalies: videos without fellows or missing emails
            for v in all_videos:
                if not v.fellow_id:
                    issues.append(HealthIssue(
                        severity="WARNING",
                        category="DATA_QUALITY",
                        description=f"Video #{v.video_id} ('{v.title}') has no mapped Fellow ID.",
                        record_id=v.video_id,
                    ))
                elif v.fellow and (not v.fellow.email or "@" not in v.fellow.email):
                    issues.append(HealthIssue(
                        severity="CRITICAL",
                        category="DATA_QUALITY",
                        description=f"Fellow #{v.fellow.fellow_id} ({v.fellow.full_name}) has invalid/missing email.",
                        record_id=v.fellow.fellow_id,
                    ))

        is_healthy = not any(i.severity == "CRITICAL" for i in issues)
        return HealthReport(healthy=is_healthy, issues=issues, stats=stats)


class RetryService:
    """Handles controlled retries for transient failure states."""

    def __init__(self, dispatcher: Optional[NotificationDispatcher] = None, session_factory=None):
        self.dispatcher = dispatcher or NotificationDispatcher()
        self.session_factory = session_factory or get_session

    def retry_failed(
        self,
        max_attempts: int = 3,
        mode: Optional[PipelineMode] = None,
        redirect_to: Optional[str] = None,
    ) -> RetryReport:
        eff_mode = mode or settings.PIPELINE_MODE
        report = RetryReport()

        with self.session_factory() as session:
            repo = PipelineRepository(session)
            retryable = repo.get_retryable_failed_notifications(max_attempts=max_attempts)
            report.retried_count = len(retryable)

            for notif in retryable:
                video = notif.video
                fellow = video.fellow if video else None
                if not fellow:
                    continue

                project_name = fellow.project.project_name if fellow.project else "Humanitarians AI"
                dispatch_res: DispatchResult = self.dispatcher.dispatch(
                    video_id=video.video_id,
                    fellow_id=fellow.fellow_id,
                    fellow_name=fellow.full_name,
                    fellow_email=fellow.email,
                    fellow_status=fellow.status,
                    video_title=video.title or f"Video {video.video_id}",
                    youtube_video_id=video.youtube_video_id or "UNKNOWN",
                    project_name=project_name,
                    mode=eff_mode,
                    redirect_to=redirect_to,
                )

                if dispatch_res.success and dispatch_res.delivery_status == NotificationDeliveryStatus.SENT:
                    repo.update_notification_status(
                        notification_id=notif.notification_id,
                        delivery_status=NotificationDeliveryStatus.SENT,
                        reason="Successfully delivered on retry",
                        increment_attempt=True,
                    )
                    report.succeeded_count += 1
                    report.details.append(f"Notification #{notif.notification_id} (Video #{video.video_id}) succeeded on retry.")
                else:
                    repo.update_notification_status(
                        notification_id=notif.notification_id,
                        delivery_status=NotificationDeliveryStatus.FAILED,
                        reason=f"Retry failed: {dispatch_res.reason}",
                        increment_attempt=True,
                    )
                    if notif.attempt_count + 1 >= max_attempts:
                        report.exhausted_count += 1
                        report.details.append(f"Notification #{notif.notification_id} (Video #{video.video_id}) exhausted all {max_attempts} attempts.")
                    else:
                        report.details.append(f"Notification #{notif.notification_id} failed attempt {notif.attempt_count + 1}/{max_attempts}.")

        return report
