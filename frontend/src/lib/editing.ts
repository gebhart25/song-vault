// The clip editor's model (spec 5.10): a recording laid out as back-to-back stretches
// covering all of it, each kept (a clip) or dropped (dead space). Pure functions, so
// every edit is a new list and undo is a list of old ones.

export const MIN_PIECE = 0.5; // seconds, as the server checks
const MIN_DROP = 0.1;
const QUIET = 6; // a peak (0-100) at or under this is quiet: about 6% of the loudest

export interface Segment {
	key: string;
	/** The clip this stretch is; null = a new piece, made on save */
	clipId: number | null;
	projectId: number | null;
	start: number;
	end: number;
	keep: boolean;
	labelIds: number[];
	role: string | null;
	title: string;
}

let counter = 0;
export const newKey = () => `s${++counter}`;

const minLength = (s: Segment) => (s.keep ? MIN_PIECE : MIN_DROP);

/** Dropped stretches next to each other become one. */
function tidy(segments: Segment[]): Segment[] {
	const out: Segment[] = [];
	for (const s of segments) {
		const last = out.at(-1);
		if (last && !last.keep && !s.keep) out[out.length - 1] = { ...last, end: s.end };
		else out.push(s);
	}
	return out;
}

/** The index of the stretch playing at `t`. */
export function segmentAt(segments: Segment[], t: number): number {
	const i = segments.findIndex((s) => t >= s.start && t < s.end);
	return i >= 0 ? i : segments.length - 1;
}

/** Cut at `t`. The right-hand part is a new piece like the left (same song, parts, role). */
export function splitAt(segments: Segment[], t: number): Segment[] | string {
	const i = segmentAt(segments, t);
	const s = segments[i];
	if (t - s.start < minLength(s) || s.end - t < minLength(s)) return 'Too close to a cut or an end to split there.';
	const left = { ...s, end: t };
	const right = { ...s, key: newKey(), clipId: null, start: t, title: '' };
	return [...segments.slice(0, i), left, right, ...segments.slice(i + 1)];
}

/** Move the cut between stretch `i` and the next one. */
export function moveCut(segments: Segment[], i: number, t: number): Segment[] {
	const a = segments[i];
	const b = segments[i + 1];
	if (!a || !b) return segments;
	const at = Math.min(Math.max(t, a.start + minLength(a)), b.end - minLength(b));
	const next = [...segments];
	next[i] = { ...a, end: at };
	next[i + 1] = { ...b, start: at };
	return next;
}

/** Take away the cut after stretch `i`: the two become one (kept if either was). */
export function removeCut(segments: Segment[], i: number): Segment[] {
	const a = segments[i];
	const b = segments[i + 1];
	if (!a || !b) return segments;
	const base = a.keep || !b.keep ? a : b;
	const merged = { ...base, start: a.start, end: b.end, keep: a.keep || b.keep, clipId: a.clipId ?? b.clipId };
	return tidy([...segments.slice(0, i), merged, ...segments.slice(i + 2)]);
}

export function setKeep(segments: Segment[], i: number, keep: boolean): Segment[] {
	const s = segments[i];
	if (keep && s.end - s.start < MIN_PIECE) return segments;
	return tidy(segments.map((x, j) => (j === i ? { ...x, keep } : x)));
}

/** Start the kept stretch `i` at `t`: the bit before it is dropped. */
export function startAt(segments: Segment[], i: number, t: number): Segment[] | string {
	const s = segments[i];
	if (t <= s.start || t >= s.end - MIN_PIECE) return 'Pick a time inside this piece, at least half a second before its end.';
	const prev = segments[i - 1];
	if (prev && !prev.keep) return moveCut(segments, i - 1, t);
	const head = { ...s, key: newKey(), clipId: null, end: t, keep: false, title: '' };
	return tidy([...segments.slice(0, i), head, { ...s, start: t }, ...segments.slice(i + 1)]);
}

/** End the kept stretch `i` at `t`: the bit after it is dropped. */
export function endAt(segments: Segment[], i: number, t: number): Segment[] | string {
	const s = segments[i];
	if (t >= s.end || t <= s.start + MIN_PIECE) return 'Pick a time inside this piece, at least half a second after its start.';
	const next = segments[i + 1];
	if (next && !next.keep) return moveCut(segments, i, t);
	const tail = { ...s, key: newKey(), clipId: null, start: t, keep: false, title: '' };
	return tidy([...segments.slice(0, i), { ...s, end: t }, tail, ...segments.slice(i + 1)]);
}

// --- Find gaps: quiet stretches in the waveform ---------------------------------------

/** Quiet runs at least `minSeconds` long, as [start, end] in seconds. */
export function quietRuns(peaks: number[], perSecond: number, minSeconds: number): [number, number][] {
	const runs: [number, number][] = [];
	let from = -1;
	for (let i = 0; i <= peaks.length; i++) {
		const quiet = i < peaks.length && peaks[i] <= QUIET;
		if (quiet && from < 0) from = i;
		if (!quiet && from >= 0) {
			if ((i - from) / perSecond >= minSeconds) runs.push([from / perSecond, i / perSecond]);
			from = -1;
		}
	}
	return runs;
}

const MARGIN = 0.25; // seconds of quiet left around the sound

/** Drop the quiet at both ends of the recording, leaving a short margin. */
export function trimSilence(segments: Segment[], peaks: number[], perSecond: number, duration: number): Segment[] | string {
	const runs = quietRuns(peaks, perSecond, 0.3);
	let next = segments;
	let changed = false;
	const head = runs[0];
	if (head && head[0] <= 0.05 && head[1] < duration - 1) {
		const i = segmentAt(next, head[1]);
		const r = startAt(next, i, head[1] - MARGIN);
		if (typeof r !== 'string') [next, changed] = [r, true];
	}
	const tail = runs.at(-1);
	if (tail && tail[1] >= duration - 0.1 && tail[0] > 1) {
		const i = segmentAt(next, tail[0] - 0.01);
		const r = endAt(next, i, tail[0] + MARGIN);
		if (typeof r !== 'string') [next, changed] = [r, true];
	}
	return changed ? next : 'No quiet stretch to trim at either end.';
}

/** Cut at every gap of 2 s or more inside the recording, dropping the gaps. */
export function splitAtGaps(segments: Segment[], peaks: number[], perSecond: number, duration: number): Segment[] | string {
	let next = segments;
	let made = 0;
	for (const [from, to] of quietRuns(peaks, perSecond, 2)) {
		if (from <= 0.05 || to >= duration - 0.05) continue; // the ends are for Trim silence
		const a = from + MARGIN;
		const b = to - MARGIN;
		const first = splitAt(next, a);
		if (typeof first === 'string') continue;
		const second = splitAt(first, b);
		if (typeof second === 'string') continue;
		next = setKeep(second, segmentAt(second, (a + b) / 2), false);
		made++;
	}
	return made ? next : 'No gaps of 2 seconds or more found.';
}

/** "1:12.4" */
export const clock = (t: number) => `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, '0')}`;
