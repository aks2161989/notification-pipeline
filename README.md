<div align="center">

# Automated Publication-Notification Pipeline
### Humanitarians AI &bull; Content Operations & Workflow Automation

[![CI Pipeline](https://github.com/aks2161989/notification-pipeline/actions/workflows/ci.yml/badge.svg)](https://github.com/aks2161989/notification-pipeline/actions)
[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Database](https://img.shields.io/badge/database-PostgreSQL%20%7C%20SQLite-green.svg)](https://www.postgresql.org/)
[![Tests](https://img.shields.io/badge/tests-24%20passed-brightgreen.svg)](https://pytest.org/)
[![Architecture](https://img.shields.io/badge/architecture-idempotent%20state%20machine-purple.svg)](#architecture--system-design)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

An idempotent, production-grade data pipeline that automates creator publication notifications by reconciling Google Drive submissions, relational video registers, and YouTube Data API publication events with strict concurrency controls and zero duplicate delivery guarantees.

</div>

---

## 📌 Problem & Motivation

At **Humanitarians AI**, educational fellows produce videos that are reviewed by editorial teams and uploaded as drafts to YouTube playlists (`Queue`). When reviewers publish videos to the public, fellows must be promptly notified with their live publication URL.

### The Engineering Challenges:
1. **Ambiguous Identifier Matching**: Submissions arrived via Google Drive with raw filenames (e.g., `Caching_Explainer_Final.mp4`), but YouTube titles were rewritten for audience SEO (e.g., `Caching Explained in 5 Minutes`). Matching by title or filename was brittle, producing silent misses or incorrect mappings.
2. **Duplicate Email Risks**: Scheduled cron polling repeatedly checks video availability. Without strict idempotency guards, overlapping executions or mid-pipeline retries risked spamming external fellows.
3. **Operational Silos**: Review events, draft uploads, and fellow email directories existed across disconnected spreadsheets without relational integrity or transactional audit logging.

---

## 🏗️ Architecture & System Design

The pipeline replaces brittle string matching with an **explicit relational identifier model** and an **idempotent two-phase state machine**:

```mermaid
flowchart TD
    subgraph Ingestion["1. Ingestion & Identity Modeling"]
        Drive["Google Drive Submissions"] -->|Extract Stable File ID| IngestEngine["Data Ingestion Engine"]
        Roster["Fellow & PM Directory"] -->|Map Fellow ID| IngestEngine
        IngestEngine -->|Surrogate Keys| DB[("Operational Store: PostgreSQL / SQLite")]
    end

    subgraph Polling["2. Concurrency Lock & Polling Engine"]
        Cron["Scheduled Cron Runner"] --> Lock{"Acquire ExecutionLock?"}
        Lock -->|"Held / Active"| Collide["Abort Overlap Safely"]
        Lock -->|Acquired| Poller["YouTube Status Detector"]
        Poller -->|Fetch Privacy Status| YTAPI["YouTube Data API v3"]
        YTAPI -->|"Status = Public"| Reconciler["Reconciliation Matcher"]
    end

    subgraph Idempotency["3. Two-Phase Idempotent State Machine"]
        Reconciler --> CheckSent{"Notification In-Flight or Sent?"}
        CheckSent -->|"Yes (sending or sent)"| SkipDuplicate["Skip Dispatch / Duplicate Guard"]
        CheckSent -->|"No Record"| Step1["Phase 1: Pre-commit State = 'SENDING'"]
        Step1 --> Dispatcher["Notification Dispatcher"]
        Dispatcher --> ModeEval{"Pipeline Mode"}
        ModeEval -->|DRY_RUN| LogOnly["Log Intended Send + Save Outbox"]
        ModeEval -->|REDIRECTED| TestInbox["Redirect to Test Inbox with X-Headers"]
        ModeEval -->|"COHORT / PROD"| LiveSend["Send to Fellow via Gmail/SMTP"]
        Dispatcher --> Result{"Delivery Result?"}
        Result -->|Success| Step2A["Phase 2A: Commit State = 'SENT' + Timestamp"]
        Result -->|Failure| Step2B["Phase 2B: Commit State = 'FAILED' + Retry Queue"]
    end
```

---

## 💾 Relational Data Model

Implemented in SQLAlchemy ORM with foreign keys, status check constraints, and composite indexes:

```mermaid
erDiagram
    PROJECTS ||--o{ FELLOWS : contains
    PROJECTS ||--o{ PMS : manages
    FELLOWS ||--o{ VIDEOS : authors
    VIDEOS ||--o{ REVIEW_EVENTS : logs
    VIDEOS ||--o{ NOTIFICATIONS : tracks

    PROJECTS {
        int project_id PK
        string project_name
        string project_type
    }

    FELLOWS {
        int fellow_id PK
        int project_id FK
        string full_name
        string email
        string status
        string display_label
    }

    VIDEOS {
        int video_id PK
        int fellow_id FK
        string title
        string drive_file_id
        string youtube_video_id
        string current_status
        datetime submitted_on
        datetime published_on
    }

    REVIEW_EVENTS {
        int event_id PK
        int video_id FK
        string event_type
        string reviewer
        datetime occurred_on
        string notes
    }

    NOTIFICATIONS {
        int notification_id PK
        int video_id FK
        string recipient_email
        string delivery_status
        datetime sent_on
        int attempt_count
        string reason
    }
```

---

## 🛡️ Reliability & Enterprise Guarantees

### 1. Cross-Process Mutual Exclusion (`ExecutionLock`)
To prevent concurrent scheduled executions from colliding:
* Acquires a file-backed or database advisory lock capturing `pid`, `hostname`, and `timestamp`.
* Overlapping triggers raise `LockAcquisitionError` and abort immediately.
* **Automatic Stale-Lock Reclamation**: Expired locks exceeding the TTL (default: 600s) are automatically reclaimed to prevent deadlocks caused by killed containers.

### 2. Two-Phase Commit Delivery State Machine
* **Phase 1**: Prior to making external HTTP/SMTP calls, the pipeline commits a record with `delivery_status = 'SENDING'`.
* **Phase 2**: If the process crashes mid-network call, the record remains visible in the audit log as `SENDING`. The next run detects the in-flight record and refuses to double-send, trading an invisible failure for a visible, auditable record.
* Upon confirmed dispatch, the status is updated to `SENT` with an UTC delivery timestamp.

### 3. Four-Stage Rollout Engine
| Mode | Behavior | Purpose |
| :--- | :--- | :--- |
| **`DRY_RUN`** | Simulates logic and renders `.html`/`.json` outbox previews without dispatching emails. | Validates state transitions and fellow resolutions locally. |
| **`REDIRECTED`** | Formats emails but redirects to a test address, preserving `X-Original-Recipient`. | Verifies visual template layout in a live email inbox. |
| **`COHORT`** | Filters recipients against a strict canary whitelist (`COHORT_FELLOW_IDS`). | Enables limited production rollout for early feedback. |
| **`PROD`** | Live end-to-end delivery to verified active fellow emails. | Full organization rollout. |

### 4. Operational Health Diagnostics (`doctor` & `retry`)
* **Pipeline Doctor**: Detects stranded `SENDING` jobs, orphaned videos without fellow records, and fellows with invalid emails.
* **Automated Retry Service**: Queries transient failures and re-attempts delivery with exponential backoff up to 3 attempts.

### 5. Ingestion Hygiene & Database-Level Constraints
* **Canonical URL & ID Parsing**: Rather than expecting users to paste raw IDs, regex extractors automatically parse YouTube watch URLs, shortlinks, embed links, and Drive URLs down to their canonical tokens during ingestion.
* **Database Check Constraints**: Enforces SQL `CheckConstraint` on controlled vocabularies (`current_status`, `delivery_status`, `status`) at the database engine level, rejecting invalid states.

---

## 🚀 Quickstart & Interactive CLI

### 1. Setup Environment
```bash
# Clone repository
git clone https://github.com/aks2161989/notification-pipeline.git
cd notification-pipeline

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment (`.env`)
By default, the pipeline uses SQLite for zero-dependency local testing. It can be switched to PostgreSQL by editing `.env`:
```ini
DATABASE_URL=sqlite:///pipeline.db
# DATABASE_URL=postgresql://postgres:password@localhost:5432/notification_pipeline

PIPELINE_MODE=DRY_RUN
REDIRECT_TEST_EMAIL=your.email@example.com
USE_MOCK_APIS=true
```

### 3. Initialize & Seed Database
```bash
# Create database schema
python -m src.cli init-db

# Seed from teammate tracker spreadsheet and multi-fellow synthetic scenarios
python -m src.cli seed

# Display live operational dashboard
python -m src.cli status
```

---

## 🕹️ Simulating the End-to-End Publication Lifecycle

You can simulate the entire production lifecycle from video review to publication:

```bash
# Step 1: Mark Video 1 as reviewed & queued in YouTube playlist by PM
python -m src.cli queue-video --video-id 1

# Step 2: Simulate reviewer (Prof. Brown) publishing the video on YouTube
python -m src.cli simulate-publish --video-id 1

# Step 3: Run the reconciliation pipeline in REDIRECTED mode
python -m src.cli run --mode REDIRECTED --redirect-to "your.email@example.com"

# Step 4: Run the pipeline again to verify idempotency (0 duplicates sent)
python -m src.cli run --mode REDIRECTED --redirect-to "your.email@example.com"

# Step 5: Inspect generated audit report
python -m src.cli report

# Step 6: Run operational health monitor
python -m src.cli doctor
```

---

## 🧪 Test Suite

The project includes unit and integration tests covering relational constraints, locking, and failure recovery:

```bash
pytest tests/ -v
```

```
tests/test_adapters.py::test_mock_youtube_adapter_lifecycle PASSED       [  4%]
tests/test_adapters.py::test_mock_drive_adapter_metadata PASSED          [  8%]
tests/test_adapters.py::test_email_formatter_templates PASSED            [ 12%]
tests/test_adapters.py::test_dispatcher_dry_run_mode PASSED              [ 16%]
tests/test_adapters.py::test_dispatcher_redirected_mode PASSED           [ 20%]
tests/test_adapters.py::test_dispatcher_inactive_fellow_skipped PASSED   [ 25%]
tests/test_normalizer.py::test_normalize_email_hygiene PASSED            [ 29%]
tests/test_normalizer.py::test_extract_youtube_id_formats PASSED         [ 33%]
tests/test_normalizer.py::test_extract_drive_file_id_formats PASSED      [ 37%]
tests/test_normalizer.py::test_normalize_status PASSED                   [ 41%]
tests/test_normalizer.py::test_db_check_constraint_rejects_invalid_video_status PASSED [ 45%]
tests/test_normalizer.py::test_db_check_constraint_rejects_invalid_notification_status PASSED [ 50%]
tests/test_pipeline.py::test_execution_lock_mutual_exclusion PASSED      [ 54%]
tests/test_pipeline.py::test_execution_lock_stale_reclaim PASSED         [ 58%]
tests/test_pipeline.py::test_idempotency_state_machine_and_duplicate_prevention PASSED [ 62%]
tests/test_pipeline.py::test_failure_handling_and_retry_state PASSED     [ 66%]
tests/test_recovery.py::test_doctor_detects_stranded_sending PASSED      [ 70%]
tests/test_recovery.py::test_doctor_detects_exhausted_retries PASSED     [ 75%]
tests/test_recovery.py::test_retry_service_successful_retry PASSED       [ 79%]
tests/test_storage.py::test_project_and_fellow_creation PASSED           [ 83%]
tests/test_storage.py::test_video_record_lifecycle PASSED                [ 87%]
tests/test_idempotency_guard_detection PASSED                            [ 91%]
tests/test_storage.py::test_seeder_synthetic_scenarios PASSED            [ 95%]
tests/test_storage.py::test_excel_file_ingestion PASSED                  [100%]
============================= 24 passed in 2.05s ==============================
```

---

## 📂 Project Structure

```
notification-pipeline/
├── .github/workflows/ci.yml       # GitHub Actions CI workflow
├── data/
│   └── YouTube_Pipeline_Tracker.xlsx # Master reference spreadsheet schema
├── docs/
│   └── INTERVIEW_CHEAT_SHEET.md   # Architectural talking points & interview guide
├── src/
│   ├── config.py                  # Pydantic configuration & runtime settings
│   ├── models/
│   │   ├── domain.py              # Pydantic domain models & state Enums
│   │   └── orm.py                 # SQLAlchemy ORM relational models
│   ├── storage/
│   │   ├── database.py            # Dual PostgreSQL & SQLite engine / session manager
│   │   ├── repository.py          # Data access queries & idempotency guards
│   │   └── seeder.py              # Excel parser & synthetic scenario generator
│   ├── adapters/
│   │   ├── youtube_adapter.py     # Mock & Live YouTube Data API v3 client
│   │   ├── drive_adapter.py       # Mock & Live Google Drive API v3 client
│   │   └── email_adapter.py       # Multi-sink dispatcher (Console, Outbox, SMTP)
│   ├── pipeline/
│   │   ├── lock.py                # Process execution lock with TTL
│   │   ├── engine.py              # Two-phase reconciliation coordinator
│   │   └── recovery.py            # Health monitoring & automated retry service
│   └── cli.py                     # Command-line interface
├── tests/                         # Complete pytest test suite
├── requirements.txt               # Dependencies
└── pytest.ini                     # Pytest configuration
```

---

## 📄 License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
