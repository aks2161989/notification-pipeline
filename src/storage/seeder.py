"""Database seeder loading data from YouTube_Pipeline_Tracker.xlsx or synthetic seed."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import openpyxl
from sqlalchemy.orm import Session

from src.models.orm import (
    ProjectORM,
    PMORM,
    FellowORM,
    VideoRecordORM,
    ReviewEventORM,
    NotificationLogORM,
)


def _excel_date_to_datetime(val: Any) -> datetime | None:
    """Convert Excel serial date or ISO string to datetime."""
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        return val
    try:
        # Excel serial date offset from Dec 30, 1899
        num_val = float(val)
        return datetime(1899, 12, 30) + timedelta(days=num_val)
    except (ValueError, TypeError):
        pass
    try:
        return datetime.fromisoformat(str(val))
    except Exception:
        return None


def _to_int(val: Any) -> int | None:
    if val is None or val == "":
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def seed_from_excel(xlsx_path: Path, session: Session) -> dict[str, int]:
    """Load tables directly from YouTube_Pipeline_Tracker.xlsx."""
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    counts = {"projects": 0, "pms": 0, "fellows": 0, "videos": 0, "review_events": 0, "notifications": 0}

    # 1. Projects
    if "projects" in wb.sheetnames:
        ws = wb["projects"]
        rows = list(ws.iter_rows(values_only=True))
        if len(rows) > 1:
            header = [str(c).strip().lower() for c in rows[0]]
            for r in rows[1:]:
                if not any(r) or r[0] is None:
                    continue
                row_dict = dict(zip(header, r))
                pid = _to_int(row_dict.get("project_id"))
                if pid is not None and not session.get(ProjectORM, pid):
                    session.add(ProjectORM(
                        project_id=pid,
                        project_name=str(row_dict.get("project_name", f"Project {pid}")),
                        project_type=str(row_dict.get("project_type", "internal")),
                    ))
                    counts["projects"] += 1
            session.flush()

    # 2. PMs
    if "pms" in wb.sheetnames:
        ws = wb["pms"]
        rows = list(ws.iter_rows(values_only=True))
        if len(rows) > 1:
            header = [str(c).strip().lower() for c in rows[0]]
            for r in rows[1:]:
                if not any(r) or r[0] is None:
                    continue
                row_dict = dict(zip(header, r))
                pm_id = _to_int(row_dict.get("pm_id"))
                if pm_id is not None and not session.get(PMORM, pm_id):
                    session.add(PMORM(
                        pm_id=pm_id,
                        project_id=_to_int(row_dict.get("project_id")) or 1,
                        full_name=str(row_dict.get("full_name", f"PM {pm_id}")),
                        email=str(row_dict.get("email", f"pm{pm_id}@humanitarians.ai")),
                        role=str(row_dict.get("role", "pm")),
                        status=str(row_dict.get("status", "active")),
                    ))
                    counts["pms"] += 1
            session.flush()

    # 3. Fellows
    if "fellows" in wb.sheetnames:
        ws = wb["fellows"]
        rows = list(ws.iter_rows(values_only=True))
        if len(rows) > 1:
            header = [str(c).strip().lower() for c in rows[0]]
            for r in rows[1:]:
                if not any(r) or r[0] is None:
                    continue
                row_dict = dict(zip(header, r))
                fid = _to_int(row_dict.get("fellow_id"))
                if fid is not None and not session.get(FellowORM, fid):
                    session.add(FellowORM(
                        fellow_id=fid,
                        project_id=_to_int(row_dict.get("project_id")) or 1,
                        full_name=str(row_dict.get("full_name", f"Fellow {fid}")),
                        email=str(row_dict.get("email", f"fellow{fid}@northeastern.edu")),
                        status=str(row_dict.get("status", "active")),
                        display_label=str(row_dict.get("display_label", "")),
                    ))
                    counts["fellows"] += 1
            session.flush()

    # 4. Videos
    if "videos" in wb.sheetnames:
        ws = wb["videos"]
        rows = list(ws.iter_rows(values_only=True))
        if len(rows) > 1:
            header = [str(c).strip().lower() for c in rows[0]]
            for r in rows[1:]:
                if not any(r) or r[0] is None:
                    continue
                row_dict = dict(zip(header, r))
                vid = _to_int(row_dict.get("video_id"))
                if vid is not None and not session.get(VideoRecordORM, vid):
                    session.add(VideoRecordORM(
                        video_id=vid,
                        fellow_id=_to_int(row_dict.get("fellow_id")),
                        title=str(row_dict.get("title")) if row_dict.get("title") else f"Video {vid}",
                        youtube_link=str(row_dict.get("youtube_link")) if row_dict.get("youtube_link") else None,
                        drive_link=str(row_dict.get("drive_link")) if row_dict.get("drive_link") else None,
                        drive_file_id=str(row_dict.get("drive_file_id")) if row_dict.get("drive_file_id") else f"drive_file_{vid}",
                        youtube_video_id=str(row_dict.get("youtube_video_id")) if row_dict.get("youtube_video_id") else f"yt_id_{vid}",
                        current_status=str(row_dict.get("current_status", "submitted")),
                        status_note=str(row_dict.get("status_note", "")) if row_dict.get("status_note") else None,
                        submitted_on=_excel_date_to_datetime(row_dict.get("submitted_on")),
                        published_on=_excel_date_to_datetime(row_dict.get("published_on")),
                    ))
                    counts["videos"] += 1
            session.flush()

    # 5. Review Events
    if "review_events" in wb.sheetnames:
        ws = wb["review_events"]
        rows = list(ws.iter_rows(values_only=True))
        if len(rows) > 1:
            header = [str(c).strip().lower() for c in rows[0]]
            for r in rows[1:]:
                if not any(r) or r[0] is None:
                    continue
                row_dict = dict(zip(header, r))
                eid = _to_int(row_dict.get("event_id"))
                vid = _to_int(row_dict.get("video_id"))
                if eid is not None and vid is not None and not session.get(ReviewEventORM, eid):
                    session.add(ReviewEventORM(
                        event_id=eid,
                        video_id=vid,
                        event_type=str(row_dict.get("event_type", "submitted")),
                        reviewer=str(row_dict.get("reviewer", "system")),
                        occurred_on=_excel_date_to_datetime(row_dict.get("occurred_on")) or datetime.now(timezone.utc),
                        notes=str(row_dict.get("notes", "")) if row_dict.get("notes") else None,
                    ))
                    counts["review_events"] += 1
            session.flush()

    return counts


def seed_synthetic_scenarios(session: Session) -> dict[str, int]:
    """Add realistic multi-fellow test scenarios covering all workflow edge cases."""
    # Ensure projects exist
    p_names = ["Madison", "NeuroVEP", "Lyrical Literacy", "Medhavi"]
    for i, name in enumerate(p_names, start=1):
        if not session.get(ProjectORM, i):
            session.add(ProjectORM(project_id=i, project_name=name, project_type="internal"))
    session.flush()

    # Synthetic fellows
    fellows_data = [
        (1, 1, "Jainil Shah", "shah.jainil@northeastern.edu", "active", "Jainil Shah - Madison"),
        (2, 2, "Maya Lin", "m.lin@northeastern.edu", "active", "Maya Lin - NeuroVEP"),
        (3, 3, "Marcus Johnson", "marcus.j@northeastern.edu", "active", "Marcus Johnson - Lyrical"),
        (4, 4, "Aria Gupta", "gupta.aria@northeastern.edu", "inactive", "Aria Gupta - Medhavi"), # Inactive fellow test
        (5, 1, "David Kim", "d.kim@northeastern.edu", "active", "David Kim - Madison"),
    ]
    for fid, pid, name, email, status, label in fellows_data:
        if not session.get(FellowORM, fid):
            session.add(FellowORM(
                fellow_id=fid, project_id=pid, full_name=name, email=email, status=status, display_label=label
            ))
    session.flush()

    # Videos across different statuses
    now = datetime.now(timezone.utc)
    videos_data = [
        # video_id, fellow_id, title, yt_link, drive_link, drive_id, yt_id, status, submitted_on, published_on
        (
            1, 1, "Madison System Architecture Overview",
            "https://www.youtube.com/watch?v=RzW17zhV4Nw", "https://drive.google.com/file/d/1A_xYZ",
            "drive_file_101", "RzW17zhV4Nw", "queued", now - timedelta(days=2), None
        ),
        (
            2, 2, "NeuroVEP Visual Evoked Potential Analysis",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://drive.google.com/file/d/2B_yZA",
            "drive_file_102", "dQw4w9WgXcQ", "queued", now - timedelta(days=1), None
        ),
        (
            3, 3, "Lyrical Literacy Interactive Learning Demo",
            "https://www.youtube.com/watch?v=3C_zAB12345", "https://drive.google.com/file/d/3C_zAB",
            "drive_file_103", "3C_zAB12345", "published", now - timedelta(days=5), now - timedelta(days=3)
        ),
        (
            4, 4, "Medhavi Assistive Tech Walkthrough",
            "https://www.youtube.com/watch?v=4D_wCD67890", "https://drive.google.com/file/d/4D_wCD",
            "drive_file_104", "4D_wCD67890", "queued", now - timedelta(days=3), None
        ),
        (
            5, 5, "Caching Explained in 5 Minutes",
            "https://www.youtube.com/watch?v=5E_vEF99999", "https://drive.google.com/file/d/5E_vEF",
            "drive_file_105", "5E_vEF99999", "in_review", now - timedelta(days=4), None
        ),
    ]

    for vid, fid, title, yt_link, dr_link, dr_id, yt_id, status, sub_on, pub_on in videos_data:
        if not session.get(VideoRecordORM, vid):
            session.add(VideoRecordORM(
                video_id=vid,
                fellow_id=fid,
                title=title,
                youtube_link=yt_link,
                drive_link=dr_link,
                drive_file_id=dr_id,
                youtube_video_id=yt_id,
                current_status=status,
                submitted_on=sub_on,
                published_on=pub_on,
            ))
    session.flush()

    # Pre-existing notification for already published video 3 (to test duplicate prevention)
    if not session.get(NotificationLogORM, 1):
        session.add(NotificationLogORM(
            notification_id=1,
            video_id=3,
            recipient_email="marcus.j@northeastern.edu",
            sent_on=now - timedelta(days=3),
            delivery_status="sent",
            reason="Video published on YouTube",
            attempt_count=1,
        ))
    session.flush()

    return {"status": "seeded"}
