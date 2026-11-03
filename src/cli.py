"""Command-line interface for managing the notification pipeline and operational store."""

import argparse
import sys
from pathlib import Path
from tabulate import tabulate

from src.config import settings, PipelineMode
from src.models.domain import VideoStatus, EventType
from src.storage.database import init_db, drop_db, get_session, engine
from src.storage.repository import PipelineRepository
from src.storage.seeder import seed_from_excel, seed_synthetic_scenarios
from src.adapters.youtube_adapter import MockYouTubeAdapter, get_youtube_adapter
from src.adapters.email_adapter import NotificationDispatcher, ConsoleSink, FileOutboxSink
from src.pipeline.engine import PipelineEngine
from src.pipeline.lock import ExecutionLock


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


def cmd_simulate_publish(args):
    """Simulate Professor Brown or Nina publishing a video on YouTube."""
    init_db()
    with get_session() as session:
        repo = PipelineRepository(session)
        video = repo.get_video(args.video_id)
        if not video:
            print(f"[-] Video ID {args.video_id} not found.")
            sys.exit(1)
        
        yt_id = video.youtube_video_id
        if not yt_id:
            print(f"[-] Video {args.video_id} has no YouTube video ID.")
            sys.exit(1)

        # Get adapter and publish
        yt_adapter = get_youtube_adapter()
        if hasattr(yt_adapter, "simulate_publish"):
            yt_adapter.simulate_publish(yt_id)
            print(f"[+] [SIMULATOR] Professor Brown published video on YouTube: ID='{yt_id}', Title='{video.title}'")
            print(f"[+] Video '{yt_id}' privacy status is now: PUBLIC.")
        else:
            print(f"[*] Live YouTube adapter configured; cannot simulate publish.")


def cmd_run(args):
    """Run scheduled publication detection and notification reconciliation."""
    mode = PipelineMode(args.mode.upper()) if args.mode else settings.PIPELINE_MODE
    redirect_to = args.redirect_to or settings.REDIRECT_TEST_EMAIL

    print("\n" + "=" * 65)
    print("  HUMANITARIANS AI - PUBLICATION NOTIFICATION RECONCILIATION")
    print("=" * 65)
    print(f" Mode               : {mode.value}")
    if mode == PipelineMode.REDIRECTED:
        print(f" Redirect Recipient : {redirect_to}")
    print(f" YouTube Adapter    : {'Mock (Simulated)' if settings.USE_MOCK_APIS else 'Live YouTube v3'}")
    print("-" * 65)

    engine_instance = PipelineEngine()
    report = engine_instance.run_reconciliation(
        mode=mode,
        redirect_to=redirect_to,
        bypass_lock=args.bypass_lock,
    )

    print("\n--- Execution Report ---")
    print(f" Videos Inspected         : {report.videos_inspected}")
    print(f" New Publications Detected: {report.new_publications_detected}")
    print(f" Notifications Dispatched : {report.notifications_sent}")
    print(f" Duplicates Prevented     : {report.duplicates_prevented}")
    print(f" Skipped (DryRun/Canary)  : {report.notifications_skipped}")
    print(f" Failures                 : {report.failures}")

    if report.processed_items:
        print("\n--- Per-Video Action Log ---")
        rows = []
        for item in report.processed_items:
            rows.append([
                item.video_id,
                item.youtube_video_id,
                item.action_taken,
                item.fellow_email or "N/A",
                item.detail[:40] + "..." if item.detail and len(item.detail) > 40 else item.detail,
            ])
        print(tabulate(
            rows,
            headers=["Video ID", "YouTube ID", "Action Taken", "Recipient", "Details"],
            tablefmt="rounded_grid",
        ))
    print("=" * 65 + "\n")


def cmd_report(args):
    """Generate comprehensive pipeline audit and reconciliation metrics."""
    init_db()
    with get_session() as session:
        repo = PipelineRepository(session)
        metrics = repo.get_metrics()
        videos = repo.list_all_videos()

        print("\n" + "=" * 65)
        print("   OPERATIONAL PIPELINE AUDIT & RECONCILIATION REPORT")
        print("=" * 65)
        print(f" Total Registered Videos : {metrics['total_videos']}")
        print(f" Active Fellow Roster    : {metrics['total_fellows']}")
        print(f" In Queue (Awaiting Pub) : {metrics['queued_videos']}")
        print(f" Published on YouTube    : {metrics['published_videos']}")
        print(f" Total Notifications Sent: {metrics['sent_notifications']}")
        print(f" Stranded / Failed       : {metrics['failed_notifications']}")
        print("=" * 65)

        # Audit details
        audit_rows = []
        for v in videos:
            notifs = repo.get_notifications_for_video(v.video_id)
            notif_status = notifs[0].delivery_status if notifs else "NONE"
            sent_on = notifs[0].sent_on.strftime("%Y-%m-%d %H:%M") if notifs and notifs[0].sent_on else "N/A"
            fellow_name = v.fellow.full_name if v.fellow else "UNASSIGNED"
            audit_rows.append([
                v.video_id,
                v.title[:30] + "..." if v.title and len(v.title) > 30 else v.title,
                fellow_name,
                v.current_status,
                notif_status,
                sent_on,
            ])
        print(tabulate(
            audit_rows,
            headers=["ID", "Title", "Fellow", "Video Status", "Notif State", "Delivered On"],
            tablefmt="rounded_grid",
        ))
        print("")


def cmd_queue_video(args):
    """Mark a video as reviewed, placed in YouTube Queue playlist, awaiting reviewer publish."""
    init_db()
    with get_session() as session:
        repo = PipelineRepository(session)
        video = repo.get_video(args.video_id)
        if not video:
            print(f"[-] Video ID {args.video_id} not found.")
            sys.exit(1)
        
        repo.update_video_status(args.video_id, status=VideoStatus.QUEUED, note="Placed in YouTube Queue playlist")
        repo.record_review_event(
            video_id=args.video_id,
            event_type=EventType.QUEUED,
            reviewer=args.reviewer or "sanjana.rao@humanitarians.ai",
            notes="Editorial review complete. Draft uploaded to YouTube Queue playlist.",
        )
        print(f"[+] Video {args.video_id} ('{video.title}') marked as QUEUED by PM.")


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

    # simulate-publish
    p_sim = subparsers.add_parser("simulate-publish", help="Simulate publishing a video on YouTube")
    p_sim.add_argument("--video-id", type=int, required=True, help="Internal Video ID to publish")

    # queue-video
    p_q = subparsers.add_parser("queue-video", help="Mark a reviewed video as QUEUED in YouTube playlist")
    p_q.add_argument("--video-id", type=int, required=True, help="Internal Video ID to queue")
    p_q.add_argument("--reviewer", type=str, default="sanjana.rao@humanitarians.ai", help="PM reviewer email")

    # run
    p_run = subparsers.add_parser("run", help="Run scheduled publication detection and notification job")
    p_run.add_argument("--mode", choices=["DRY_RUN", "REDIRECTED", "COHORT", "PROD"], help="Execution mode override")
    p_run.add_argument("--redirect-to", type=str, help="Recipient override in REDIRECTED mode")
    p_run.add_argument("--bypass-lock", action="store_true", help="Bypass process concurrency lock (dev only)")

    # report
    subparsers.add_parser("report", help="Generate pipeline audit and reconciliation metrics")

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
        "simulate-publish": cmd_simulate_publish,
        "queue-video": cmd_queue_video,
        "run": cmd_run,
        "report": cmd_report,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
