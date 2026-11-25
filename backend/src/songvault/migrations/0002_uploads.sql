-- Resumable uploads (tus). The partial file lives at <media>/.uploads/<id>.part;
-- its size on disk is the upload offset.

CREATE TABLE uploads (
    id            TEXT PRIMARY KEY,           -- random token used in the upload URL
    length        INTEGER NOT NULL,
    filename      TEXT NOT NULL,
    filetype      TEXT NOT NULL DEFAULT '',
    last_modified TEXT,                       -- the file's modified time reported by the browser
    timezone      TEXT,                       -- the browser's IANA time zone, for dates in file names
    project_id    INTEGER REFERENCES projects (id) ON DELETE SET NULL,
    label_ids     TEXT NOT NULL DEFAULT '[]', -- JSON array
    created_at    TEXT NOT NULL,
    completed_at  TEXT,                       -- set once verified and turned into a clip
    clip_id       INTEGER REFERENCES clips (id) ON DELETE SET NULL
) STRICT;

CREATE INDEX clips_uploaded ON clips (uploaded_at);
CREATE INDEX jobs_clip ON jobs (clip_id, state);
