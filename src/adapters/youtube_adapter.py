"""YouTube adapter interface and implementations (Mock & Live Data API v3)."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Dict
import requests

import json
from pathlib import Path
from src.config import settings, BASE_DIR


@dataclass
class YouTubeVideoMetadata:
    """Normalized metadata for a YouTube video."""
    video_id: str
    title: str
    privacy_status: str       # 'public', 'private', 'unlisted'
    upload_status: str        # 'processed', 'uploaded'
    published_at: Optional[datetime] = None
    channel_id: Optional[str] = None
    playlist_name: Optional[str] = None


class BaseYouTubeAdapter(ABC):
    """Abstract interface for YouTube interaction."""

    @abstractmethod
    def get_video_status(self, video_id: str) -> Optional[YouTubeVideoMetadata]:
        """Fetch metadata and current privacy status for a video ID."""
        pass

    @abstractmethod
    def is_video_public(self, video_id: str) -> bool:
        """Return True if the video is currently public."""
        pass


class MockYouTubeAdapter(BaseYouTubeAdapter):
    """In-memory & file-persisted mock adapter simulating YouTube Studio and publication events.
    
    Allows simulating Professor Brown or Nina publishing a video from the Queue playlist.
    """

    def __init__(self, state_file: Optional[Path] = None):
        self.state_file = state_file
        self._videos: Dict[str, YouTubeVideoMetadata] = {}
        self._load_or_init()

    def _load_or_init(self) -> None:
        defaults = {
            "RzW17zhV4Nw": {
                "title": "Madison System Architecture Overview",
                "privacy_status": "unlisted",
                "upload_status": "processed",
                "playlist_name": "Queue",
            },
            "dQw4w9WgXcQ": {
                "title": "NeuroVEP Visual Evoked Potential Analysis",
                "privacy_status": "unlisted",
                "upload_status": "processed",
                "playlist_name": "Queue",
            },
            "3C_zAB12345": {
                "title": "Lyrical Literacy Interactive Learning Demo",
                "privacy_status": "public",
                "upload_status": "processed",
            },
            "4D_wCD67890": {
                "title": "Medhavi Assistive Tech Walkthrough",
                "privacy_status": "unlisted",
                "upload_status": "processed",
                "playlist_name": "Queue",
            },
            "5E_vEF99999": {
                "title": "Caching Explained in 5 Minutes",
                "privacy_status": "private",
                "upload_status": "processed",
            },
        }

        saved_state = {}
        if self.state_file and self.state_file.exists():
            try:
                saved_state = json.loads(self.state_file.read_text(encoding="utf-8"))
            except Exception:
                saved_state = {}

        # Merge defaults with saved
        for vid, data in defaults.items():
            state = saved_state.get(vid, data)
            self._videos[vid] = YouTubeVideoMetadata(
                video_id=vid,
                title=state.get("title", data["title"]),
                privacy_status=state.get("privacy_status", data["privacy_status"]),
                upload_status=state.get("upload_status", data["upload_status"]),
                playlist_name=state.get("playlist_name", data.get("playlist_name")),
            )

    def _persist(self) -> None:
        if not self.state_file:
            return
        raw = {}
        for vid, m in self._videos.items():
            raw[vid] = {
                "title": m.title,
                "privacy_status": m.privacy_status,
                "upload_status": m.upload_status,
                "playlist_name": m.playlist_name,
            }
        try:
            self.state_file.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        except OSError:
            pass

    def register_video(self, meta: YouTubeVideoMetadata) -> None:
        """Register or update a simulated video."""
        self._videos[meta.video_id] = meta
        self._persist()

    def simulate_publish(self, video_id: str) -> bool:
        """Simulate a reviewer publishing the video (making it Public)."""
        if video_id in self._videos:
            self._videos[video_id].privacy_status = "public"
            self._videos[video_id].published_at = datetime.now(timezone.utc)
            self._persist()
            return True
        return False

    def simulate_unpublish(self, video_id: str) -> bool:
        """Simulate a video being set back to private/unlisted."""
        if video_id in self._videos:
            self._videos[video_id].privacy_status = "unlisted"
            self._persist()
            return True
        return False

    def get_video_status(self, video_id: str) -> Optional[YouTubeVideoMetadata]:
        return self._videos.get(video_id)

    def is_video_public(self, video_id: str) -> bool:
        meta = self.get_video_status(video_id)
        return meta is not None and meta.privacy_status.lower() == "public"


class LiveYouTubeAdapter(BaseYouTubeAdapter):
    """Production client calling Google YouTube Data API v3."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or getattr(settings, "YOUTUBE_API_KEY", "")
        self.base_url = "https://www.googleapis.com/youtube/v3"

    def get_video_status(self, video_id: str) -> Optional[YouTubeVideoMetadata]:
        if not self.api_key:
            raise ValueError("YOUTUBE_API_KEY not configured. Set USE_MOCK_APIS=true or provide key.")
        
        url = f"{self.base_url}/videos"
        params = {
            "part": "status,snippet",
            "id": video_id,
            "key": self.api_key,
        }
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        items = data.get("items", [])
        if not items:
            return None
        
        item = items[0]
        status = item.get("status", {})
        snippet = item.get("snippet", {})
        
        pub_at = snippet.get("publishedAt")
        pub_dt = datetime.fromisoformat(pub_at.replace("Z", "+00:00")) if pub_at else None

        return YouTubeVideoMetadata(
            video_id=video_id,
            title=snippet.get("title", ""),
            privacy_status=status.get("privacyStatus", "unknown"),
            upload_status=status.get("uploadStatus", "unknown"),
            published_at=pub_dt,
            channel_id=snippet.get("channelId"),
        )

    def is_video_public(self, video_id: str) -> bool:
        meta = self.get_video_status(video_id)
        return meta is not None and meta.privacy_status.lower() == "public"


def get_youtube_adapter() -> BaseYouTubeAdapter:
    """Factory creating YouTube adapter based on settings."""
    if settings.USE_MOCK_APIS:
        return MockYouTubeAdapter(state_file=BASE_DIR / ".mock_youtube.json")
    return LiveYouTubeAdapter()
