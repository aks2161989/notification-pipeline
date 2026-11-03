# Vernal Point Interview Preparation & Talking Points Cheat-Sheet
**Role**: Data Engineering Associate | **Firm**: Vernal Point Advisors (Multi-Family Office RIA, $4B+ AUM)  
**Candidate**: Akshay Madan Chavan | **Project**: Automated Publication-Notification Pipeline (Humanitarians AI)

---

## 1. Executive Summary & Opening Elevator Pitch

> *"At Humanitarians AI, I noticed an operational bottleneck in our content publication workflow. Fellows submitted educational videos to Google Drive, staff reviewed and published them to YouTube, and an operations lead spent hours manually cross-referencing filenames against video titles to figure out who submitted what and email them.*
>
> *Matching by title was fragile—titles were rewritten for YouTube, files got renamed, and multiple fellows had identical video names. As a result, publication notifications were either missed or sent multiple times.*
>
> *I designed and built an automated reconciliation and notification pipeline in Python and PostgreSQL. I replaced fuzzy title matching with a deterministic data model linking Google Drive file IDs, Fellow identities, and YouTube video IDs. To ensure production reliability, I implemented a two-phase state machine, cross-process execution locks, and an idempotency guard that guarantees duplicate emails can never be sent. I also built a 4-stage rollout framework (dry-run, redirected testing, canary cohorts, full production) so we could rigorously test the system before touching fellow inboxes.*
>
> *This eliminated the manual reconciliation process entirely, established a clean operational audit trail, and serves as the single source of truth for our content lifecycle."*

---

## 2. Direct Mapping: Your Application Claims $\longleftrightarrow$ The Codebase

| What You Claimed in Your Cover Email | Where It Lives in the Codebase | Key Talking Point for the Interviewer |
| :--- | :--- | :--- |
| **"A per-video tracking model that links Drive file IDs, fellow identities and YouTube video IDs..."** | `src/models/orm.py`<br>`src/models/domain.py` | "Instead of ambiguous string matching, we enforced relational surrogate keys across 6 tables (`projects`, `pms`, `fellows`, `videos`, `review_events`, `notifications`). Foreign keys and composite indexes guarantee a stable join: `drive_file_id` $\leftrightarrow$ `video_id` $\leftrightarrow$ `fellow_id` $\leftrightarrow$ `youtube_video_id`." |
| **"Automated ingestion of Drive and video metadata into shared records..."** | `src/storage/seeder.py`<br>`src/adapters/drive_adapter.py` | "We built ingestion parsers that ingest submission metadata directly from Google Sheets/Excel registers and Drive folders, auto-populating records rather than requiring manual data entry." |
| **"A scheduled job that detects YouTube publication-state changes..."** | `src/pipeline/engine.py`<br>`src/adapters/youtube_adapter.py` | "The reconciliation engine queries queued drafts, polls the YouTube Data API v3 for privacy status changes (`unlisted/private` $\rightarrow$ `public`), and triggers the notification lifecycle." |
| **"Idempotent processing with explicit delivery states, retries, audit logs and concurrency locks..."** | `src/pipeline/lock.py`<br>`src/pipeline/engine.py`<br>`src/pipeline/recovery.py` | "We prevented race conditions and duplicate sends through two layers: (1) an `ExecutionLock` preventing overlapping cron executions, and (2) a two-phase commit state machine (`pending` $\rightarrow$ `sending` $\rightarrow$ `sent`/`failed`). If the network crashes mid-send, the row stays in `sending` for audit rather than silently resending." |
| **"A staged rollout: dry-run mode, redirected-test mode, and limited production cohort..."** | `src/adapters/email_adapter.py`<br>`src/config.py` | "We designed 4 runtime modes (`DRY_RUN`, `REDIRECTED`, `COHORT`, `PROD`). In redirected mode, recipient addresses are rewritten to a test inbox while preserving `X-Original-Recipient: fellow@northeastern.edu` in headers and saving `.html` previews to `outbox/` for inspection." |

---

## 3. High-Probability Interview Questions & Star Answers

### Q1: "How did you process and normalize the data before using it in the pipeline?"
> **Your Answer**:
> *"When I inherited the initial tracking spreadsheet, it had classic operational data hygiene problems: it was denormalized (e.g. composite strings like 'Jainil Shah - Madison' were pasted directly into video rows), emails had mixed casing and whitespace, and video links were pasted inconsistently as full URLs, short links, or raw IDs.
>
> To resolve this, I took a two-tier approach:
> 1. **Relational Normalization (3NF)**: I decomposed the raw structure into Third Normal Form across 6 discrete tables (`projects`, `pms`, `fellows`, `videos`, `review_events`, `notifications`). This decoupled fellow identities from projects, eliminated transitive dependencies, and gave every record a stable surrogate primary key.
> 2. **Ingestion Cleansing & Canonical Extractors (`normalizer.py`)**: I built regex and URL parsing extractors that automatically parse full YouTube links (`watch?v=...`, `youtu.be/...`, embed links) down to their canonical 11-character video ID, and Drive links down to their stable file ID. Emails are sanitized (`.strip().lower()`) and stripped of non-printable unicode characters.
> 3. **Defensive Database Constraints**: Rather than relying only on application code, I enforced database-level SQL `CheckConstraint`s on our controlled vocabularies (`current_status`, `delivery_status`). If any corrupt status is attempted, the database engine itself rejects the write."*

### Q2: "Why did you build this in Python and PostgreSQL instead of Google Apps Script?"
> **Your Answer**:
> *"The team initially considered Google Apps Script inside Google Sheets because the workspace was already on Google. However, once I evaluated the reliability requirements for Phase 3, Apps Script presented critical engineering constraints:*
> 1. *Execution Caps*: Apps Script enforces a strict 6-minute timeout limit, making batch polling and retries prone to mid-run termination.
> 2. *Testability*: It lacks a modern unit testing framework. For a system emailing 100+ fellows, I insisted on having a deterministic test suite with mocked external APIs so we could verify edge cases locally in CI (`pytest`).
> 3. *Concurrency & State*: Apps Script's `LockService` has a 30-second timeout and fails silently under high concurrency. With Python and PostgreSQL, we implemented transactional locks (`pg_try_advisory_lock` or file mutexes with TTLs) and composite unique constraints.
> 4. *Warehouse Compatibility*: PostgreSQL allowed us to write standard ANSI SQL migrations and prepare the schema for future export into an analytical data warehouse."

---

### Q3: "How did you prevent duplicate emails when scheduled triggers overlap?"
> **Your Answer**:
> *"Duplicates in event pipelines typically happen in two ways: overlapping execution triggers and mid-pipeline crashes. We solved both:*
> * First, **Cross-Process Concurrency Locking (`ExecutionLock`)**: At the start of each run, the pipeline acquires an execution lock recording the worker PID, hostname, and timestamp. If a prior job is still running, overlapping triggers immediately exit with `LockAcquisitionError`. We also added stale-lock reclamation (TTL = 10 mins) to prevent deadlocks if a container dies.
> * Second, **Two-Phase Idempotency State Machine (`PipelineEngine`)**: Before calling the external email dispatcher, we commit a row to the `notifications` table with status `SENDING`. If the process crashes mid-network call, the next run sees the `SENDING` row and skips it. Once the email is confirmed delivered, the status is updated to `SENT`. If it fails, it's marked `FAILED` with the exact diagnostic reason for controlled retry."

---

### Q4: "What happens if an external API (YouTube or Gmail) fails intermittently?"
> **Your Answer**:
> *"We built an operational `RetryService` and health monitoring `doctor` command:*
> * Transient failures (e.g. SMTP connection timeouts or API rate limits) transition the notification to `FAILED` and increment `attempt_count`.
> * The `RetryService` scans for failed notifications where `attempt_count < 3` and re-attempts dispatch with backoff.
> * If a notification fails 3 times, it is marked as `RETRY_EXHAUSTED` (Severity: `CRITICAL`). Our `doctor` health monitor surfaces these records so an engineer can inspect the failure payload rather than letting emails fail silently."

---

### Q5: "How does this experience translate to Vernal Point's data engineering needs?"
> **Your Answer**:
> *"It's the exact same engineering pattern described in your job description for the First Year:*
> * **Raw Feed to Trusted Dataset**: In both cases, you have untrusted, decoupled systems (Google Drive & YouTube vs. Custodians like Schwab, Fidelity, or Addepar). You cannot rely on raw descriptions; you must model stable surrogate identifiers (`account_id`, `entity_id`, `file_id`).
> * **Retiring Manual Reconciliation**: At Humanitarians AI, an operations colleague manually matched video titles to names. At Vernal Point, operations teams frequently reconcile trade settlements, bank feeds, and capital calls by hand. Building scheduled jobs with data quality checks replaces brittle manual handoffs with auditable automated pipelines.
> * **Fiduciary Responsibility & Zero Silent Failures**: In wealth management with $4B+ AUM, an automated client notice or capital call can never be sent twice or missed silently. The state machine, idempotency guards, and dry-run rollout engine I built reflect the high fiduciary engineering discipline required for multi-family office infrastructure."

---

## 4. Live Coding / Repository Walkthrough Guide

If the interviewer asks you to share your screen or walk through your repository:

1. **Start with the Domain Model (`src/models/orm.py`)**:
   * Show the 6 relational tables (`projects`, `fellows`, `videos`, `notifications`).
   * Point out the explicit foreign keys: `VideoRecordORM.fellow_id -> FellowORM.fellow_id`.
   * Highlight the composite index: `Index("idx_notif_video_status", "video_id", "delivery_status")`.

2. **Show the Two-Phase State Machine (`src/pipeline/engine.py` $\rightarrow$ `_process_single_video`)**:
   * Walk them through the lines:
     * Line 116: Idempotency check (`repo.get_active_or_sent_notification`).
     * Line 139: Committing `SENDING` state *before* dispatch.
     * Line 158: Dispatching email via the adapter.
     * Line 172: Updating to `SENT` or `FAILED`.

3. **Demonstrate the CLI Live in PowerShell**:
   ```powershell
   # 1. Show database status
   python -m src.cli status

   # 2. Run the doctor health check
   python -m src.cli doctor

   # 3. Run the full pytest test suite (18 passing tests)
   pytest tests/ -v
   ```
   *Seeing 18 clean, passing tests with 0 warnings in < 2 seconds immediately proves code quality and professional engineering discipline.*
