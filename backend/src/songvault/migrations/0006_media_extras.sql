-- Media extras (spec 5.4, 5.9): audio-only copies of videos (clips.audio_path, there since
-- 0001), a strip of frames for scrubbing on a tile, and waveforms of audio clips.

ALTER TABLE clips ADD COLUMN preview_path TEXT;      -- <name>.preview.jpg: frames side by side
ALTER TABLE clips ADD COLUMN preview_frames INTEGER; -- how many frames are in the strip
-- Loudest point in each of 120 slices of an audio clip, 0-100.
ALTER TABLE clips ADD COLUMN waveform TEXT CHECK (waveform IS NULL OR json_valid(waveform));

-- Make them for the clips already stored (Trash included: they can come back).
INSERT INTO jobs (clip_id, type, state, created_at, updated_at)
SELECT c.id, t.type, 'queued', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
FROM clips c
JOIN (SELECT 'audio' AS type, 'video' AS media_type
      UNION ALL SELECT 'preview', 'video'
      UNION ALL SELECT 'waveform', 'audio') t ON t.media_type = c.media_type
ORDER BY c.id, t.type;
