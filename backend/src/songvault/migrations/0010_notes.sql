-- The Notebook (spec 5.11): lyric lines, rhymes, titles and song ideas. Notes are
-- written on a device, often offline, and synced later (see notes.py).

CREATE TABLE notes (
    id         INTEGER PRIMARY KEY,
    uid        TEXT NOT NULL UNIQUE,      -- made on the device that wrote it
    body       TEXT NOT NULL DEFAULT '',  -- plain text; #tags are read from it
    starred    INTEGER NOT NULL DEFAULT 0 CHECK (starred IN (0, 1)),
    status     TEXT NOT NULL DEFAULT 'fresh' CHECK (status IN ('fresh', 'used')),
    revision   INTEGER NOT NULL DEFAULT 1,  -- up by one with every change
    copy_of    TEXT,                      -- "Edited on two devices": the uid of the other version
    seq        INTEGER NOT NULL,          -- note_clock.seq at its last change, for syncing
    created_at TEXT NOT NULL,             -- when it was written (the device's clock)
    updated_at TEXT NOT NULL,
    deleted_at TEXT                       -- set = in Trash
) STRICT;

CREATE INDEX notes_seq ON notes (seq);

-- Every change to a note takes the next number from `seq`, and a device asks for the
-- changes after the last number it saw. `purged_seq` is the highest number among notes
-- deleted for good: a device that last synced before it gets the whole notebook again,
-- since there's nothing left to tell it those notes are gone.
CREATE TABLE note_clock (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    seq        INTEGER NOT NULL,
    purged_seq INTEGER NOT NULL
) STRICT;

INSERT INTO note_clock (id, seq, purged_seq) VALUES (1, 0, 0);
