"""Runtime settings, read from environment variables.

In the Docker image every path is set explicitly. When the variables are unset
(local development), everything lives under ``<repo>/.dev/`` and the built
frontend is served from ``<repo>/frontend/build``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# backend/src/songvault/config.py -> parents[3] is the repository root.
REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    """Database and config. Keep it on a local disk: SQLite doesn't belong on a network share."""

    media_dir: Path
    """Originals, playback copies and thumbnails. Any large folder or share."""

    static_dir: Path
    """The built SvelteKit app."""

    session_days: int = 365
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    default_timezone: str = "UTC"
    """Used to read timestamps in file names when the uploading browser didn't send its time zone."""

    start_worker: bool = True
    """Run the background media worker (tests turn it off and process jobs directly)."""

    @property
    def db_path(self) -> Path:
        return self.data_dir / "songvault.db"

    @property
    def backup_marker(self) -> Path:
        """Written by the nightly offsite backup script after a successful run (spec 2.5)."""
        return self.media_dir / ".offsite-backup.json"

    @property
    def uploads_dir(self) -> Path:
        """Partial uploads. Inside the media share so finished files can be moved, not copied."""
        return self.media_dir / ".uploads"


def load_settings() -> Settings:
    dev = REPO_ROOT / ".dev"
    return Settings(
        data_dir=Path(os.environ.get("SONGVAULT_DATA_DIR", dev / "data")),
        media_dir=Path(os.environ.get("SONGVAULT_MEDIA_DIR", dev / "media")),
        static_dir=Path(os.environ.get("SONGVAULT_STATIC_DIR", REPO_ROOT / "frontend" / "build")),
        ffmpeg=os.environ.get("SONGVAULT_FFMPEG", "ffmpeg"),
        ffprobe=os.environ.get("SONGVAULT_FFPROBE", "ffprobe"),
        default_timezone=os.environ.get("TZ") or "UTC",
    )
