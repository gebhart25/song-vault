// Chord helpers for the song notes and the lyrics page (spec 5.2, 5.6).
//
// Lyrics keep chords inline in square brackets, in front of the syllable where
// the chord changes (ChordPro style): "[D]Hello [Bm]darkness my [G]old friend".

export const CHORD_MAX = 16;

/** "D - Bm - G - A", "D Bm G A", "D-Bm-G-A" or "D, Bm, G, A" -> ["D", "Bm", "G", "A"] */
export function splitChords(input: string): string[] {
	return input.split(/[\s,|–—-]+/).filter(Boolean);
}

export const joinChords = (chords: string[]) => chords.join(' - ');

export function isChord(value: string): boolean {
	const v = value.trim();
	return v.length > 0 && v.length <= CHORD_MAX && !/[[\]\n]/.test(v);
}

// --- chords inside lyric lines ------------------------------------------------------

export interface ChordAt {
	/** Position in the line's words (chord marks removed) */
	index: number;
	chord: string;
}

export interface ParsedLine {
	/** The words alone */
	text: string;
	/** Sorted by index, at most one per index */
	chords: ChordAt[];
}

const CHORD_MARK = /\[([^[\]\n]{1,16})\]/g;

export function parseLine(line: string): ParsedLine {
	let text = '';
	const chords: ChordAt[] = [];
	let last = 0;
	for (const m of line.matchAll(CHORD_MARK)) {
		text += line.slice(last, m.index);
		last = m.index + m[0].length;
		const chord = m[1].trim();
		if (!chord) continue;
		const prev = chords.at(-1);
		if (prev && prev.index === text.length) prev.chord += ` ${chord}`;
		else chords.push({ index: text.length, chord });
	}
	return { text: text + line.slice(last), chords };
}

export function serializeLine({ text, chords }: ParsedLine): string {
	let out = '';
	let pos = 0;
	for (const { index, chord } of [...chords].sort((a, b) => a.index - b.index)) {
		const i = Math.min(Math.max(index, pos), text.length);
		out += `${text.slice(pos, i)}[${chord}]`;
		pos = i;
	}
	return out + text.slice(pos);
}

/** Put `chord` at `index` in the line (replacing any chord there), or remove it (null). */
export function setChord(line: string, index: number, chord: string | null): string {
	const parsed = parseLine(line);
	const chords = parsed.chords.filter((c) => c.index !== index);
	if (chord) chords.push({ index, chord });
	return serializeLine({ text: parsed.text, chords });
}

/** The words with the chord marks taken out. */
export const stripChords = (text: string) => text.replace(CHORD_MARK, '');

/** Every chord used in some lyrics, in order of first use. */
export function chordsIn(text: string): string[] {
	const seen = new Set<string>();
	for (const m of text.matchAll(CHORD_MARK)) {
		for (const chord of m[1].trim().split(/\s+/)) if (chord) seen.add(chord);
	}
	return [...seen];
}

// --- laying out a line with chords above the words ----------------------------------

/** A run of words with (maybe) a chord over its start. */
export interface Piece {
	chord: string | null;
	text: string;
}

/**
 * Split a line into words (each a list of pieces) so the chord row and the words
 * stay aligned and long lines can still wrap between words.
 */
export function layoutLine({ text, chords }: ParsedLine): Piece[][] {
	const at = new Map(chords.map((c) => [Math.min(c.index, text.length), c.chord]));
	const words: Piece[][] = [];
	let word: Piece[] = [];
	let piece: Piece = { chord: null, text: '' };
	const flushPiece = () => {
		if (piece.text || piece.chord) word.push(piece);
		piece = { chord: null, text: '' };
	};
	const flushWord = () => {
		flushPiece();
		if (word.length) words.push(word);
		word = [];
	};
	for (let i = 0; i <= text.length; i++) {
		const chord = at.get(i);
		if (chord !== undefined) {
			flushPiece();
			piece.chord = chord;
		}
		if (i === text.length) break;
		piece.text += text[i];
		if (text[i] === ' ' && text[i + 1] !== ' ') flushWord();
	}
	flushWord();
	return words;
}

// --- chords before and after a line's words -------------------------------------------
//
// A chord played before the singing starts sits on a leading space, and one played
// after the last word on a trailing space (the usual ChordPro way):
//   "[G] [D]Headlights on the county road [A]"
// reads as G, then D on "Headlights", and A once the line is sung.

/** Where the words start and end. A line without words has only "after" chords. */
function wordBounds(text: string): { start: number; end: number } {
	if (!text.trim()) return { start: 0, end: 0 };
	return { start: text.length - text.trimStart().length, end: text.trimEnd().length };
}

/** One tappable spot per character of the words. */
export interface Spot {
	index: number;
	char: string;
	chord: string | null;
}

export interface LineSpots {
	/** Chords before the words, in order */
	before: ChordAt[];
	/** The words' characters, grouped by word */
	words: Spot[][];
	/** Chords after the words, in order */
	after: ChordAt[];
}

export function lineSpots({ text, chords }: ParsedLine): LineSpots {
	const { start, end } = wordBounds(text);
	const at = new Map(chords.map((c) => [c.index, c.chord]));
	const words: Spot[][] = [];
	let word: Spot[] = [];
	for (let i = start; i < end; i++) {
		word.push({ index: i, char: text[i], chord: at.get(i) ?? null });
		if (text[i] === ' ' && text[i + 1] !== ' ') {
			words.push(word);
			word = [];
		}
	}
	if (word.length) words.push(word);
	return {
		before: chords.filter((c) => c.index < start),
		words,
		after: chords.filter((c) => c.index >= end)
	};
}

const shiftFrom = (chords: ChordAt[], from: number, by: number) =>
	chords.map((c) => (c.index >= from ? { ...c, index: c.index + by } : c));

/** Add `chord` before the line's words, after any chords already there. */
export function addChordBefore(line: string, chord: string): string {
	const { text, chords } = parseLine(line);
	if (!text.trim()) return addChordAfter(line, chord);
	const { start } = wordBounds(text);
	return serializeLine({
		text: `${text.slice(0, start)} ${text.slice(start)}`,
		chords: [...shiftFrom(chords, start, 1), { index: start, chord }]
	});
}

/** Add `chord` after the line's words, after any chords already there. */
export function addChordAfter(line: string, chord: string): string {
	const parsed = parseLine(line);
	let text = parsed.text;
	if (parsed.chords.some((c) => c.index === text.length) || (text.trim() && !/\s$/.test(text))) text += ' ';
	return serializeLine({ text, chords: [...parsed.chords, { index: text.length, chord }] });
}

/** Take away the chord at `index`. One before or after the words takes its space with it. */
export function removeChord(line: string, index: number): string {
	const { text, chords } = parseLine(line);
	const rest = chords.filter((c) => c.index !== index);
	const { start, end } = wordBounds(text);
	let cut = -1;
	if (index < start) cut = index;
	else if (index >= end) {
		const c = index < text.length ? index : index - 1;
		if (c >= end && /\s/.test(text[c] ?? '')) cut = c;
	}
	if (cut < 0) return serializeLine({ text, chords: rest });
	return serializeLine({ text: text.slice(0, cut) + text.slice(cut + 1), chords: shiftFrom(rest, cut + 1, -1) });
}

// --- chords in a key -------------------------------------------------------------

const SHARPS = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
const FLATS = ['C', 'Db', 'D', 'Eb', 'E', 'F', 'Gb', 'G', 'Ab', 'A', 'Bb', 'B'];
const LETTERS: Record<string, number> = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };

export interface Key {
	root: number;
	minor: boolean;
}

/** "D", "Bm", "F# minor", "Eb major", "C#m" -> Key; anything else -> null */
export function parseKey(value: string | null | undefined): Key | null {
	const m = (value ?? '').trim().match(/^([A-Ga-g])([#♯b♭]?)\s*(m|min|minor|maj|major)?$/);
	if (!m) return null;
	let root = LETTERS[m[1].toUpperCase()];
	if (m[2] === '#' || m[2] === '♯') root += 1;
	if (m[2] === 'b' || m[2] === '♭') root -= 1;
	const quality = m[3] ?? '';
	return { root: (root + 12) % 12, minor: quality === 'm' || quality.startsWith('min') };
}

/** How notes are spelled when there's no key to go by: the usual guitar-chart names. */
const MIXED = ['C', 'C#', 'D', 'Eb', 'E', 'F', 'F#', 'G', 'Ab', 'A', 'Bb', 'B'];

/** Note names for a key: flats in flat keys (F, Bb, Eb... and Dm, Gm, Cm...), sharps otherwise. */
export function namesForKey(key: Key | null): string[] {
	if (!key) return MIXED;
	const relativeMajor = key.minor ? (key.root + 3) % 12 : key.root;
	return [5, 10, 3, 8, 1, 6].includes(relativeMajor) ? FLATS : SHARPS;
}

/** The chords that belong to a key: I ii iii IV V vi vii° (major) or i ii° III iv v VI VII + V (minor). */
export function chordsInKey(value: string | null | undefined): string[] {
	const key = parseKey(value);
	if (!key) return [];
	const names = namesForKey(key);
	const note = (semitones: number) => names[(key.root + semitones) % 12];
	if (!key.minor) {
		return [
			note(0),
			`${note(2)}m`,
			`${note(4)}m`,
			note(5),
			note(7),
			`${note(9)}m`,
			`${note(11)}dim`
		];
	}
	return [
		`${note(0)}m`,
		`${note(2)}dim`,
		note(3),
		`${note(5)}m`,
		`${note(7)}m`,
		note(8),
		note(10),
		note(7) // the major V that minor songs usually borrow
	];
}

// --- transposing (spec 5.6) ----------------------------------------------------------

function noteIndex(letter: string, accidental: string): number {
	let n = LETTERS[letter.toUpperCase()];
	if (accidental === '#' || accidental === '♯') n += 1;
	if (accidental === 'b' || accidental === '♭') n -= 1;
	return (n + 12) % 12;
}

const mod12 = (n: number) => ((n % 12) + 12) % 12;

/**
 * "Dm7/F#" up 2 -> "Em7/G#". Anything that isn't a chord ("N.C.", "x2") is left alone.
 * `names` spells the new notes (see namesAfter); without it, a note keeps its sharp or
 * flat style and natural notes get the usual chart names.
 */
export function transposeChord(chord: string, semitones: number, names?: string[]): string {
	const shift = mod12(semitones);
	const m = chord.match(/^([A-G])([#♯b♭]?)(.*)$/);
	if (!m || shift === 0) return chord;
	const move = (letter: string, accidental: string) => {
		const spell = names ?? (/[#♯]/.test(accidental) ? SHARPS : /[b♭]/.test(accidental) ? FLATS : MIXED);
		return spell[mod12(noteIndex(letter, accidental) + shift)];
	};
	let rest = m[3];
	let bass = '';
	const slash = rest.lastIndexOf('/');
	const b = slash >= 0 ? rest.slice(slash + 1).match(/^([A-G])([#♯b♭]?)$/) : null;
	if (b) {
		bass = `/${move(b[1], b[2])}`;
		rest = rest.slice(0, slash);
	}
	return move(m[1], m[2]) + rest + bass;
}

/** Every chord in a run of chords ("D - Bm - G", "D G"), moved by `semitones`. */
export const transposeChords = (chords: string, semitones: number, names?: string[]) =>
	chords.replace(/[^\s,|–—-]+/g, (token) => transposeChord(token, semitones, names));

/** Every [chord] in some lyrics, moved by `semitones`. */
export const transposeLyrics = (text: string, semitones: number, names?: string[]) =>
	text.replace(CHORD_MARK, (_, inner: string) => `[${transposeChords(inner, semitones, names)}]`);

/** The key after moving `semitones`: "D" +2 -> "E", "F# minor" +3 -> "A minor". Null if it isn't a key. */
export function transposeKey(value: string | null | undefined, semitones: number): string | null {
	const key = parseKey(value);
	if (!key || !value) return null;
	const root = mod12(key.root + semitones);
	const name = namesForKey({ root, minor: key.minor })[root];
	return value.trim().replace(/^[A-Ga-g][#♯b♭]?/, name);
}

/** The note names to spell chords with once the song has moved `semitones` from `songKey`
 * (undefined without a key: each chord then keeps its own sharp or flat style). */
export function namesAfter(songKey: string | null | undefined, semitones: number): string[] | undefined {
	const key = parseKey(songKey);
	return key ? namesForKey({ root: mod12(key.root + semitones), minor: key.minor }) : undefined;
}
