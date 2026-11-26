// Text helpers for the Notebook (spec 5.11): tags, search, pasting a whole notepad in.
// No imports, so this runs anywhere (the page, a quick check in Node).

/** A `#word` tag: letters, digits, `-` and `_`, with at least one letter, not right
 * after a letter or digit, so chords (`C#m`, `[F#]`) and links (`/#top`) aren't tags. */
const TAG = /(^|[^\p{L}\p{N}_#&/])#([\p{L}\p{N}_-]*\p{L}[\p{L}\p{N}_-]*)/gu;

const tagName = (raw: string) => raw.replace(/[-_]+$/, '').toLocaleLowerCase();

/** The note's tags, lowercased, each once, in order. */
export function tagsOf(body: string): string[] {
	const out: string[] = [];
	for (const m of body.matchAll(TAG)) {
		const name = tagName(m[2]);
		if (name && !out.includes(name)) out.push(name);
	}
	return out;
}

export interface Segment {
	text: string;
	/** Set when this piece is a tag (lowercased, without the #) */
	tag: string | null;
}

/** The text in pieces, with the tags picked out for highlighting. */
export function segmentsOf(body: string): Segment[] {
	const out: Segment[] = [];
	let last = 0;
	for (const m of body.matchAll(TAG)) {
		const start = (m.index ?? 0) + m[1].length;
		const raw = '#' + m[2].replace(/[-_]+$/, '');
		if (start > last) out.push({ text: body.slice(last, start), tag: null });
		out.push({ text: raw, tag: tagName(m[2]) });
		last = start + raw.length;
	}
	if (last < body.length) out.push({ text: body.slice(last), tag: null });
	return out;
}

/** A note's words for the lyrics page: tags taken out, and a line of nothing but tags
 * dropped. Chords in [brackets] stay, as that's the lyrics page's own format. */
export function lyricText(body: string): string {
	const lines: string[] = [];
	for (const line of body.replace(/\r\n?/g, '\n').split('\n')) {
		const stripped = line.replace(TAG, '$1');
		if (line.trim() && !stripped.trim()) continue;
		const indent = stripped.length - stripped.trimStart().length;
		lines.push((stripped.slice(0, indent) + stripped.slice(indent).replace(/ {2,}/g, ' ')).trimEnd());
	}
	return lines.join('\n').replace(/^\n+|\n+$/g, '');
}

/** Notes combined into one, in order: each one's text with the blank lines at its start
 * and end trimmed, and one blank line at each join. */
export function combineTexts(bodies: string[]): string {
	return bodies
		.map((b) => b.replace(/\r\n?/g, '\n').replace(/^(?:[ \t]*\n)+/, '').trimEnd())
		.filter((b) => b.trim() !== '')
		.join('\n\n');
}

/** The first line with words, tags out: a name for a song started from a note. */
export function firstLine(body: string, max = 80): string {
	const line = lyricText(body).split('\n').find((l) => l.trim()) ?? '';
	const clean = line.replace(/\[[^\]\n]*\]/g, '').replace(/\s+/g, ' ').trim();
	return clean.length > max ? clean.slice(0, max).trimEnd() : clean;
}

/** Lowercase, without accents: "Café" and "cafe" match. */
export function foldText(text: string): string {
	return text.normalize('NFD').replace(/\p{M}/gu, '').toLocaleLowerCase();
}

/** The words of a search, folded. A note matches when it has all of them. */
export function searchWords(query: string): string[] {
	return foldText(query).split(/\s+/).filter(Boolean);
}

/** Blocks of pasted text become notes, split at blank lines unless `split` is off. */
export function splitPasted(text: string, split = true): string[] {
	const clean = text.replace(/\r\n?/g, '\n');
	const blocks = split ? clean.split(/\n[ \t]*\n/) : [clean];
	return blocks.map((b) => b.replace(/^\s*\n/, '').trimEnd()).filter((b) => b.trim() !== '');
}

/** A random id for a new note, made on the device (works without HTTPS too). */
export function newUid(): string {
	const bytes = crypto.getRandomValues(new Uint8Array(16));
	return btoa(String.fromCharCode(...bytes))
		.replace(/\+/g, '-')
		.replace(/\//g, '_')
		.replace(/=+$/, '');
}

/** "9:41 AM" today, "Yesterday", "Nov 3", or "Nov 3, 2025" in another year. */
export function noteDate(iso: string, now = new Date()): string {
	const d = new Date(iso);
	if (Number.isNaN(d.getTime())) return '';
	const day = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
	const days = Math.round((day(now) - day(d)) / 86_400_000);
	if (days === 0) return d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
	if (days === 1) return 'Yesterday';
	return d.toLocaleDateString(undefined, {
		month: 'short',
		day: 'numeric',
		year: d.getFullYear() === now.getFullYear() ? undefined : 'numeric'
	});
}
