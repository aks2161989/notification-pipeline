"""Google Drive adapter interface and implementations (Mock & Live Drive API v3)."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Dict, List

from src.config import settings


@dataclass
class DriveFileMetadata:
    """Normalized metadata for a file submitted to Google Drive."""
    file_id: str
    name: str
    mime_type: str
    size_bytes: int
    created_time: datetime
    folder_path: str
    submitter_email: Optional[str] = None


class BaseDriveAdapter(ABC):
    """Abstract interface for Google Drive ingestion."""

    @abstractmethod
    def get_file_metadata(self, file_id: str) -> Optional[DriveFileMetadata]:
        """Fetch metadata for a Drive file by its stable file ID."""
        pass

    @abstractmethod
    def list_folder_submissions(self, folder_id: Optional[str] = None) -> List[DriveFileMetadata]:
        """List video submission files in a given folder."""
        pass


class MockDriveAdapter(BaseDriveAdapter):
    """Mock Drive adapter simulating fellow file uploads."""

    def __init__(self):
        now = datetime.now(timezone.utc)
        self._files: Dict[str, DriveFileMetadata] = {
            "drive_file_101": DriveFileMetadata(
                file_id="drive_file_101",
                name="Madison_Architecture_Final.mp4",
                mime_type="video/mp4",
                size_bytes=450_000_000,
                created_time=now,
                folder_path="/Submissions/Madison/Jainil_Shah",
                submitter_email="shah.jainil@northeastern.edu",
            ),
            "drive_file_102": DriveFileMetadata(
                file_id="drive_file_102",
                name="NeuroVEP_Analysis_v2.mp4",
                mime_type="video/mp4",
                size_bytes=320_000_000,
                created_time=now,
                folder_path="/Submissions/NeuroVEP/Maya_Lin",
                submitter_email="m.lin@northeastern.edu",
            ),
        }

    def register_file(self, file: DriveFileMetadata) -> None:
        self._files[file.file_id] = file

    def get_file_metadata(self, file_id: str) -> Optional[DriveFileMetadata]:
        return self._files.get(file_id)

    def list_folder_submissions(self, folder_id: Optional[str] = None) -> List[DriveFileMetadata]:
        return list(self._files.values())


class LiveDriveAdapter(BaseDriveAdapter):
    """Production adapter using Google Drive API v3."""

    def __init__(self, service=None):
        self.service = service

    def get_file_metadata(self, file_id: str) -> Optional[DriveFileMetadata]:
        if not self.service:
            raise NotImplementedError("Live Google Drive client requires credentials.")
        # Production Drive API call: files().get(fileId=..., fields="id, name, mimeType, size, createdTime")
        return None

    def list_folder_submissions(self, folder_id: Optional[str] = None) -> List[DriveFileMetadata]:
        if not self.service:
            raise NotImplementedError("Live Google Drive client requires credentials.")
        return []


def get_drive_adapter() -> BaseDriveAdapter:
    """Factory creating Drive adapter based on settings."""
    if settings.USE_MOCK_APIS:
        return MockDriveAdapter()
    return LiveDriveAdapter()
