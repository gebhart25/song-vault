"""Media handling: ffprobe metadata, recorded dates, file layout, ffmpeg commands.

File layout (spec 2.3), relative to the media dir:

    <project-slug or _inbox>/<YYYY-MM-DD_HHMMSS>_<uid>.<ext>        original, as uploaded
    <project-slug or _inbox>/<YYYY-MM-DD_HHMMSS>_<uid>.play.mp4     H.264 playback copy
    <project-slug or _inbox>/<YYYY-MM-DD_HHMMSS>_<uid>.thumb.jpg    poster thumbnail
    <project-slug or _inbox>/<YYYY-MM-DD_HHMMSS>_<uid>.audio.m4a    audio-only copy of a video
    <project-slug or _inbox>/<YYYY-MM-DD_HHMMSS>_<uid>.preview.jpg  frames in a grid, for scrubbing
    <project-slug or _inbox>/<YYYY-MM-DD_HHMMSS>_<uid>.peaks.json   detailed waveform (the editor's)
    <song or _inbox>/<YYYY-MM-DD_HHMMSS>_<uid>[.<clip uid>].thumb.jpg  a clip's poster

The first five belong to a recording (one uploaded file); the thumbnail to a clip, a
stretch of a recording (spec 5.10). A recording split into pieces in different songs
keeps its files where they are; each piece's thumbnail lives in its own song's folder.
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
import subprocess
import sys
from array import array
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

INBOX_DIR = "_inbox"
CUTS_DIR = ".cuts"  # pieces cut out for a download or an export, deleted after

VIDEO_EXTENSIONS = {"mp4", "mov", "m4v", "webm", "mkv", "3gp"}
AUDIO_EXTENSIONS = {"m4a", "mp3", "wav", "aac", "ogg", "oga", "opus", "flac"}
ALLOWED_EXTENSIONS = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS

# Every file a recording has on disk, and a clip's own. Moving and deleting handle all of them.
RECORDING_FILES = ("original_path", "playback_path", "audio_path", "preview_path", "peaks_path")
CLIP_FILES = ("thumbnail_path",)

# Longest side of the playback copy. Plenty for learning parts back, and small to stream.
PLAYBACK_MAX_SIDE = 1280
THUMB_MAX_SIDE = 480
# The preview strip (spec 5.4): about a frame a second, up to 60, this tall, in a grid
# PREVIEW_COLUMNS across, so a short piece of a long recording still has frames.
PREVIEW_HEIGHT = 240
PREVIEW_MAX_FRAMES = 60
PREVIEW_COLUMNS = 10
# The detailed waveform (spec 5.9, 5.10): the loudest point of every 1/PEAKS_PER_SECOND s,
# from audio decoded at PEAKS_RATE; a clip's tile and player show WAVEFORM_BARS of it.
PEAKS_PER_SECOND = 20
PEAKS_RATE = 4000
WAVEFORM_BARS = 120

CONTENT_TYPES = {
    "mp4": "video/mp4",
    "m4v": "video/mp4",
    "mov": "video/quicktime",
    "webm": "video/webm",
    "mkv": "video/x-matroska",
    "3gp": "video/3gpp",
    "m4a": "audio/mp4",
    "aac": "audio/aac",
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "ogg": "audio/ogg",
    "oga": "audio/ogg",
    "opus": "audio/ogg",
    "flac": "audio/flac",
    "jpg": "image/jpeg",
}

# Audio codecs every target browser plays as-is.
BROWSER_AUDIO = {"aac", "mp3", "flac", "pcm_s16le", "pcm_s24le", "pcm_f32le"}

HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}
TONEMAP = (
    "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
    "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p"
)

EARLIEST_PLAUSIBLE = datetime(2000, 1, 1, tzinfo=UTC)


class MediaError(Exception):
    """A file that ffprobe/ffmpeg can't handle."""


def extension(filename: str) -> str:
    return PurePosixPath(filename.replace("\\", "/")).suffix.lower().lstrip(".")


def new_uid() -> str:
    return secrets.token_hex(4)


def content_type(path: str | Path) -> str:
    return CONTENT_TYPES.get(Path(path).suffix.lower().lstrip("."), "application/octet-stream")


# --- ffprobe ----------------------------------------------------------------------


@dataclass
class ProbeInfo:
    media_type: Literal["video", "audio"]
    format_name: str
    duration: float | None
    width: int | None  # as displayed (rotation applied)
    height: int | None
    video_codec: str | None
    pix_fmt: str | None
    is_hdr: bool
    audio_codec: str | None
    creation_time: datetime | None


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def parse_probe(data: dict) -> ProbeInfo:
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    video = next(
        (
            s
            for s in streams
            if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")
        ),
        None,
    )
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None and audio is None:
        raise MediaError("No audio or video in this file")

    width = height = None
    if video is not None:
        width, height = video.get("width"), video.get("height")
        rotation = video.get("tags", {}).get("rotate")
        for side in video.get("side_data_list", []):
            if "rotation" in side:
                rotation = side["rotation"]
        try:
            if rotation is not None and abs(int(float(rotation))) % 180 == 90:
                width, height = height, width
        except ValueError:
            pass

    tags = {k.lower(): v for k, v in fmt.get("tags", {}).items()}
    # Apple's creationdate carries the real local offset; prefer it when present.
    created = _parse_time(tags.get("com.apple.quicktime.creationdate")) or _parse_time(tags.get("creation_time"))
    if created is None:
        for s in streams:
            created = _parse_time({k.lower(): v for k, v in s.get("tags", {}).items()}.get("creation_time"))
            if created:
                break

    duration = fmt.get("duration") or (video or audio or {}).get("duration")
    return ProbeInfo(
        media_type="video" if video is not None else "audio",
        format_name=fmt.get("format_name", ""),
        duration=float(duration) if duration else None,
        width=width,
        height=height,
        video_codec=video.get("codec_name") if video else None,
        pix_fmt=video.get("pix_fmt") if video else None,
        is_hdr=bool(video and video.get("color_transfer") in HDR_TRANSFERS),
        audio_codec=audio.get("codec_name") if audio else None,
        creation_time=created,
    )


def probe(path: Path, ffprobe: str = "ffprobe") -> ProbeInfo:
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except FileNotFoundError as exc:
        raise MediaError(f"{ffprobe} is not installed") from exc
    if out.returncode != 0:
        raise MediaError("This isn't a playable audio or video file")
    return parse_probe(json.loads(out.stdout or "{}"))


# --- recorded date ------------------------------------------------------------------

_FILENAME_TS = re.compile(
    r"(?<!\d)(20\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])"
    r"[ _T.-]?([01]\d|2[0-3])[-_.:h]?([0-5]\d)[-_.:m]?([0-5]\d)"
)


def get_zone(name: str | None, fallback: str = "UTC") -> ZoneInfo:
    for candidate in (name, fallback, "UTC"):
        if not candidate:
            continue
        try:
            return ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return ZoneInfo("UTC")


def date_from_filename(filename: str, zone: ZoneInfo) -> datetime | None:
    """`20261102_113414.mp4`, `PXL_20261123_141500123.mp4`, `VID_2026-11-23 14.15.00.mp4` -> UTC.
    File names carry the phone's local time, so they're read in the uploader's time zone."""
    m = _FILENAME_TS.search(PurePosixPath(filename.replace("\\", "/")).name)
    if not m:
        return None
    try:
        local = datetime(*map(int, m.groups()), tzinfo=zone)
    except ValueError:
        return None
    return local.astimezone(UTC)


def _plausible(dt: datetime | None, now: datetime) -> datetime | None:
    if dt is None or dt < EARLIEST_PLAUSIBLE or dt > now + timedelta(days=1):
        return None
    return dt


def resolve_recorded_at(
    *,
    meta: datetime | None,
    filename: str,
    duration: float | None,
    last_modified: datetime | None,
    zone: ZoneInfo,
    now: datetime | None = None,
) -> tuple[datetime | None, str | None]:
    """Pick when the recording was made (spec 3.2). Returns (UTC time, source).

    Order: file metadata, then a timestamp in the file name, then the file's
    modified date. When metadata and the file name agree, the file name wins
    because it is the start of the recording; Samsung phones write the *end*
    time into creation_time, and some phones write local time labeled as UTC.
    """
    now = now or datetime.now(UTC)
    meta = _plausible(meta, now)
    from_name = _plausible(date_from_filename(filename, zone), now)
    length = timedelta(seconds=duration or 0)
    slack = timedelta(minutes=2)

    if meta and from_name:
        if from_name - slack <= meta <= from_name + length + slack:
            return from_name, "filename"  # metadata confirms it; the name has the start time
        naive_meta_as_local = meta.replace(tzinfo=zone).astimezone(UTC)
        if from_name - slack <= naive_meta_as_local <= from_name + length + slack:
            return from_name, "filename"  # metadata was local time mislabeled as UTC
        return meta, "metadata"
    if meta:
        return meta, "metadata"
    if from_name:
        return from_name, "filename"
    last_modified = _plausible(last_modified, now)
    if last_modified:
        return last_modified, "file_date"
    return None, None


# --- file layout -----------------------------------------------------------------------


def folder_for(slug: str | None) -> str:
    return slug or INBOX_DIR


def original_name(recorded_local: datetime, uid: str, ext: str) -> str:
    return f"{recorded_local:%Y-%m-%d_%H%M%S}_{uid}.{ext}"


def derived_path(original_rel: str, suffix: str) -> str:
    """`song/2026-11-23_141500_ab12cd34.mp4` + `.thumb.jpg` -> `song/2026-11-23_141500_ab12cd34.thumb.jpg`"""
    p = PurePosixPath(original_rel)
    return str(p.with_name(p.stem + suffix))


def resolve(media_dir: Path, rel: str) -> Path:
    root = media_dir.resolve()
    path = (root / rel).resolve()
    if not path.is_relative_to(root):
        raise MediaError("Path outside the media folder")
    return path


def move_file(src: Path, dest: Path) -> None:
    """Rename when possible; copy + delete across disks (shutil.move handles both)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise MediaError(f"{dest.name} already exists")
    shutil.move(str(src), str(dest))


# --- ffmpeg ----------------------------------------------------------------------------


def _nice(cmd: list[str]) -> list[str]:
    """Run below normal priority on the server so Plex/Frigate stay responsive."""
    nice = shutil.which("nice")
    return [nice, "-n", "10", *cmd] if nice else cmd


def run_ffmpeg(args: list[str], ffmpeg: str = "ffmpeg", timeout: float = 4 * 3600) -> None:
    cmd = _nice([ffmpeg, "-nostdin", "-hide_banner", "-v", "error", "-y", *args])
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except FileNotFoundError as exc:
        raise MediaError(f"{ffmpeg} is not installed") from exc
    if out.returncode != 0:
        raise MediaError((out.stderr or "ffmpeg failed").strip()[-2000:])


def _fit(max_side: int) -> str:
    # Shrink so the longest side is at most max_side; never upscale; keep even dimensions.
    return (
        f"scale=w='if(gt(iw,ih),min({max_side},iw),-2)':h='if(gt(iw,ih),-2,min({max_side},ih))'"
    )


def thumbnail_args(src: Path, dest: Path, info: ProbeInfo, at: float | None = None) -> list[str]:
    """A poster frame `at` seconds into the file (default: 3 s in, or 10% of a short one)."""
    if at is None:
        at = min(3.0, (info.duration or 0) * 0.1)
    filters = [TONEMAP] if info.is_hdr else []
    filters.append(_fit(THUMB_MAX_SIDE))
    return ["-ss", f"{at:.2f}", "-i", str(src), "-frames:v", "1", "-vf", ",".join(filters), "-q:v", "4", str(dest)]


def playback_plan(info: ProbeInfo, ext: str) -> Literal["original", "remux", "transcode"]:
    """Decide how to make a copy every browser can play. A file without a length (a WebM
    straight from a browser's recorder, spec 5.7) always gets a copy, which has one."""
    if info.media_type == "audio":
        return "original" if info.audio_codec in BROWSER_AUDIO and info.duration else "transcode"
    h264_ok = info.video_codec == "h264" and info.pix_fmt in ("yuv420p", "yuvj420p") and not info.is_hdr
    audio_ok = info.audio_codec in (None, "aac", "mp3")
    if h264_ok and audio_ok:
        return "original" if ext in ("mp4", "m4v") and info.duration else "remux"
    return "transcode"


def playback_args(src: Path, dest: Path, info: ProbeInfo, plan: str) -> list[str]:
    if info.media_type == "audio":
        return ["-i", str(src), "-vn", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(dest)]
    if plan == "remux":
        return ["-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", "-movflags", "+faststart", str(dest)]
    filters = [TONEMAP] if info.is_hdr else []
    filters += [_fit(PLAYBACK_MAX_SIDE), "format=yuv420p"]
    audio = ["-c:a", "copy"] if info.audio_codec == "aac" else ["-c:a", "aac", "-b:a", "160k", "-ac", "2"]
    return [
        "-i", str(src),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", ",".join(filters),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-profile:v", "high", "-threads", "4",
        *audio,
        "-movflags", "+faststart",
        str(dest),
    ]  # fmt: skip


# --- extras (spec 5.4, 5.9): audio-only copies, preview strips, waveforms -----------------


def audio_copy_args(src: Path, dest: Path, info: ProbeInfo) -> list[str]:
    """The sound of a video on its own, for listening in the car or on headphones."""
    audio = ["-c:a", "copy"] if info.audio_codec == "aac" else ["-c:a", "aac", "-b:a", "160k", "-ac", "2"]
    return ["-i", str(src), "-map", "0:a:0", "-vn", *audio, "-movflags", "+faststart", str(dest)]


def preview_grid(duration: float | None) -> tuple[int, int]:
    """(columns, rows) of the preview grid: about a frame a second, 4 to 60 frames, filling
    whole rows of 10 ((0, 0) when the length is unknown). Frame k shows the moment
    k / frames of the way through."""
    if not duration or duration <= 0:
        return (0, 0)
    frames = max(4, min(PREVIEW_MAX_FRAMES, round(duration)))
    columns = min(frames, PREVIEW_COLUMNS)
    return (columns, -(-frames // columns))


def preview_args(
    src: Path, dest: Path, info: ProbeInfo, grid: tuple[int, int], duration: float, *, keyframes_only: bool
) -> list[str]:
    """Evenly spaced frames in a grid in one JPEG. A slightly higher frame rate than needed
    makes sure the grid fills before the file ends. Decoding only keyframes is fast for
    long phone videos; short clips may have too few, and are decoded in full."""
    columns, rows = grid
    rate = columns * rows / max(duration * 0.98, 0.1)
    filters = [TONEMAP] if info.is_hdr else []
    filters += [f"fps={rate:.5f}", f"scale=-2:{PREVIEW_HEIGHT}", f"tile={columns}x{rows}", "format=yuvj420p"]
    skip = ["-skip_frame", "nokey"] if keyframes_only else []
    return [*skip, "-i", str(src), "-an", "-vf", ",".join(filters), "-frames:v", "1", "-q:v", "5", str(dest)]


def cut_args(src: Path, dest: Path, start: float, end: float) -> list[str]:
    """A stretch of a file without re-encoding (spec 5.10): instant and at full quality.
    Video can only start on a keyframe, so it starts at the one before `start` (the
    user's phone writes one every 1.1 s); audio is cut within a few milliseconds."""
    mp4_like = dest.suffix.lower() in (".mp4", ".m4v", ".mov", ".m4a", ".3gp")
    return [
        "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{max(end - start, 0.01):.3f}",
        "-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy", "-avoid_negative_ts", "make_zero",
        *(["-movflags", "+faststart"] if mp4_like else []),
        str(dest),
    ]  # fmt: skip


def peaks(src: Path, ffmpeg: str = "ffmpeg") -> list[int]:
    """The detailed waveform: the loudest point of every 1/PEAKS_PER_SECOND s of the
    recording, 0-100 (scaled so the loudest is 100)."""
    cmd = _nice([ffmpeg, "-nostdin", "-hide_banner", "-v", "error", "-i", str(src),
                 "-vn", "-ac", "1", "-ar", str(PEAKS_RATE), "-f", "s16le", "-"])  # fmt: skip
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=3600)
    except FileNotFoundError as exc:
        raise MediaError(f"{ffmpeg} is not installed") from exc
    if out.returncode != 0:
        raise MediaError(out.stderr.decode("utf-8", "replace").strip()[-2000:] or "ffmpeg failed")
    samples = array("h")
    samples.frombytes(out.stdout[: len(out.stdout) // 2 * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    if len(samples) == 0:
        raise MediaError("No sound to draw")
    step = PEAKS_RATE // PEAKS_PER_SECOND
    values = []
    for i in range(0, len(samples), step):
        chunk = samples[i : i + step]
        values.append(max(max(chunk), -min(chunk)))
    top = max(values) or 1
    return [round(v * 100 / top) for v in values]


def clip_waveform(points: list[int], start: float, end: float | None, bars: int = WAVEFORM_BARS) -> list[int]:
    """The waveform of a clip's stretch, as `bars` bars scaled so its loudest is 100."""
    first = int(start * PEAKS_PER_SECOND)
    last = len(points) if end is None else max(first + 1, int(end * PEAKS_PER_SECOND))
    stretch = points[first:last] or [0]
    out = []
    for i in range(bars):
        chunk = stretch[i * len(stretch) // bars : max((i + 1) * len(stretch) // bars, i * len(stretch) // bars + 1)]
        out.append(max(chunk) if chunk else 0)
    top = max(out) or 1
    return [round(v * 100 / top) for v in out]
