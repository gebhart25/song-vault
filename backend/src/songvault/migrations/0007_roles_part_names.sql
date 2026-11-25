-- Roles and per-song part names (spec 3.3, 5.1).

-- What a take is (Rhythm, Lead, Vocals...). One Current take per part and role, so the
-- rhythm and lead takes of a solo can both be Current.
ALTER TABLE clips ADD COLUMN role TEXT;

-- A part's name in this song only, e.g. the built-in Verse shown as "Verse 1" once the
-- song has a "Verse 2". NULL = the part's own name.
ALTER TABLE project_label_order ADD COLUMN name TEXT;
