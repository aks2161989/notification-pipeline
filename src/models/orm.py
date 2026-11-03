"""SQLAlchemy ORM models mirroring the YouTube Content Pipeline Tracker."""

from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    ForeignKey,
    Index,
    UniqueConstraint,
    Text,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def utc_now():
    return datetime.now(timezone.utc)


class ProjectORM(Base):
    __tablename__ = "projects"

    project_id = Column(Integer, primary_key=True)
    project_name = Column(String(255), nullable=False)
    project_type = Column(String(50), default="internal", nullable=False)

    fellows = relationship("FellowORM", back_populates="project")
    pms = relationship("PMORM", back_populates="project")


class PMORM(Base):
    __tablename__ = "pms"

    pm_id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.project_id"), nullable=False)
    full_name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=False)
    role = Column(String(50), default="pm", nullable=False)
    status = Column(String(50), default="active", nullable=False)

    project = relationship("ProjectORM", back_populates="pms")


class FellowORM(Base):
    __tablename__ = "fellows"

    fellow_id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.project_id"), nullable=False)
    full_name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=False)
    status = Column(String(50), default="active", nullable=False)
    display_label = Column(String(255), nullable=True)

    project = relationship("ProjectORM", back_populates="fellows")
    videos = relationship("VideoRecordORM", back_populates="fellow")


class VideoRecordORM(Base):
    __tablename__ = "videos"

    video_id = Column(Integer, primary_key=True)
    fellow_id = Column(Integer, ForeignKey("fellows.fellow_id"), nullable=True)
    title = Column(String(500), nullable=True)
    youtube_link = Column(String(500), nullable=True)
    drive_link = Column(String(500), nullable=True)
    drive_file_id = Column(String(255), nullable=True)
    youtube_video_id = Column(String(255), nullable=True, index=True)
    current_status = Column(String(50), default="submitted", nullable=False, index=True)
    status_note = Column(Text, nullable=True)
    submitted_on = Column(DateTime, nullable=True)
    published_on = Column(DateTime, nullable=True)

    fellow = relationship("FellowORM", back_populates="videos")
    events = relationship("ReviewEventORM", back_populates="video", cascade="all, delete-orphan")
    notifications = relationship("NotificationLogORM", back_populates="video", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_video_status_yt", "current_status", "youtube_video_id"),
    )


class ReviewEventORM(Base):
    __tablename__ = "review_events"

    event_id = Column(Integer, primary_key=True, autoincrement=True)
    video_id = Column(Integer, ForeignKey("videos.video_id"), nullable=False, index=True)
    event_type = Column(String(50), nullable=False)
    reviewer = Column(String(255), nullable=False)
    occurred_on = Column(DateTime, default=utc_now, nullable=False)
    notes = Column(Text, nullable=True)

    video = relationship("VideoRecordORM", back_populates="events")


class NotificationLogORM(Base):
    __tablename__ = "notifications"

    notification_id = Column(Integer, primary_key=True, autoincrement=True)
    video_id = Column(Integer, ForeignKey("videos.video_id"), nullable=False, index=True)
    recipient_email = Column(String(255), nullable=False)
    sent_on = Column(DateTime, nullable=True)
    delivery_status = Column(String(50), default="pending", nullable=False, index=True)
    reason = Column(Text, nullable=True)
    attempt_count = Column(Integer, default=0, nullable=False)

    video = relationship("VideoRecordORM", back_populates="notifications")

    __table_args__ = (
        Index("idx_notif_video_status", "video_id", "delivery_status"),
    )
