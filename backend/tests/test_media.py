"""Recorded-date rules and ffprobe parsing. The cases mirror real files from the user's
Samsung phone and voice recorder (see samples/ in the repo root, not committed)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from songvault import media

CHICAGO = media.get_zone("America/Chicago")
NOW = datetime(2026, 11, 24, tzinfo=UTC)


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def resolve(**kw):
    kw.setdefault("duration", None)
    kw.setdefault("last_modified", None)
    kw.setdefault("zone", CHICAGO)
    return media.resolve_recorded_at(now=NOW, **kw)


def test_samsung_video_uses_start_time_from_file_name() -> None:
    # 20261102_113414.mp4: name = local start, creation_time = UTC end (start + 95 s).
    assert resolve(meta=utc("2026-11-02T17:35:50Z"), filename="20261102_113414.mp4", duration=95.1) == (
        utc("2026-11-02T17:34:14Z"),
        "filename",
    )


def test_voice_recording_uses_metadata() -> None:
    assert resolve(meta=utc("2026-11-17T03:15:05Z"), filename="Recording.m4a", duration=174.6) == (
        utc("2026-11-17T03:15:05Z"),
        "metadata",
    )


def test_pixel_style_name_with_milliseconds() -> None:
    assert resolve(meta=utc("2026-11-23T20:15:00Z"), filename="PXL_20261123_141500123.mp4", duration=30) == (
        utc("2026-11-23T20:15:00Z"),
        "filename",
    )


def test_local_time_mislabeled_as_utc_is_caught() -> None:
    assert resolve(meta=utc("2026-11-23T14:15:30Z"), filename="VID_20261123_141500.mp4", duration=60) == (
        utc("2026-11-23T20:15:00Z"),
        "filename",
    )


def test_metadata_wins_when_the_name_disagrees() -> None:
    # e.g. a renamed file
    assert resolve(meta=utc("2026-11-23T20:15:00Z"), filename="take_20200101_000000.mp4", duration=60) == (
        utc("2026-11-23T20:15:00Z"),
        "metadata",
    )


def test_name_only_and_file_date_fallbacks() -> None:
    assert resolve(meta=None, filename="2026-11-23 14.15.00.mp4") == (utc("2026-11-23T20:15:00Z"), "filename")
    assert resolve(meta=None, filename="Recording (2).m4a", last_modified=utc("2026-11-18T22:16:55Z")) == (
        utc("2026-11-18T22:16:55Z"),
        "file_date",
    )
    assert resolve(meta=None, filename="idea.mp3") == (None, None)


@pytest.mark.parametrize("bad", ["1970-01-01T00:00:00Z", "1904-01-01T00:00:00Z", "2030-01-01T00:00:00Z"])
def test_implausible_metadata_is_ignored(bad: str) -> None:
    assert resolve(meta=utc(bad), filename="idea.m4a") == (None, None)


def test_daylight_saving_is_applied() -> None:
    # Summer: Chicago is UTC-5, winter UTC-6.
    assert media.date_from_filename("20260704_120000.mp4", CHICAGO) == utc("2026-07-04T17:00:00Z")
    assert media.date_from_filename("20261225_120000.mp4", CHICAGO) == utc("2026-12-25T18:00:00Z")


def test_unknown_time_zone_falls_back() -> None:
    assert media.get_zone("Not/AZone", "America/Chicago").key == "America/Chicago"
    assert media.get_zone(None, "nonsense").key == "UTC"


def _probe(streams: list[dict], tags: dict | None = None) -> media.ProbeInfo:
    return media.parse_probe({"format": {"format_name": "mov,mp4", "duration": "10.0", "tags": tags or {}}, "streams": streams})


def test_parse_probe_portrait_samsung_video() -> None:
    info = _probe(
        [
            {
                "codec_type": "video", "codec_name": "hevc", "width": 1920, "height": 1080, "pix_fmt": "yuv420p",
                "color_transfer": "bt709", "side_data_list": [{"rotation": -90}],
            },
            {"codec_type": "audio", "codec_name": "aac"},
        ],
        {"creation_time": "2026-11-02T17:33:04.000000Z"},
    )  # fmt: skip
    assert (info.media_type, info.width, info.height) == ("video", 1080, 1920)
    assert (info.video_codec, info.audio_codec, info.is_hdr) == ("hevc", "aac", False)
    assert info.creation_time == utc("2026-11-02T17:33:04Z")
    assert info.duration == 10.0


def test_parse_probe_cover_art_is_not_video() -> None:
    info = _probe(
        [
            {"codec_type": "audio", "codec_name": "mp3"},
            {"codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}},
        ]
    )
    assert info.media_type == "audio"


def test_parse_probe_hdr_and_apple_date() -> None:
    info = _probe(
        [{"codec_type": "video", "codec_name": "hevc", "pix_fmt": "yuv420p10le", "color_transfer": "arib-std-b67"}],
        {"creation_time": "2026-11-23T20:15:03Z", "com.apple.quicktime.creationdate": "2026-11-23T14:15:00-0600"},
    )
    assert info.is_hdr is True
    assert info.creation_time == utc("2026-11-23T20:15:00Z")


def test_parse_probe_rejects_files_without_streams() -> None:
    with pytest.raises(media.MediaError):
        _probe([{"codec_type": "data"}])


def info(**kw) -> media.ProbeInfo:
    base = dict(
        media_type="video", format_name="mov,mp4", duration=10.0, width=1920, height=1080,
        video_codec="h264", pix_fmt="yuv420p", is_hdr=False, audio_codec="aac", creation_time=None,
    )  # fmt: skip
    return media.ProbeInfo(**{**base, **kw})


def test_playback_plan() -> None:
    assert media.playback_plan(info(), "mp4") == "original"
    assert media.playback_plan(info(), "mov") == "remux"
    assert media.playback_plan(info(video_codec="hevc"), "mp4") == "transcode"
    assert media.playback_plan(info(pix_fmt="yuv420p10le"), "mp4") == "transcode"
    assert media.playback_plan(info(is_hdr=True), "mp4") == "transcode"
    assert media.playback_plan(info(audio_codec="opus"), "mp4") == "transcode"
    assert media.playback_plan(info(media_type="audio", video_codec=None, audio_codec="aac"), "m4a") == "original"
    assert media.playback_plan(info(media_type="audio", video_codec=None, audio_codec="opus"), "ogg") == "transcode"


def test_paths() -> None:
    assert media.original_name(datetime(2026, 11, 2, 11, 34, 14), "ab12cd34", "mp4") == "2026-11-02_113414_ab12cd34.mp4"
    assert media.derived_path("song/2026-11-02_113414_ab12cd34.mp4", ".thumb.jpg") == "song/2026-11-02_113414_ab12cd34.thumb.jpg"
    assert media.extension("C:\\phone\\VID.MOV") == "mov"
