-- What kind of song a project is (spec 3.1, added 2026-11-27): the Projects page filters
-- by it. Existing songs are originals until set otherwise.

ALTER TABLE projects ADD COLUMN kind TEXT NOT NULL DEFAULT 'original'
    CHECK (kind IN ('original', 'cowrite', 'cover', 'arrangement', 'practice'));
