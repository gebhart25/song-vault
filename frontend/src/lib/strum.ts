// Strumming patterns in the song notes (spec 5.2).
//
// Typed with D (down), U (up), X (muted strum / chuck) and - or . (a miss), e.g.
// "D - D U - U D U". Lowercase d / u are lighter strums. Spaces and bar lines group
// the strums for reading; anything else makes it free text, shown as typed.

export type StrumKind = 'down' | 'up' | 'mute' | 'rest';

export interface StrumSlot {
	kind: StrumKind;
	/** A lighter strum (typed in lowercase) */
	soft: boolean;
	/** "1", "&", "e", "a"... when the beats can be counted, else "" */
	count: string;
	/** A space or bar line came before it (used for grouping when there are no counts) */
	gap: boolean;
}

const KINDS: Record<string, StrumKind> = { d: 'down', u: 'up', x: 'mute', '-': 'rest', '.': 'rest' };
/** What's counted between beats, by slots per beat: 1 & 2 &, 1 & a 2 & a, 1 e & a 2 e & a */
const SUBDIVISIONS: Record<number, string[]> = { 1: [], 2: ['&'], 3: ['&', 'a'], 4: ['e', '&', 'a'] };

/** Count names for `slots` spread evenly over one bar, or null if they don't fit evenly. */
function beatCounts(slots: number, timeSignature: string | null | undefined): string[] | null {
	const beats = Number(timeSignature?.match(/^\s*(\d+)\s*\//)?.[1]) || 4; // no time signature: 4/4
	const per = slots / beats;
	if (!Number.isInteger(per) || per < 1 || per > 4) return null;
	const counts: string[] = [];
	for (let beat = 1; beat <= beats; beat++) counts.push(String(beat), ...SUBDIVISIONS[per]);
	return counts;
}

/**
 * The strums of a pattern, with beat counts when they fill the bar evenly
 * ("D - D U - U D U" in 4/4 -> 1 & 2 & 3 & 4 &). Null for free text.
 */
export function strumSlots(
	pattern: string,
	timeSignature?: string | null
): { slots: StrumSlot[]; counted: boolean } | null {
	const slots: StrumSlot[] = [];
	let gap = false;
	for (const c of pattern) {
		if (/[\s|]/.test(c)) {
			gap = slots.length > 0;
			continue;
		}
		const kind = KINDS[c.toLowerCase()];
		if (!kind) return null;
		slots.push({ kind, soft: c === 'd' || c === 'u', count: '', gap });
		gap = false;
	}
	if (slots.length === 0 || slots.length > 32) return null;
	const counts = beatCounts(slots.length, timeSignature);
	if (counts) slots.forEach((s, i) => (s.count = counts[i]));
	return { slots, counted: counts !== null };
}
