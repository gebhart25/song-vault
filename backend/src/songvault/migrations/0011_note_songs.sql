-- Notebook notes linked to songs (spec 5.11, step 2). A note can be in several songs.
-- Purging a note or a song removes its links.

CREATE TABLE note_songs (
    note_id    INTEGER NOT NULL REFERENCES notes (id) ON DELETE CASCADE,
    project_id INTEGER NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    added_at   TEXT NOT NULL,
    PRIMARY KEY (note_id, project_id)
) STRICT;

CREATE INDEX note_songs_project ON note_songs (project_id);
