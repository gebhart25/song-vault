-- Strumming patterns in the song notes (spec 5.2).

-- [{"label": "Verse", "pattern": "D - D U - U D U"}, {"label": "", "pattern": "D D U"}]
-- The label is optional; the pattern is kept as typed (D = down, U = up, X = muted, - = miss).
ALTER TABLE projects ADD COLUMN strumming TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(strumming));
