-- Per-clip details (spec 5.2): a take can use a different key, tempo or tuning than the song.
ALTER TABLE clips ADD COLUMN song_key TEXT;
ALTER TABLE clips ADD COLUMN bpm REAL;
ALTER TABLE clips ADD COLUMN time_signature TEXT;
ALTER TABLE clips ADD COLUMN capo INTEGER;
ALTER TABLE clips ADD COLUMN tuning TEXT;
-- Chord progressions without labels, since the clip's parts already say where they go:
-- [["D", "Bm", "G", "A"], ["G", "A", "Bm"]]
ALTER TABLE clips ADD COLUMN progressions TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(progressions));
