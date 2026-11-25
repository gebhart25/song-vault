// The song detail fields shared by the song notes and a clip's details (spec 5.2).

export type FieldKey = 'song_key' | 'bpm' | 'time_signature' | 'capo' | 'tuning' | 'sounds_like';

export interface FieldDef {
	key: FieldKey;
	label: string;
	placeholder: string;
	numeric?: { min: number; max: number; integer: boolean };
	options?: string[];
	maxlength: number;
}

export const SONG_FIELDS: FieldDef[] = [
	{
		key: 'song_key',
		label: 'Key',
		placeholder: 'D or Bm',
		maxlength: 40,
		options: ['C', 'C#', 'Db', 'D', 'Eb', 'E', 'F', 'F#', 'Gb', 'G', 'Ab', 'A', 'Bb', 'B', 'Am', 'Bbm', 'Bm', 'Cm', 'C#m', 'Dm', 'D#m', 'Ebm', 'Em', 'Fm', 'F#m', 'Gm', 'G#m']
	},
	{ key: 'bpm', label: 'BPM', placeholder: '92', maxlength: 6, numeric: { min: 1, max: 400, integer: false } },
	{
		key: 'time_signature',
		label: 'Time signature',
		placeholder: '4/4',
		maxlength: 10,
		options: ['4/4', '3/4', '6/8', '12/8', '2/4', '5/4', '7/8']
	},
	{ key: 'capo', label: 'Capo', placeholder: '2', maxlength: 2, numeric: { min: 0, max: 24, integer: true } },
	{
		key: 'tuning',
		label: 'Tuning',
		placeholder: 'Standard',
		maxlength: 80,
		options: ['Standard', 'Half step down', 'Drop D', 'Drop C', 'DADGAD', 'Open G', 'Open D', 'Open E']
	},
	{ key: 'sounds_like', label: 'Sounds like', placeholder: 'Songs or artists with a similar feel', maxlength: 300 }
];

/** A take can differ from the song in these. */
export const CLIP_FIELDS = SONG_FIELDS.filter((f) => f.key !== 'sounds_like');

/** The value as it's shown and edited ("" when not set). */
export function fieldText(key: FieldKey, value: string | number | null | undefined): string {
	if (value === null || value === undefined || value === '') return '';
	return key === 'bpm' ? String(Math.round(Number(value) * 100) / 100) : String(value);
}

/** What typed text saves as, or an error message. Blank saves as null (not set). */
export function parseField(f: FieldDef, raw: string): { value: string | number | null } | { error: string } {
	const text = raw.trim();
	if (!text) return { value: null };
	if (!f.numeric) return { value: text };
	const n = Number(text);
	const { min, max, integer } = f.numeric;
	if (!Number.isFinite(n) || n < min || n > max || (integer && !Number.isInteger(n))) {
		return { error: `${f.label} should be ${integer ? 'a whole number' : 'a number'} from ${min} to ${max}.` };
	}
	return { value: n };
}
