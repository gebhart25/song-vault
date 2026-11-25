-- Song artwork on the Projects page (spec 3.1): a stock picture that ships with the app,
-- or an image of the user's own (a copy of a clip's thumbnail, or an upload) kept in
-- .covers/ in the media folder. Neither set = automatic: the song's newest thumbnail.
ALTER TABLE projects ADD COLUMN cover_stock TEXT;
ALTER TABLE projects ADD COLUMN cover_path TEXT;
-- The clip a copied thumbnail came from, to show which one is picked.
ALTER TABLE projects ADD COLUMN cover_clip_id INTEGER REFERENCES clips(id) ON DELETE SET NULL;
