# Song Vault

A self-hosted web app for organizing rough video and audio recordings of song ideas: one project per song, one row per song part, newest takes on the left. Runs as a single Docker container on a home server (Unraid, a NAS, any Docker host); see [Self-hosting](#self-hosting).

Code comments that cite "spec 3.5" and so on refer to the design notes, which aren't part of this repository.

## Status

Phases 1 (the MVP), 2 (quality of life), 3 (extras), 4 (clip editing) and 5 (the Notebook) are built. Done so far:

- PIN setup and sign-in (4–6 digits, one-year session cookie), Change PIN in the account menu
- Projects: create, rename (renames the media folder too), status, archive, move to Trash
- Projects page cards: long-press / right-click to set the status or type or change the thumbnail; pictures from the song's clips, 20 stock artworks (`frontend/static/covers/`), a solid colour, or an upload you crop to the card's shape (pan and zoom)
- Song types (Original, Co-write, Cover, Arrangement, Practice) with a type filter on the Projects page, next to the status filter
- Song notes: key, BPM, time signature, capo, tuning, "sounds like", labeled chord progressions, strumming patterns (arrows with beat counts), general notes
- Lyrics & chords page: labeled sections, chord sheet view, tap-to-place chords (from the song's progressions or key; on a word, or before or after a line), autosave with conflict check; transpose (view or save in the new key), auto-scroll with the screen kept on, two-column view, the song's strumming patterns on the sheet, print view (in one or two columns, with strumming if it's on)
- Song page: one row per part, Full Song pinned at the top, rows can be rearranged and collapsed, custom parts per song, horizontal clip strips with the Current take pinned left, a **+** on each row to upload straight into that part
- Song page tools: Hide discarded, filters (parts, status, role, recorded dates), a long-press / right-click menu on each tile (Set as current, Edit parts, Role, Move to song, Notes, Move to Trash), and bulk select (move, add or remove a part, set a role or status, Trash)
- Roles on takes (Rhythm, Lead, Vocals…): one Current take per part and role, so parts played together can all be Current
- Each row's menu: record into the part, add a numbered repeat (Verse → Verse 1 + Verse 2), rename the part in this song only
- Uploads: resumable tus uploads, a SHA-256 computed in a Web Worker and checked by the server, duplicate detection, recorded dates from metadata / file name / file date
- Media jobs (ffmpeg, one at a time, low priority): per recording, 720p H.264 playback copies (browser-friendly files play as-is), audio-only copies of videos, scrub preview grids (hover a video tile) and detailed waveforms; per clip, poster thumbnails from the clip's stretch
- Player panel: its own controls (fullscreen included) that keep to the clip's stretch, playback at 0.5× / 0.75× / 1×, Audio only (keeps playing with the screen off), waveform seek bar for audio, A/B loop with 0.1 s nudges, an optional 5 s countdown before playing, status (Idea / Current / Used / Discarded, one Current take per part), title, song, parts, details (chord progressions, key, BPM, time signature, capo, tuning), recorded date, notes, Use this frame (thumbnail), download clip / whole recording, move to Trash
- Clip editing (**Trim & split**, in the player and the tile menu): a clip is a stretch of an untouched recording; trim dead space, split one take into pieces in different parts, drop the gaps, Find gaps (trim silence, split at gaps), keep the whole take too, undo / redo, a draggable marker kept within the piece with back-to-start and +15 s, re-edit any time
- Trash page: preview and restore deleted songs and recordings; anything left for 30 days is deleted for good
- Record in the app: video or audio only, straight into a song and part (the red button in the top bar), with an optional 5 s countdown; **Save & trim** opens the editor once the take is up
- Export a song as a zip: originals, trimmed and split clips cut out without re-encoding, song notes, a plain-text chord sheet, ChordPro and a JSON manifest
- Uncategorized (a card on the Projects page that opens a play-and-sort screen) and Upload history pages (marks files **Safe to delete** once the offsite backup has them; see [Backups](#backups))
- **Notebook** for lyric lines, rhymes, titles and song ideas: a Jot box that saves as you type, `#tags` with filter chips, search, stars, Move to top, Paste notes (a whole phone notepad, split at blank lines), Trash with Undo. It works offline: the notebook is kept on the device (IndexedDB) and synced by change number when the server is reachable, with edits from two devices kept side by side rather than overwritten. Text shared from other apps becomes a note, and the home-screen icon has a **New note** shortcut
- Notebook and songs: **Use in a song** (song chips on the note, a Notebook panel on the song page), **Start a song from this**, Used / Hide used, and **Combine notes** (pick several, numbered in the order picked, joined with a blank line). A song's ideas stay in its Notebook panel, out of the lyrics; its zip export includes them
- Writing helpers in the Notebook and the lyrics Write view: **Rhymes** (perfect and near, by syllables, everyday words first) for the word at the cursor, and **syllable counts** beside each line, from the [CMU Pronouncing Dictionary](http://www.speech.cs.cmu.edu/cgi-bin/cmudict) shipped with the app (BSD-style licence: `backend/src/songvault/data/cmudict-LICENSE.txt`), so the words never leave your server
- Installable app (PWA) with an app-shell cache (it opens without the server, in offline mode), and the **Android share target**: share videos from the gallery to Song Vault, pick the song and part, upload

## Layout

```
backend/    FastAPI app (Python 3.13, managed with uv)
  src/songvault/
    main.py         app factory, static frontend serving
    auth.py         PIN + sessions
    projects.py     projects, song notes and song-page rows
    lyrics.py       the lyrics page (sections with inline chords)
    labels.py       song parts
    clips.py        clip details/editing, Uncategorized, history, media files, cut downloads
    covers.py       song pictures for the Projects page: stock, a clip's thumbnail, or an upload
    recordings.py   the clip editor's API: a recording and its clips, save all pieces at once
    uploads.py      tus resumable uploads + checksum verification
    media.py        ffprobe, recorded-date rules, file layout, ffmpeg commands
    jobs.py         background worker: playback and audio copies, preview grids, waveforms (per recording); thumbnails (per clip)
    export.py       a song as a zip
    notes.py        the Notebook: notes synced from devices by change number, conflicts kept as copies
    words.py        rhymes and syllable counts (data/cmudict.dict.gz)
    db.py           SQLite connection + migrations
    migrations/     numbered .sql files (schema version = PRAGMA user_version)
  tests/
frontend/   SvelteKit 3 single-page app (Svelte 5, TypeScript), built to frontend/build
Dockerfile  single image: frontend build + backend + ffmpeg
```

## Development

Needs Node 24, [uv](https://docs.astral.sh/uv/), and ffmpeg/ffprobe on the PATH (the upload tests generate small media files with ffmpeg and are skipped without it).

```powershell
# backend: install and test
cd backend
uv sync
uv run pytest

# frontend: install, check, build
cd ../frontend
npm install
npm run check
npm run build
```

Run everything from one process (serves `frontend/build`):

```powershell
cd backend
uv run uvicorn --factory songvault.main:create_app --port 8000 --reload
```

Then open http://localhost:8000. For frontend work with hot reload, also run `npm run dev` in `frontend/` and open http://localhost:5173 (it proxies `/api` to port 8000).

Local data goes in `.dev/` (database in `.dev/data`, media in `.dev/media`). Delete the folder to start fresh. Locally, `uv run songvault reset-pin` in `backend/` clears the PIN.

### Configuration

| Variable | Default (dev) | In the container |
|---|---|---|
| `SONGVAULT_DATA_DIR` | `.dev/data` | `/data` (the database: mount a folder on a local disk) |
| `SONGVAULT_MEDIA_DIR` | `.dev/media` | `/media` (the recordings: mount your big storage) |
| `SONGVAULT_STATIC_DIR` | `frontend/build` | `/app/static` |
| `TZ` | `UTC` | e.g. `Europe/London`; reads dates in file names when the browser doesn't send its time zone |
| `SONGVAULT_FFMPEG` / `SONGVAULT_FFPROBE` | `ffmpeg` / `ffprobe` | same |

## Self-hosting

Song Vault is one Docker image: the backend, the built app and ffmpeg. There's no published image, so build it from a clone:

```bash
docker build -t songvault .
```

Then run it with two folders mounted:

```bash
docker run -d --name songvault --restart unless-stopped \
  -p 8095:8095 \
  -v /path/to/appdata/songvault:/data \
  -v /path/to/recordings:/media \
  -e TZ=Europe/London \
  songvault
```

- `/data` holds the SQLite database. Put it on a local disk, not a network share (on Unraid, an appdata folder on a pool such as `/mnt/cache/appdata/songvault` rather than a `/mnt/user/...` path).
- `/media` holds the recordings, playback copies and thumbnails, in one folder per song. This is the one that grows.
- The container runs as UID 99 / GID 100, so both folders must be writable by that user (`chown 99:100 <folder>`), or pass `--user <uid>:<gid>` for an account that owns them.
- `TZ` is only used to read times in file names when the uploading browser doesn't send its time zone.

Open `http://<server>:8095` and choose a PIN. Anyone who can reach the app needs the PIN, so keep it on your home network or behind a VPN rather than open to the internet.

To update, pull the new code, build the image again and recreate the container. The database upgrades itself when the app starts.

### HTTPS

The app works over plain HTTP, but installing it on a phone and the Android share target need HTTPS. Put it behind a reverse proxy (Caddy, Nginx Proxy Manager, Traefik and so on) that:

- sends `X-Forwarded-Proto` (the app then marks its sign-in cookie Secure), and
- allows request bodies of at least 32 MB, since uploads are sent in 32 MB pieces. Caddy has no limit by default; in nginx, set `client_max_body_size 64m;`.

A minimal Caddyfile, with a certificate from Let's Encrypt:

```
songvault.example.com {
	reverse_proxy <server>:8095
}
```

For a name that only resolves on your home network, Caddy can get the certificate with a DNS challenge instead (a Caddy build with your DNS provider's plugin).

### Backups

- **Database:** `docker exec songvault songvault snapshot-db /data/backup/songvault.db` writes a consistent copy while the app runs. Copy that file off the server, not the live `songvault.db`.
- **Recordings:** back up the whole `/media` folder. `.uploads/` (unfinished uploads) and `.cuts/` (temporary pieces for downloads) can be left out.
- **Safe to delete:** after a backup finishes, write `/media/.offsite-backup.json` containing `{"started_at": "2026-01-31T04:00:00Z", "finished_at": "2026-01-31T04:20:00Z"}` (UTC). The Upload history page then marks every upload verified before `started_at` as **Safe to delete** from the phone. Without the file, nothing is marked.

### Forgot the PIN

```bash
docker exec songvault songvault reset-pin
```

This clears the PIN and signs out every device; the next visit shows the setup screen. Locally: `uv run songvault reset-pin` in `backend/`.
