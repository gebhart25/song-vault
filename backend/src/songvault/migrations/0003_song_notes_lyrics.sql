-- Song notes and lyrics (spec 5.2, 5.6).

-- Labeled chord progressions: [{"label": "Verse", "chords": ["D", "Bm", "G", "A"]}, ...]
ALTER TABLE projects ADD COLUMN progressions TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(progressions));
-- Reference songs or artists for the feel ("Tom Petty, but slower").
ALTER TABLE projects ADD COLUMN sounds_like TEXT;

-- One chord/lyric sheet per project.
CREATE TABLE lyrics (
    project_id INTEGER PRIMARY KEY REFERENCES projects (id) ON DELETE CASCADE,
    -- [{"id": "...", "label": "Verse 1", "text": "[D]Hello [Bm]darkness..."}, ...]
    -- Chords sit inline in square brackets before the syllable they land on (ChordPro style).
    sections   TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(sections)),
    plain_text TEXT NOT NULL DEFAULT '',   -- labels and words without chords, for search
    revision   INTEGER NOT NULL DEFAULT 0, -- bumped on every save; stale saves are refused
    updated_at TEXT NOT NULL
) STRICT;
