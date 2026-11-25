export type ProjectStatus = 'idea' | 'writing' | 'arranging' | 'recorded' | 'done' | 'shelved';
export type ClipStatus = 'idea' | 'current' | 'used' | 'discarded';

export const PROJECT_STATUSES: { value: ProjectStatus; label: string }[] = [
	{ value: 'idea', label: 'Idea' },
	{ value: 'writing', label: 'Writing' },
	{ value: 'arranging', label: 'Arranging' },
	{ value: 'recorded', label: 'Recorded' },
	{ value: 'done', label: 'Done' },
	{ value: 'shelved', label: 'Shelved' }
];

export function statusLabel(status: ProjectStatus): string {
	return PROJECT_STATUSES.find((s) => s.value === status)?.label ?? status;
}

export interface AuthStatus {
	setup_required: boolean;
	authenticated: boolean;
	/** False while an install still uses its old password: the app asks for a PIN. */
	pin_set: boolean;
}

export interface ClipSummary {
	id: number;
	uid: string;
	title: string;
	status: ClipStatus;
	media_type: 'video' | 'audio';
	recorded_at: string | null;
	uploaded_at: string;
	duration_seconds: number | null;
	thumbnail_url: string | null;
	/** pending = thumbnail or playable copy still being made */
	processing: 'pending' | 'failed' | null;
	/** Videos: `preview_frames` frames side by side, for scrubbing on the tile (spec 5.4) */
	preview_url?: string | null;
	preview_frames?: number | null;
	/** Audio clips: loudest point in each of 120 slices, 0-100 (spec 5.9) */
	waveform?: number[] | null;
	/** What the take is (Rhythm, Lead, Vocals...): one Current per part and role (spec 5.1) */
	role?: string | null;
	/** A clip is a stretch of a recording (spec 5.10): where it starts and ends in it, in seconds */
	recording_id: number;
	start_seconds: number;
	end_seconds: number | null;
	recording_duration: number | null;
}

export interface ClipDetail extends ClipSummary {
	notes: string;
	original_filename: string;
	recorded_at_source: 'metadata' | 'filename' | 'file_date' | 'manual' | null;
	size_bytes: number;
	sha256: string;
	verified_at: string | null;
	project_id: number | null;
	project_name: string | null;
	label_ids: number[];
	width: number | null;
	height: number | null;
	video_codec: string | null;
	is_hdr: boolean;
	media_url: string | null;
	original_url: string;
	/** Just the sound: a video's audio-only copy, or an audio clip itself (spec 5.4) */
	audio_url: string | null;
	/** This stretch as a file (cut without re-encoding); null when the clip is the whole recording */
	cut_url: string | null;
	trimmed: boolean;
	/** Clips made from the same recording, this one included (not those in Trash) */
	pieces: number;
	/** The chosen poster frame, seconds into the clip; null = automatic */
	thumbnail_at: number | null;
	deleted_at: string | null;
	jobs: { type: string; state: string; error: string | null }[];
	duplicate: boolean;
	// This take's details, when they differ from the song (spec 5.2)
	song_key: string | null;
	bpm: number | null;
	time_signature: string | null;
	capo: number | null;
	tuning: string | null;
	/** Chord progressions without labels: the clip's parts say where they go */
	progressions: string[][];
}

export const CLIP_STATUSES: { value: ClipStatus; label: string }[] = [
	{ value: 'idea', label: 'Idea' },
	{ value: 'current', label: 'Current' },
	{ value: 'used', label: 'Used' },
	{ value: 'discarded', label: 'Discarded' }
];

export interface InboxClip extends ClipSummary {
	project_id: number | null;
	project_name: string | null;
}

export interface HistoryItem {
	id: number;
	original_filename: string;
	recorded_at: string | null;
	uploaded_at: string;
	size_bytes: number;
	sha256: string;
	verified_at: string | null;
	project_name: string | null;
	deleted: boolean;
	/** In the offsite backup, so safe to delete from the phone */
	backed_up: boolean;
}

export interface History {
	last_backup_at: string | null;
	items: HistoryItem[];
}

export interface ClipsSummary {
	/** Clips that aren't in a project yet */
	uncategorized: number;
	uncategorized_cover_url: string | null;
	uncategorized_updated_at: string | null;
	processing: number;
	/** Songs and recordings in Trash */
	trash: number;
}

export interface TrashProject {
	id: number;
	name: string;
	status: ProjectStatus;
	clip_count: number;
	cover_thumbnail_url: string | null;
	deleted_at: string;
	/** When it's deleted for good */
	purge_at: string;
}

export interface TrashClip extends ClipSummary {
	project_id: number | null;
	project_name: string | null;
	/** The clip's song is in Trash too: restore the song first */
	project_in_trash: boolean;
	deleted_at: string;
	purge_at: string;
}

export interface Trash {
	days: number;
	projects: TrashProject[];
	clips: TrashClip[];
}

/** A song's picture on the Projects page (spec 3.1) */
export interface Cover {
	/** auto = the newest clip thumbnail; stock = a picture that ships with the app;
	 * image = the user's own (a copied clip thumbnail or an upload) */
	kind: 'auto' | 'stock' | 'image';
	url: string | null;
	stock: string | null;
	/** For an image copied from a clip's thumbnail: that clip */
	clip_id: number | null;
}

export interface ProjectSummary {
	id: number;
	name: string;
	slug: string;
	status: ProjectStatus;
	archived: boolean;
	created_at: string;
	updated_at: string;
	clip_count: number;
	/** What the card shows: the same as cover.url */
	cover_thumbnail_url: string | null;
	cover: Cover;
}

export interface Row {
	label_id: number;
	name: string;
	is_full_song: boolean;
	is_custom: boolean;
	collapsed: boolean;
	clips: ClipSummary[];
}

export interface ProjectDetail {
	id: number;
	name: string;
	slug: string;
	status: ProjectStatus;
	archived: boolean;
	notes: string;
	song_key: string | null;
	bpm: number | null;
	tuning: string | null;
	capo: number | null;
	time_signature: string | null;
	sounds_like: string | null;
	progressions: Progression[];
	strumming: Strum[];
	/** Number of sections on the lyrics page */
	lyric_sections: number;
	created_at: string;
	updated_at: string;
	clip_count: number;
	cover: Cover;
	unlabeled: ClipSummary[];
	rows: Row[];
}

export interface Label {
	id: number;
	name: string;
	project_id: number | null;
	is_full_song: boolean;
	builtin: boolean;
}

/** A labeled chord progression in the song notes, e.g. Verse: D - Bm - G - A */
export interface Progression {
	label: string;
	chords: string[];
}

/** A strumming pattern in the song notes, e.g. Verse: D - D U - U D U (label optional) */
export interface Strum {
	label: string;
	pattern: string;
}

/** One section of the lyrics page. Chords sit inline in brackets: "[D]Hello [Bm]darkness". */
export interface LyricSection {
	id: string;
	label: string;
	/** chords = an instrumental section whose text is just chord lines ("D - Bm - G - A") */
	kind: 'lyrics' | 'chords';
	text: string;
}

export interface Lyrics {
	sections: LyricSection[];
	revision: number;
	updated_at: string | null;
}

/** One clip of a recording, as the editor sees it (spec 5.10) */
export interface RecordingPiece {
	id: number;
	start_seconds: number;
	end_seconds: number;
	project_id: number | null;
	project_name: string | null;
	label_ids: number[];
	role: string | null;
	title: string;
	status: ClipStatus;
	thumbnail_url: string | null;
}

/** An uploaded file with its clips, for the editor (spec 5.10) */
export interface Recording {
	id: number;
	media_type: 'video' | 'audio';
	original_filename: string;
	duration_seconds: number | null;
	recorded_at: string | null;
	width: number | null;
	height: number | null;
	media_url: string | null;
	audio_url: string | null;
	/** JSON list of `peaks_per_second` points a second, 0-100 */
	peaks_url: string | null;
	peaks_per_second: number;
	/** Frames in a grid, `preview_columns` across */
	preview_url: string | null;
	preview_frames: number | null;
	preview_columns: number;
	clips: RecordingPiece[];
}
