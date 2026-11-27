// Writing helpers (spec 5.11): rhymes and syllable counts from the dictionary on the
// server. Syllable counts fall back to a guess when the server can't be reached.
import { api } from '#lib/api.ts';
import { fetchWithTimeout } from '#lib/net.ts';

export interface RhymeGroup {
	syllables: number;
	words: string[];
	/** How many more there are with this many syllables */
	more: number;
}

export interface Rhymes {
	word: string;
	/** In the dictionary; if not, there are no rhymes */
	known: boolean;
	syllables: number | null;
	perfect: RhymeGroup[];
	near: RhymeGroup[];
}

const rhymeCache = new Map<string, Rhymes>();

/** Rhymes for the last word of `text`. Throws ApiError(0) when the server can't be reached. */
export async function findRhymes(text: string): Promise<Rhymes> {
	const key = text.trim().toLowerCase();
	const cached = rhymeCache.get(key);
	if (cached) return cached;
	const r = await api.get<Rhymes>(`/words/rhymes?word=${encodeURIComponent(text.trim())}`);
	rhymeCache.set(key, r);
	return r;
}

// --- syllables ---------------------------------------------------------------------

const CHORD = /\[[^\]\n]*\]/g;
const TAG = /(^|[^\p{L}\p{N}_#&/])#[\p{L}\p{N}_-]*\p{L}[\p{L}\p{N}_-]*/gu;
const WORD = /[\p{L}]+(?:'[\p{L}]+)*/gu;

/** A guess for one word, as the server guesses words its dictionary doesn't have. */
export function guessWord(word: string): number {
	let w = word
		.normalize('NFD')
		.replace(/\p{M}/gu, '')
		.toLowerCase()
		.replace(/'/g, '');
	if (w.length <= 3) return 1;
	w = w.replace(/(?:[^laeiouy]es|ed|[^laeiouy]e)$/, '').replace(/^y/, '');
	return Math.max(1, (w.match(/[aeiouy]+/g) ?? []).length);
}

/** A guess for a line (chords and tags don't count); null for a line with no words. */
export function guessLine(line: string): number | null {
	const words = line.replace(CHORD, '').replace(TAG, '$1 ').match(WORD);
	return words ? words.reduce((n, w) => n + guessWord(w), 0) : null;
}

const known = new Map<string, number | null>();
let waiting = new Set<string>();
let batch: Promise<void> | null = null;

/** Ask the server about the lines nobody has asked about yet, all together. */
function ask(lines: string[]): Promise<void> {
	for (const l of lines) if (!known.has(l)) waiting.add(l);
	if (!waiting.size) return Promise.resolve();
	batch ??= new Promise<void>((resolve) =>
		setTimeout(async () => {
			const sent = [...waiting].slice(0, 1000);
			waiting = new Set([...waiting].slice(1000));
			batch = null;
			try {
				const res = await fetchWithTimeout('/api/words/syllables', {
					method: 'POST',
					headers: { 'Content-Type': 'application/json' },
					body: JSON.stringify({ lines: sent })
				});
				if (res.ok) {
					const { counts } = (await res.json()) as { counts: (number | null)[] };
					sent.forEach((l, i) => known.set(l, counts[i]));
					if (known.size > 20_000) known.clear();
				}
			} catch {
				// Offline: the guesses stand, and these lines are asked again next time.
			}
			resolve();
		}, 60)
	);
	return batch;
}

/** Syllables per line: the dictionary's counts where the server has answered, guesses
 * otherwise. `onupdate` gets the better counts once they arrive. */
export function syllableCounts(lines: string[], onupdate: (counts: (number | null)[]) => void): (number | null)[] {
	const now = lines.map((l) => (known.has(l) ? (known.get(l) ?? null) : guessLine(l)));
	if (lines.some((l) => !known.has(l))) {
		void ask(lines).then(() => onupdate(lines.map((l) => (known.has(l) ? (known.get(l) ?? null) : guessLine(l)))));
	}
	return now;
}
