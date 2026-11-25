-- Clip editing (spec 5.10): a clip is a stretch of a recording.
--
-- A recording is one uploaded file, never changed. Trimming moves a clip's start or
-- end; splitting adds clips on the same recording. Each existing clip becomes a
-- recording with the same id, plus that clip covering all of it.

CREATE TABLE recordings (
    id                 INTEGER PRIMARY KEY,
    uid                TEXT NOT NULL UNIQUE,      -- used in the file names
    original_filename  TEXT NOT NULL,
    media_type         TEXT NOT NULL CHECK (media_type IN ('video', 'audio')),
    duration_seconds   REAL,
    width              INTEGER,
    height             INTEGER,
    video_codec        TEXT,
    is_hdr             INTEGER CHECK (is_hdr IN (0, 1)),
    recorded_at        TEXT,                      -- when the file starts
    recorded_at_source TEXT CHECK (recorded_at_source IN ('metadata', 'filename', 'file_date', 'manual')),
    uploaded_at        TEXT NOT NULL,
    size_bytes         INTEGER NOT NULL,
    sha256             TEXT NOT NULL,
    verified_at        TEXT,                      -- set once size and checksum matched
    original_path      TEXT NOT NULL,             -- relative to the media dir
    playback_path      TEXT,                      -- whole-recording copies, shared by its clips
    audio_path         TEXT,
    preview_path       TEXT,                      -- frames in a grid, 10 across (spec 5.4)
    preview_frames     INTEGER,
    peaks_path         TEXT                       -- detailed waveform for the editor, 20 points a second
) STRICT;

CREATE INDEX recordings_sha256 ON recordings (sha256);
CREATE INDEX recordings_uploaded ON recordings (uploaded_at);

INSERT INTO recordings (id, uid, original_filename, media_type, duration_seconds, width, height, video_codec,
    is_hdr, recorded_at, recorded_at_source, uploaded_at, size_bytes, sha256, verified_at, original_path,
    playback_path, audio_path)
SELECT id, uid, original_filename, media_type, duration_seconds, width, height, video_codec,
    is_hdr, recorded_at, recorded_at_source, uploaded_at, size_bytes, sha256, verified_at, original_path,
    playback_path, audio_path
FROM clips;
-- The old one-row preview strips are remade as grids (same file names, overwritten).

ALTER TABLE clips ADD COLUMN recording_id INTEGER REFERENCES recordings (id);
ALTER TABLE clips ADD COLUMN start_seconds REAL NOT NULL DEFAULT 0;
ALTER TABLE clips ADD COLUMN end_seconds REAL;        -- NULL = to the end of the recording
ALTER TABLE clips ADD COLUMN thumbnail_at REAL;       -- chosen frame, seconds into the clip; NULL = automatic
UPDATE clips SET recording_id = id;
CREATE INDEX clips_recording ON clips (recording_id);

ALTER TABLE clips DROP COLUMN original_filename;
ALTER TABLE clips DROP COLUMN width;
ALTER TABLE clips DROP COLUMN height;
ALTER TABLE clips DROP COLUMN video_codec;
ALTER TABLE clips DROP COLUMN is_hdr;
ALTER TABLE clips DROP COLUMN original_path;
ALTER TABLE clips DROP COLUMN playback_path;
ALTER TABLE clips DROP COLUMN audio_path;
ALTER TABLE clips DROP COLUMN size_bytes;
ALTER TABLE clips DROP COLUMN sha256;
ALTER TABLE clips DROP COLUMN verified_at;
ALTER TABLE clips DROP COLUMN preview_path;
ALTER TABLE clips DROP COLUMN preview_frames;

-- Jobs now belong to a recording (copies, preview, waveform) or a clip (its thumbnail).
-- The queue is rebuilt; finished jobs aren't needed any more.
DROP TABLE jobs;
CREATE TABLE jobs (
    id           INTEGER PRIMARY KEY,
    recording_id INTEGER REFERENCES recordings (id) ON DELETE CASCADE,
    clip_id      INTEGER REFERENCES clips (id) ON DELETE CASCADE,
    type         TEXT NOT NULL,
    state        TEXT NOT NULL DEFAULT 'queued' CHECK (state IN ('queued', 'running', 'done', 'failed')),
    attempts     INTEGER NOT NULL DEFAULT 0,
    error        TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    CHECK ((recording_id IS NULL) <> (clip_id IS NULL))
) STRICT;
CREATE INDEX jobs_state ON jobs (state, id);
CREATE INDEX jobs_recording ON jobs (recording_id, state);
CREATE INDEX jobs_clip ON jobs (clip_id, state);

-- Queue what existing recordings now need: anything missing, plus the new grid preview
-- strips and detailed waveforms.
INSERT INTO jobs (recording_id, type, created_at, updated_at)
SELECT r.id, t.type, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
FROM recordings r
JOIN (SELECT 1 AS n, 'playback' AS type UNION ALL SELECT 2, 'audio' UNION ALL SELECT 3, 'preview'
      UNION ALL SELECT 4, 'peaks') t
  ON (t.type = 'playback' AND r.playback_path IS NULL)
  OR (t.type = 'audio' AND r.media_type = 'video' AND r.audio_path IS NULL)
  OR (t.type = 'preview' AND r.media_type = 'video')
  OR t.type = 'peaks'
ORDER BY r.id, t.n;

INSERT INTO jobs (clip_id, type, created_at, updated_at)
SELECT id, 'thumbnail', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
FROM clips WHERE media_type = 'video' AND thumbnail_path IS NULL;
