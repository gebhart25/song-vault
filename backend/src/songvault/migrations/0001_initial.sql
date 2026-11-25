-- Song Vault schema v1. See song-vault-spec.md section 6.
-- Times are ISO 8601 UTC strings ("2026-11-23T14:15:00.000Z").

CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;

CREATE TABLE sessions (
    token_hash   TEXT PRIMARY KEY,   -- sha256 of the cookie value; the token itself is never stored
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
) STRICT;

CREATE TABLE projects (
    id             INTEGER PRIMARY KEY,
    name           TEXT NOT NULL,
    slug           TEXT NOT NULL UNIQUE,    -- media folder name: songvault/<slug>/
    status         TEXT NOT NULL DEFAULT 'idea'
                   CHECK (status IN ('idea', 'writing', 'arranging', 'recorded', 'done', 'shelved')),
    song_key       TEXT,
    bpm            REAL,
    tuning         TEXT,
    capo           INTEGER,
    time_signature TEXT,
    notes          TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    archived       INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1)),
    deleted_at     TEXT                     -- set = in Trash
) STRICT;

CREATE TABLE clips (
    id                 INTEGER PRIMARY KEY,
    uid                TEXT NOT NULL UNIQUE,      -- short random id used in file names
    project_id         INTEGER REFERENCES projects (id),  -- NULL = Unsorted inbox
    title              TEXT NOT NULL DEFAULT '',
    notes              TEXT NOT NULL DEFAULT '',
    status             TEXT NOT NULL DEFAULT 'idea'
                       CHECK (status IN ('idea', 'current', 'used', 'discarded')),
    recorded_at        TEXT,
    recorded_at_source TEXT CHECK (recorded_at_source IN ('metadata', 'filename', 'file_date', 'manual')),
    uploaded_at        TEXT NOT NULL,
    original_filename  TEXT NOT NULL,
    media_type         TEXT NOT NULL CHECK (media_type IN ('video', 'audio')),
    duration_seconds   REAL,
    width              INTEGER,
    height             INTEGER,
    video_codec        TEXT,
    is_hdr             INTEGER CHECK (is_hdr IN (0, 1)),
    original_path      TEXT NOT NULL,             -- relative to the media dir
    playback_path      TEXT,
    audio_path         TEXT,
    thumbnail_path     TEXT,
    size_bytes         INTEGER NOT NULL,
    sha256             TEXT NOT NULL,
    verified_at        TEXT,                      -- set once size and checksum matched
    deleted_at         TEXT                       -- set = in Trash
) STRICT;

CREATE INDEX clips_project ON clips (project_id, deleted_at);

CREATE TABLE labels (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    project_id    INTEGER REFERENCES projects (id) ON DELETE CASCADE,  -- NULL = global
    default_order INTEGER NOT NULL DEFAULT 0,
    is_full_song  INTEGER NOT NULL DEFAULT 0 CHECK (is_full_song IN (0, 1)),
    builtin       INTEGER NOT NULL DEFAULT 0 CHECK (builtin IN (0, 1))
) STRICT;

CREATE UNIQUE INDEX labels_unique_name ON labels (coalesce(project_id, 0), name COLLATE NOCASE);

CREATE TABLE clip_labels (
    clip_id  INTEGER NOT NULL REFERENCES clips (id) ON DELETE CASCADE,
    label_id INTEGER NOT NULL REFERENCES labels (id) ON DELETE CASCADE,
    PRIMARY KEY (clip_id, label_id)
) STRICT;

CREATE TABLE project_label_order (
    project_id INTEGER NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    label_id   INTEGER NOT NULL REFERENCES labels (id) ON DELETE CASCADE,
    position   INTEGER NOT NULL,
    collapsed  INTEGER NOT NULL DEFAULT 0 CHECK (collapsed IN (0, 1)),
    PRIMARY KEY (project_id, label_id)
) STRICT;

CREATE TABLE current_picks (
    project_id INTEGER NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    label_id   INTEGER NOT NULL REFERENCES labels (id) ON DELETE CASCADE,
    clip_id    INTEGER NOT NULL REFERENCES clips (id) ON DELETE CASCADE,
    PRIMARY KEY (project_id, label_id)
) STRICT;

CREATE TABLE bookmarks (
    id           INTEGER PRIMARY KEY,
    clip_id      INTEGER NOT NULL REFERENCES clips (id) ON DELETE CASCADE,
    time_seconds REAL NOT NULL,
    note         TEXT NOT NULL DEFAULT ''
) STRICT;

CREATE TABLE jobs (
    id         INTEGER PRIMARY KEY,
    clip_id    INTEGER NOT NULL REFERENCES clips (id) ON DELETE CASCADE,
    type       TEXT NOT NULL,
    state      TEXT NOT NULL DEFAULT 'queued' CHECK (state IN ('queued', 'running', 'done', 'failed')),
    attempts   INTEGER NOT NULL DEFAULT 0,
    error      TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
) STRICT;

CREATE INDEX jobs_state ON jobs (state, id);

-- Default song parts (spec 3.3). Full Song is always pinned to the top of a song page.
INSERT INTO labels (name, default_order, is_full_song, builtin) VALUES
    ('Full Song',   0, 1, 1),
    ('Intro',       1, 0, 1),
    ('Verse',       2, 0, 1),
    ('Pre-Chorus',  3, 0, 1),
    ('Chorus',      4, 0, 1),
    ('Bridge',      5, 0, 1),
    ('Solo',        6, 0, 1),
    ('Lead Ideas',  7, 0, 1),
    ('Riff',        8, 0, 1),
    ('Outro',       9, 0, 1),
    ('Lyrics',     10, 0, 1),
    ('Misc',       11, 0, 1);
