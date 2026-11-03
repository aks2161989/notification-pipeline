"""Command-line interface for managing the notification pipeline and operational store."""

import argparse
import sys
from pathlib import Path
from tabulate import tabulate

from src.config import settings, PipelineMode
from src.storage.database import init_db, drop_db, get_session, engine
from src.storage.repository import PipelineRepository
from src.storage.seeder import seed_from_excel, seed_synthetic_scenarios
from src.adapters.youtube_adapter import MockYouTubeAdapter, get_youtube_adapter
from src.adapters.email_adapter import NotificationDispatcher, ConsoleSink, FileOutboxSink


def cmd_test_conn(args):
    """Test database connectivity."""
    print(f"[*] Testing database connection: {settings.DATABASE_URL}")
    try:
        with engine.connect() as conn:
            result = conn.exec_driver_sql("SELECT 1").scalar()
            print(f"[+] Connection SUCCESSFUL! Probe query returned: {result}")
            print(f"[*] Database dialect: {engine.dialect.name}")
    except Exception as e:
        print(f"[-] Connection FAILED: {e}")
        sys.exit(1)


def cmd_init_db(args):
    """Create all schema tables."""
    print(f"[*] Initializing database schema at {settings.DATABASE_URL} ...")
    if args.drop:
        print("[!] Dropping existing tables first...")
        drop_db()
    init_db()
    print("[+] Database schema successfully created!")


def cmd_seed(args):
    """Seed the database with spreadsheet and synthetic data."""
    print("[*] Ensuring database tables exist...")
    init_db()
    
    with get_session() as session:
        if settings.TRACKER_XLSX_PATH.exists() and not args.synthetic_only:
            print(f"[*] Reading source tracker spreadsheet: {settings.TRACKER_XLSX_PATH}")
            counts = seed_from_excel(settings.TRACKER_XLSX_PATH, session)
            print(f"[+] Loaded from Excel: {counts}")
        
        print("[*] Adding synthetic test scenarios covering all workflow states...")
        synth = seed_synthetic_scenarios(session)
        print(f"[+] Added scenarios: {synth}")
    print("[+] Seeding complete!")


def cmd_status(args):
    """Display current pipeline status and table counts."""
    init_db()
    with get_session() as session:
        repo = PipelineRepository(session)
        metrics = repo.get_metrics()
        
        print("\n" + "=" * 60)
        print("   HUMANITARIANS AI - PUBLICATION PIPELINE TRACKER")
        print("=" * 60)
        print(f" Database URL : {settings.DATABASE_URL}")
        print(f" Pipeline Mode: {settings.PIPELINE_MODE.value}")
        print("-" * 60)
        print(f" Total Projects    : {metrics['total_projects']}")
        print(f" Total Fellows     : {metrics['total_fellows']}")
        print(f" Total Videos      : {metrics['total_videos']}")
        print(f"   - Queued        : {metrics['queued_videos']}")
        print(f"   - Published     : {metrics['published_videos']}")
        print(f" Sent Notifications: {metrics['sent_notifications']}")
        print(f" Failed / Retrying : {metrics['failed_notifications']}")
        print("=" * 60)
        
        # Display Videos Table
        videos = repo.list_all_videos()
        if videos:
            print("\n--- Tracked Videos ---")
            video_rows = []
            for v in videos:
                fellow_name = v.fellow.full_name if v.fellow else "UNKNOWN"
                fellow_email = v.fellow.email if v.fellow else "N/A"
                video_rows.append([
                    v.video_id,
                    v.title[:35] + "..." if v.title and len(v.title) > 35 else v.title,
                    fellow_name,
                    v.youtube_video_id,
                    v.current_status,
                ])
            print(tabulate(
                video_rows,
                headers=["ID", "Title", "Fellow", "YT Video ID", "Status"],
                tablefmt="rounded_grid",
            ))

        # Display Notifications Table
        with_notifs = []
        for v in videos:
            notifs = repo.get_notifications_for_video(v.video_id)
            for n in notifs:
                with_notifs.append([
                    n.notification_id,
                    n.video_id,
                    n.recipient_email,
                    n.delivery_status,
                    n.sent_on.strftime("%Y-%m-%d %H:%M") if n.sent_on else "N/A",
                    n.attempt_count,
                    n.reason or "",
                ])
        if with_notifs:
            print("\n--- Notification Log (Audit & Idempotency Guard) ---")
            print(tabulate(
                with_notifs,
                headers=["Notif ID", "Video ID", "Recipient", "Delivery Status", "Sent On", "Attempts", "Reason"],
                tablefmt="rounded_grid",
            ))
        print("")


def cmd_preview_email(args):
    """Preview a formatted notification email for a specific video."""
    init_db()
    with get_session() as session:
        repo = PipelineRepository(session)
        video = repo.get_video(args.video_id)
        if not video:
            print(f"[-] Video with ID {args.video_id} not found.")
            sys.exit(1)
        
        fellow = video.fellow
        if not fellow:
            print(f"[-] Video {args.video_id} has no mapped fellow.")
            sys.exit(1)
            
        project_name = fellow.project.project_name if fellow.project else "Humanitarians AI"
        mode = PipelineMode(args.mode.upper()) if args.mode else settings.PIPELINE_MODE

        dispatcher = NotificationDispatcher(sinks=[ConsoleSink(), FileOutboxSink()])
        print(f"[*] Rendering notification in {mode.value} mode...")
        result = dispatcher.dispatch(
            video_id=video.video_id,
            fellow_id=fellow.fellow_id,
            fellow_name=fellow.full_name,
            fellow_email=fellow.email,
            fellow_status=fellow.status,
            video_title=video.title or "Untitled",
            youtube_video_id=video.youtube_video_id or "UNKNOWN",
            project_name=project_name,
            mode=mode,
            redirect_to=args.redirect_to,
        )
        print(f"[+] Dispatch Result: status={result.delivery_status.value}, success={result.success}")
        print(f"[+] Saved preview in: {settings.OUTBOX_DIR}")


def cmd_list_outbox(args):
    """List generated email preview files in the outbox directory."""
    if not settings.OUTBOX_DIR.exists():
        print(f"[*] Outbox directory {settings.OUTBOX_DIR} is empty or does not exist.")
        return
    
    files = list(settings.OUTBOX_DIR.glob("*"))
    if not files:
        print("[*] No preview files found in outbox.")
        return
    
    rows = []
    for f in sorted(files, key=lambda x: x.stat().st_mtime, reverse=True):
        rows.append([f.name, f"{f.stat().st_size} bytes", f.suffix])
    print("\n--- Outbox Inspection ---")
    print(tabulate(rows, headers=["File Name", "Size", "Type"], tablefmt="rounded_grid"))


def main():
    parser = argparse.ArgumentParser(description="Humanitarians AI Notification Pipeline CLI")
    subparsers = parser.add_subparsers(dest="command", help="Subcommand to run")

    # test-conn
    subparsers.add_parser("test-conn", help="Test database connection")

    # init-db
    p_init = subparsers.add_parser("init-db", help="Initialize database schema")
    p_init.add_argument("--drop", action="store_true", help="Drop existing tables before recreating")

    # seed
    p_seed = subparsers.add_parser("seed", help="Seed database with Excel and synthetic test records")
    p_seed.add_argument("--synthetic-only", action="store_true", help="Skip Excel and load only synthetic records")

    # status
    subparsers.add_parser("status", help="Show pipeline database status and metrics")

    # preview-email
    p_preview = subparsers.add_parser("preview-email", help="Preview formatted email notification for a video")
    p_preview.add_argument("--video-id", type=int, required=True, help="Video ID to render")
    p_preview.add_argument("--mode", choices=["DRY_RUN", "REDIRECTED", "COHORT", "PROD"], help="Execution mode override")
    p_preview.add_argument("--redirect-to", type=str, help="Recipient override in REDIRECTED mode")

    # list-outbox
    subparsers.add_parser("list-outbox", help="List files in outbox directory")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

    handlers = {
        "test-conn": cmd_test_conn,
        "init-db": cmd_init_db,
        "seed": cmd_seed,
        "status": cmd_status,
        "preview-email": cmd_preview_email,
        "list-outbox": cmd_list_outbox,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
