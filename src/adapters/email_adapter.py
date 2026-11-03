"""Notification dispatcher, email templating, and pluggable delivery sinks."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Optional, Dict, List
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from src.config import settings, PipelineMode
from src.models.domain import NotificationDeliveryStatus


@dataclass
class EmailNotification:
    """Strongly typed email notification payload."""
    video_id: int
    fellow_id: int
    fellow_name: str
    recipient_email: str
    intended_recipient: str
    video_title: str
    youtube_video_id: str
    youtube_url: str
    project_name: str
    subject: str
    body_text: str
    body_html: str
    mode: PipelineMode
    headers: Dict[str, str] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class DispatchResult:
    """Outcome of an email dispatch operation."""
    success: bool
    delivery_status: NotificationDeliveryStatus
    actual_recipient: str
    reason: str
    sent_on: Optional[datetime] = None


class BaseEmailSink(ABC):
    """Abstract sink for delivering or recording emails."""

    @abstractmethod
    def deliver(self, notification: EmailNotification) -> bool:
        """Deliver or record the notification. Returns True if successful."""
        pass


class ConsoleSink(BaseEmailSink):
    """Prints formatted email to terminal stdout."""

    def deliver(self, notification: EmailNotification) -> bool:
        print("\n" + "─" * 70)
        print(f" [DISPATCH SINK: CONSOLE] Mode: {notification.mode.value}")
        print(f"  To        : {notification.recipient_email}")
        if notification.recipient_email != notification.intended_recipient:
            print(f"  (Original): {notification.intended_recipient} [REDIRECTED]")
        print(f"  Subject   : {notification.subject}")
        print(f"  YouTube   : {notification.youtube_url}")
        print("─" * 70)
        print(notification.body_text.strip())
        print("─" * 70 + "\n")
        return True


class FileOutboxSink(BaseEmailSink):
    """Saves email previews as .json and .html files in outbox/ for local verification."""

    def __init__(self, outbox_dir: Optional[Path] = None):
        self.outbox_dir = outbox_dir or settings.OUTBOX_DIR
        self.outbox_dir.mkdir(parents=True, exist_ok=True)

    def deliver(self, notification: EmailNotification) -> bool:
        timestamp_str = notification.created_at.strftime("%Y%m%d_%H%M%S")
        base_name = f"video_{notification.video_id}_{timestamp_str}"

        # 1. Save JSON metadata
        json_path = self.outbox_dir / f"{base_name}.json"
        meta = {
            "video_id": notification.video_id,
            "fellow_id": notification.fellow_id,
            "fellow_name": notification.fellow_name,
            "recipient_email": notification.recipient_email,
            "intended_recipient": notification.intended_recipient,
            "subject": notification.subject,
            "youtube_url": notification.youtube_url,
            "mode": notification.mode.value,
            "created_at": notification.created_at.isoformat(),
            "headers": notification.headers,
        }
        json_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

        # 2. Save HTML preview
        html_path = self.outbox_dir / f"{base_name}.html"
        html_path.write_text(notification.body_html, encoding="utf-8")
        return True


class SMTPSink(BaseEmailSink):
    """Live SMTP delivery sink (Gmail, Amazon SES, or custom SMTP)."""

    def __init__(self, host: str = "smtp.gmail.com", port: int = 587, username: str = "", password: str = ""):
        self.host = host
        self.port = port
        self.username = username
        self.password = password

    def deliver(self, notification: EmailNotification) -> bool:
        if not self.username or not self.password:
            raise ValueError("SMTP credentials not configured.")

        msg = MIMEMultipart("alternative")
        msg["Subject"] = notification.subject
        msg["From"] = f"Humanitarians AI <{self.username}>"
        msg["To"] = notification.recipient_email
        for k, v in notification.headers.items():
            msg[k] = v

        msg.attach(MIMEText(notification.body_text, "plain"))
        msg.attach(MIMEText(notification.body_html, "html"))

        with smtplib.SMTP(self.host, self.port) as server:
            server.starttls()
            server.login(self.username, self.password)
            server.send_message(msg)
        return True


class EmailFormatter:
    """Renders professional Humanitarians AI email templates."""

    @staticmethod
    def render(
        video_id: int,
        fellow_id: int,
        fellow_name: str,
        fellow_email: str,
        video_title: str,
        youtube_video_id: str,
        project_name: str,
        mode: PipelineMode,
        redirect_to: Optional[str] = None,
    ) -> EmailNotification:
        youtube_url = f"https://www.youtube.com/watch?v=RzW17zhV4Nw" if youtube_video_id == "RzW17zhV4Nw" else f"https://www.youtube.com/watch?v={youtube_video_id}"
        
        # Recipient determination based on mode
        actual_recipient = fellow_email
        if mode == PipelineMode.REDIRECTED:
            actual_recipient = redirect_to or settings.REDIRECT_TEST_EMAIL

        # Subject line
        subject_prefix = ""
        if mode == PipelineMode.DRY_RUN:
            subject_prefix = "[DRY-RUN] "
        elif mode == PipelineMode.REDIRECTED:
            subject_prefix = "[TEST-REDIRECT] "
        
        subject = f"{subject_prefix}🎉 Your video \"{video_title}\" is now live on Humanitarians AI YouTube!"

        # Plain text version
        body_text = f"""
Hello {fellow_name},

Great news! Your video contribution for the {project_name} project has completed editorial review and is now officially published on the Humanitarians AI YouTube channel.

Video Title: {video_title}
Watch Link  : {youtube_url}

Thank you for your dedication and impactful work. Feel free to share the link with your professional network and community!

Best regards,
Humanitarians AI Content & Production Team
https://humanitarians.ai
""".strip()

        # HTML responsive version
        body_html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; color: #1e293b; padding: 20px; }}
    .container {{ max-width: 580px; margin: 0 auto; background: #ffffff; border-radius: 8px; border: 1px solid #e2e8f0; overflow: hidden; }}
    .header {{ background-color: #0f172a; color: #ffffff; padding: 24px; text-align: center; }}
    .header h1 {{ margin: 0; font-size: 20px; font-weight: 600; letter-spacing: -0.5px; }}
    .content {{ padding: 24px; line-height: 1.6; font-size: 15px; }}
    .badge {{ display: inline-block; background-color: #dbeafe; color: #1e40af; font-size: 12px; font-weight: 600; padding: 4px 10px; border-radius: 12px; margin-bottom: 12px; }}
    .video-card {{ background-color: #f1f5f9; border-left: 4px solid #2563eb; padding: 16px; margin: 20px 0; border-radius: 4px; }}
    .btn {{ display: inline-block; background-color: #ef4444; color: #ffffff !important; text-decoration: none; font-weight: 600; padding: 12px 24px; border-radius: 6px; margin-top: 10px; }}
    .footer {{ background-color: #f8fafc; border-top: 1px solid #e2e8f0; padding: 16px 24px; font-size: 12px; color: #64748b; text-align: center; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <h1>Humanitarians AI</h1>
    </div>
    <div class="content">
      <span class="badge">{project_name} Fellow Contribution</span>
      <p>Hello <strong>{fellow_name}</strong>,</p>
      <p>Your submitted video has successfully passed review and is now <strong>publicly live on YouTube</strong>!</p>
      
      <div class="video-card">
        <h3 style="margin: 0 0 8px 0; font-size: 16px; color: #0f172a;">{video_title}</h3>
        <p style="margin: 0; font-size: 13px; color: #475569;">YouTube Video ID: <code>{youtube_video_id}</code></p>
        <p style="margin-top: 12px;"><a href="{youtube_url}" class="btn" target="_blank">▶ Watch on YouTube</a></p>
      </div>

      <p>Thank you for contributing to open humanitarian technology. You can now share this publication on LinkedIn and add it to your portfolio!</p>
    </div>
    <div class="footer">
      <p>Humanitarians AI Publication Notification Pipeline &bull; Automated Delivery</p>
      <p>Intended Fellow: {fellow_email} &bull; Delivery Mode: {mode.value}</p>
    </div>
  </div>
</body>
</html>"""

        headers = {
            "X-Pipeline-Mode": mode.value,
            "X-Video-ID": str(video_id),
            "X-Fellow-ID": str(fellow_id),
            "X-Original-Recipient": fellow_email,
        }

        return EmailNotification(
            video_id=video_id,
            fellow_id=fellow_id,
            fellow_name=fellow_name,
            recipient_email=actual_recipient,
            intended_recipient=fellow_email,
            video_title=video_title,
            youtube_video_id=youtube_video_id,
            youtube_url=youtube_url,
            project_name=project_name,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            mode=mode,
            headers=headers,
        )


class NotificationDispatcher:
    """Coordinates formatting and multi-sink delivery according to rollout mode."""

    def __init__(self, sinks: Optional[List[BaseEmailSink]] = None):
        self.sinks = sinks or [ConsoleSink(), FileOutboxSink()]

    def dispatch(
        self,
        video_id: int,
        fellow_id: int,
        fellow_name: str,
        fellow_email: str,
        fellow_status: str,
        video_title: str,
        youtube_video_id: str,
        project_name: str,
        mode: Optional[PipelineMode] = None,
        redirect_to: Optional[str] = None,
    ) -> DispatchResult:
        eff_mode = mode or settings.PIPELINE_MODE

        # 1. Fellow data validation check
        if fellow_status.lower() != "active":
            return DispatchResult(
                success=False,
                delivery_status=NotificationDeliveryStatus.SKIPPED,
                actual_recipient=fellow_email,
                reason=f"Fellow status is '{fellow_status}'; notification skipped",
            )
        
        if not fellow_email or "@" not in fellow_email:
            return DispatchResult(
                success=False,
                delivery_status=NotificationDeliveryStatus.FAILED,
                actual_recipient=fellow_email or "EMPTY",
                reason="Invalid fellow email address",
            )

        # 2. Canary / Cohort filter check
        if eff_mode == PipelineMode.COHORT:
            if fellow_id not in settings.COHORT_FELLOW_IDS:
                return DispatchResult(
                    success=False,
                    delivery_status=NotificationDeliveryStatus.SKIPPED,
                    actual_recipient=fellow_email,
                    reason=f"Fellow ID {fellow_id} not in canary cohort {settings.COHORT_FELLOW_IDS}",
                )

        # 3. Format message
        notification = EmailFormatter.render(
            video_id=video_id,
            fellow_id=fellow_id,
            fellow_name=fellow_name,
            fellow_email=fellow_email,
            video_title=video_title,
            youtube_video_id=youtube_video_id,
            project_name=project_name,
            mode=eff_mode,
            redirect_to=redirect_to,
        )

        # 4. Dry-run mode check
        if eff_mode == PipelineMode.DRY_RUN:
            # Deliver to local outbox / console for audit inspection, but report as dry-run processed
            for sink in self.sinks:
                sink.deliver(notification)
            return DispatchResult(
                success=True,
                delivery_status=NotificationDeliveryStatus.SKIPPED,
                actual_recipient=notification.recipient_email,
                reason="Dry-run mode evaluated successfully (send bypassed)",
                sent_on=None,
            )

        # 5. Live / Redirected delivery across sinks
        try:
            for sink in self.sinks:
                sink.deliver(notification)
            return DispatchResult(
                success=True,
                delivery_status=NotificationDeliveryStatus.SENT,
                actual_recipient=notification.recipient_email,
                reason="Email successfully dispatched",
                sent_on=datetime.now(timezone.utc),
            )
        except Exception as e:
            return DispatchResult(
                success=False,
                delivery_status=NotificationDeliveryStatus.FAILED,
                actual_recipient=notification.recipient_email,
                reason=f"Delivery exception: {str(e)}",
            )
