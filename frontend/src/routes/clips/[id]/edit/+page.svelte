<script lang="ts">
	import { untrack } from 'svelte';
	import { beforeNavigate, goto } from '$app/navigation';
	import { page } from '$app/state';
	import { ApiError, api } from '#lib/api.ts';
	import {
		MIN_PIECE,
		clock,
		endAt,
		moveCut,
		newKey,
		removeCut,
		segmentAt,
		setKeep,
		splitAt,
		splitAtGaps,
		startAt,
		trimSilence,
		type Segment
	} from '#lib/editing.ts';
	import Icon from '#lib/components/Icon.svelte';
	import { roleOptions, sameRole } from '#lib/roles.ts';
	import type { ClipDetail, Label, Recording, RecordingPiece } from '#lib/types.ts';

	// The clip editor (spec 5.10): trim a clip and split its recording into pieces, each
	// in its own part. Nothing is cut: saving sets each piece's start and end.

	const id = $derived(Number(page.params.id));

	let clip = $state<ClipDetail | null>(null);
	let recording = $state<Recording | null>(null);
	let peaks = $state<number[] | null>(null);
	let loadError = $state('');
	let waiting = $state(false);
	let labelsBySong = $state<Record<string, Label[]>>({});
	let usedRoles = $state<string[]>([]);

	let segments = $state<Segment[]>([]);
	let keepWhole = $state(false);
	/** A clip of all of the recording, kept alongside the pieces ("Keep the whole take too") */
	let wholeClip = $state<RecordingPiece | null>(null);
	let selected = $state(0);
	let selectedCut = $state<number | null>(null);
	let undoStack = $state<string[]>([]);
	let redoStack = $state<string[]>([]);
	let dirty = $state(false);
	let message = $state('');
	let saving = $state(false);
	let saveError = $state('');

	const duration = $derived(recording?.duration_seconds ?? 0);
	const kept = $derived(segments.filter((s) => s.keep));

	// --- loading ---------------------------------------------------------------------------

	async function load(clipId: number) {
		loadError = '';
		try {
			const c = await api.get<ClipDetail>(`/clips/${clipId}`);
			const r = await api.get<Recording>(`/recordings/${c.recording_id}`);
			clip = c;
			recording = r;
			waiting = !r.duration_seconds || !r.media_url;
			if (waiting) return;
			if (r.peaks_url && !peaks) peaks = await api.get<number[]>(r.peaks_url.replace(/^\/api/, ''));
			if (!segments.length) {
				setUp(r, c);
				for (const pid of new Set([c.project_id, ...r.clips.map((p) => p.project_id)])) loadLabels(pid);
			}
		} catch (e) {
			loadError = e instanceof ApiError ? e.message : 'Could not load this recording.';
		}
	}

	function loadLabels(projectId: number | null) {
		const key = String(projectId);
		if (labelsBySong[key]) return;
		api.get<Label[]>(projectId === null ? '/labels' : `/labels?project_id=${projectId}`)
			.then((l) => (labelsBySong[key] = l))
			.catch(() => {});
	}

	/** Lay the recording out as stretches: its clips, and dropped dead space between them. */
	function setUp(r: Recording, opened: ClipDetail) {
		const total = r.duration_seconds ?? 0;
		const covers = (p: RecordingPiece) => p.start_seconds <= 0.05 && p.end_seconds >= total - 0.05;
		const whole = r.clips.length > 1 ? r.clips.find(covers) : undefined;
		wholeClip = whole ?? null;
		keepWhole = !!whole;
		const pieces = r.clips.filter((p) => p !== whole).sort((a, b) => a.start_seconds - b.start_seconds);
		const out: Segment[] = [];
		let at = 0;
		const gap = (from: number, to: number) =>
			out.push({ key: newKey(), clipId: null, projectId: opened.project_id, start: from, end: to, keep: false, labelIds: [], role: null, title: '' });
		for (const p of pieces) {
			const start = Math.max(p.start_seconds, at);
			if (start > at + 0.05) gap(at, start);
			out.push({
				key: newKey(),
				clipId: p.id,
				projectId: p.project_id,
				start,
				end: Math.max(p.end_seconds, start + MIN_PIECE),
				keep: true,
				labelIds: p.label_ids,
				role: p.role,
				title: p.title
			});
			at = Math.max(at, p.end_seconds);
		}
		if (total - at > 0.05) gap(at, total);
		segments = out;
		selected = Math.max(0, segments.findIndex((s) => s.clipId === opened.id));
	}

	$effect(() => {
		const clipId = id;
		segments = [];
		peaks = null;
		untrack(() => load(clipId));
		api.get<string[]>('/clips/roles').then((r) => (usedRoles = r)).catch(() => {});
	});

	// Still being processed (just recorded or uploaded): check again shortly. The waveform
	// may also come a little after the playable copy.
	const polling = $derived(!loadError && (waiting || (!!recording && !peaks && !recording.peaks_url)));
	$effect(() => {
		if (!polling) return;
		const timer = setInterval(() => load(id), 3000);
		return () => clearInterval(timer);
	});

	// --- editing, with undo ------------------------------------------------------------------

	function snapshot() {
		return JSON.stringify({ segments, keepWhole });
	}

	/** New pieces go in the song of the clip the editor was opened from (the server puts
	 * them there), so one split from a piece in another song takes that song's parts. */
	function inSong(list: Segment[]): Segment[] {
		const home = clip?.project_id ?? null;
		return list.map((s) => (s.clipId === null && s.projectId !== home ? { ...s, projectId: home, labelIds: [] } : s));
	}

	/** Swap in an edited list, keeping the same stretch selected. */
	function replace(list: Segment[], select?: number) {
		const key = segments[selected]?.key;
		segments = inSong(list);
		const same = segments.findIndex((s) => s.key === key);
		selected = Math.max(0, Math.min(select ?? (same >= 0 ? same : selected), segments.length - 1));
	}

	function apply(result: Segment[] | string, select?: number) {
		message = '';
		if (typeof result === 'string') {
			message = result;
			return;
		}
		undoStack = [...undoStack.slice(-99), snapshot()];
		redoStack = [];
		replace(result, select);
		selectedCut = null;
		dirty = true;
	}

	function restore(from: string[], to: string[]): [string[], string[]] {
		const last = from.at(-1);
		if (!last) return [from, to];
		const state = JSON.parse(last);
		const nextTo = [...to, snapshot()];
		segments = state.segments;
		keepWhole = state.keepWhole;
		selected = Math.min(selected, segments.length - 1);
		selectedCut = null;
		dirty = true;
		return [from.slice(0, -1), nextTo];
	}

	const undo = () => ([undoStack, redoStack] = restore(undoStack, redoStack));
	const redo = () => ([redoStack, undoStack] = restore(redoStack, undoStack));

	function edit(fn: (s: Segment) => Partial<Segment>, i = selected) {
		undoStack = [...undoStack.slice(-99), snapshot()];
		redoStack = [];
		segments = segments.map((s, j) => (j === i ? { ...s, ...fn(s) } : s));
		dirty = true;
	}

	const splitHere = () => apply(splitAt(segments, now), segmentAt(segments, now) + 1);
	const startHere = () => apply(startAt(segments, selected, now));
	const endHere = () => apply(endAt(segments, selected, now));
	function toggleKeep(i: number) {
		const s = segments[i];
		const next = setKeep(segments, i, !s.keep);
		apply(next, segmentAt(next, (s.start + s.end) / 2)); // dropping may merge it with its neighbours
	}
	const findSilence = () => peaks && recording && apply(trimSilence(segments, peaks, recording.peaks_per_second, duration));
	const findGaps = () => peaks && recording && apply(splitAtGaps(segments, peaks, recording.peaks_per_second, duration));

	function nudgeCut(delta: number) {
		if (selectedCut === null) return;
		const i = selectedCut;
		apply(moveCut(segments, i, segments[i].end + delta));
		selectedCut = i;
	}

	function deleteCut() {
		if (selectedCut === null) return;
		apply(removeCut(segments, selectedCut));
	}

	function toggleWhole() {
		undoStack = [...undoStack.slice(-99), snapshot()];
		redoStack = [];
		keepWhole = !keepWhole;
		dirty = true;
	}

	// --- the player: the whole recording ------------------------------------------------------

	const SPEEDS = [0.5, 0.75, 1];
	let player: HTMLMediaElement | undefined = $state();
	let now = $state(0);
	let playing = $state(false);
	let loopPiece = $state(false);
	let speed = $state(1);

	$effect(() => {
		if (player) player.playbackRate = speed;
	});

	function togglePlay() {
		if (!player) return;
		if (player.paused) {
			const s = segments[selected];
			if (loopPiece && s && (now < s.start || now >= s.end)) player.currentTime = s.start;
			player.play().catch(() => {});
		} else player.pause();
	}

	function seek(t: number) {
		if (player) player.currentTime = Math.min(Math.max(t, 0), duration);
		now = Math.min(Math.max(t, 0), duration);
	}

	/** The selected stretch's ends: the red marker is dragged, rewound and skipped within them. */
	const bounds = $derived.by(() => {
		const s = segments[selected];
		return s ? { start: s.start, end: s.end } : { start: 0, end: duration };
	});
	const within = (t: number) => Math.min(Math.max(t, bounds.start), bounds.end);

	/** Back to the start of the selected piece. */
	const rewind = () => seek(bounds.start);
	const SKIP = 15;
	/** 15 s on, but not past the end of the selected piece. */
	const skipAhead = () => seek(within(now + SKIP));

	// Follow the playhead every frame (smooth line, and the piece loop), scrolling the
	// timeline along when zoomed in.
	$effect(() => {
		if (!player || !playing) return;
		const el = player;
		let frame = requestAnimationFrame(function tick() {
			now = el.currentTime;
			const s = segments[selected];
			if (loopPiece && s && el.currentTime >= s.end) el.currentTime = s.start;
			if (viewport && !dragging && !scrubbing) {
				const px = x(now);
				if (px < viewport.scrollLeft || px > viewport.scrollLeft + viewWidth - 24) viewport.scrollLeft = px - 48;
			}
			frame = requestAnimationFrame(tick);
		});
		return () => cancelAnimationFrame(frame);
	});

	// --- the timeline -------------------------------------------------------------------------

	let viewport: HTMLDivElement | undefined = $state();
	let viewWidth = $state(800);
	let zoom = $state(1);
	const maxZoom = $derived(Math.max(1, Math.min(40, 30000 / Math.max(viewWidth, 1))));
	const innerWidth = $derived(viewWidth * zoom);
	const x = (t: number) => (duration ? (t / duration) * innerWidth : 0);
	let canvas: HTMLCanvasElement | undefined = $state();

	function setZoom(z: number, anchor = now) {
		const next = Math.min(Math.max(z, 1), maxZoom);
		const before = viewport ? x(anchor) - viewport.scrollLeft : 0;
		zoom = next;
		requestAnimationFrame(() => {
			if (viewport) viewport.scrollLeft = x(anchor) - before;
		});
	}

	// The waveform: kept stretches in the accent colour, dropped ones grey.
	$effect(() => {
		if (!canvas || !peaks || !duration) return;
		const width = Math.round(innerWidth);
		const height = 72;
		const scale = width * devicePixelRatio <= 32000 ? devicePixelRatio : 1;
		canvas.width = width * scale;
		canvas.height = height * scale;
		canvas.style.width = `${width}px`;
		const g = canvas.getContext('2d');
		if (!g) return;
		g.scale(scale, scale);
		const styles = getComputedStyle(canvas);
		const keptColour = styles.getPropertyValue('--accent').trim() || '#f0a646';
		const selectedColour = styles.getPropertyValue('--accent-strong').trim() || '#ffb85c';
		const pps = recording?.peaks_per_second ?? 20;
		const segs = segments;
		const sel = segs[selected];
		for (let px = 0; px < width; px += 2) {
			const t0 = (px / width) * duration;
			const t1 = ((px + 2) / width) * duration;
			let peak = 0;
			for (let i = Math.floor(t0 * pps); i <= Math.min(peaks.length - 1, Math.floor(t1 * pps)); i++) peak = Math.max(peak, peaks[i]);
			const h = Math.max(2, (peak / 100) * (height - 6));
			const seg = segs[segmentAt(segs, t0)];
			g.fillStyle = !seg?.keep ? '#4a505c' : seg === sel ? selectedColour : keptColour;
			g.fillRect(px, (height - h) / 2, 1.5, h);
		}
	});

	// Frames above the waveform (videos): each tile shows the frame nearest its time.
	let grid = $state<{ w: number; h: number } | null>(null);
	$effect(() => {
		const url = recording?.preview_url;
		grid = null;
		if (!url) return;
		const img = new Image();
		img.onload = () => (grid = { w: img.naturalWidth, h: img.naturalHeight });
		img.src = url;
	});
	const FRAME_H = 44;
	const filmstrip = $derived.by(() => {
		const frames = recording?.preview_frames ?? 0;
		if (!grid || !frames || !duration || !recording) return [];
		const columns = Math.min(frames, recording.preview_columns);
		const rows = Math.ceil(frames / columns);
		const frameW = (grid.w / columns / (grid.h / rows)) * FRAME_H;
		const tiles = [];
		for (let left = 0; left < innerWidth; left += frameW) {
			const i = Math.min(frames - 1, Math.floor(((left + frameW / 2) / innerWidth) * frames));
			tiles.push({
				left,
				width: frameW,
				bgSize: `${frameW * columns}px ${FRAME_H * rows}px`,
				bgPos: `${-(i % columns) * frameW}px ${-Math.floor(i / columns) * FRAME_H}px`
			});
		}
		return tiles;
	});

	function timeFromClient(clientX: number): number {
		if (!viewport) return 0;
		const r = viewport.getBoundingClientRect();
		return Math.min(Math.max(((clientX - r.left + viewport.scrollLeft) / innerWidth) * duration, 0), duration);
	}

	function onTimelineClick(e: MouseEvent) {
		if ((e.target as Element).closest('.cut, .ruler')) return;
		const t = timeFromClient(e.clientX);
		seek(t);
		selected = segmentAt(segments, t);
		selectedCut = null;
	}

	// Dragging the red marker along the ruler (or tapping the ruler): kept inside the
	// selected piece, so it's easy to play from the start of an edit. The rest of the
	// timeline still selects the stretch under a tap.
	let scrubbing = false;

	function onScrubDown(e: PointerEvent) {
		e.stopPropagation(); // not a pinch
		(e.currentTarget as Element).setPointerCapture(e.pointerId);
		scrubbing = true;
		seek(within(timeFromClient(e.clientX)));
	}

	function onScrubMove(e: PointerEvent) {
		if (scrubbing) seek(within(timeFromClient(e.clientX)));
	}

	function onScrubUp() {
		scrubbing = false;
	}

	function onScrubKey(e: KeyboardEvent) {
		const step = e.shiftKey ? 0.1 : 1;
		const to: Record<string, number> = {
			ArrowLeft: now - step,
			ArrowRight: now + step,
			Home: bounds.start,
			End: bounds.end
		};
		if (!(e.key in to)) return;
		e.preventDefault();
		seek(within(to[e.key]));
	}

	/** Time marks along the ruler, about every 80 px. */
	const ticks = $derived.by(() => {
		if (!duration || !innerWidth) return [];
		const perSecond = innerWidth / duration;
		const step = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600].find((s) => s * perSecond >= 80) ?? 1200;
		const label = (t: number) =>
			step < 1 ? clock(t) : `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, '0')}`;
		const out = [];
		for (let t = 0; t <= duration + 1e-6; t += step) out.push({ t, left: t * perSecond, label: label(t) });
		return out;
	});

	// Dragging a cut, or one of the handles at the very start and end of the recording
	// (which, once moved in, drop the stretch outside and become a cut).
	type Handle = number | 'head' | 'tail';
	let dragging: { at: Handle; before: string } | null = null;
	/** An end handle has to move this far in (seconds) before it trims */
	const EDGE = 0.15;

	function onCutDown(e: PointerEvent, at: Handle) {
		e.stopPropagation();
		(e.currentTarget as Element).setPointerCapture(e.pointerId);
		dragging = { at, before: snapshot() };
		selectedCut = typeof at === 'number' ? at : null;
	}

	function onCutMove(e: PointerEvent) {
		if (!dragging) return;
		const t = timeFromClient(e.clientX);
		const at = dragging.at;
		if (typeof at === 'number') {
			segments = moveCut(segments, at, t);
		} else if (at === 'head') {
			const r = t >= EDGE ? startAt(segments, 0, t) : '';
			if (typeof r === 'string') return;
			replace(r);
			dragging.at = selectedCut = 0;
		} else {
			const last = segments.length - 1;
			const r = t <= duration - EDGE ? endAt(segments, last, t) : '';
			if (typeof r === 'string') return;
			replace(r);
			dragging.at = selectedCut = last;
		}
	}

	function onCutUp() {
		if (!dragging) return;
		if (dragging.before !== snapshot()) {
			undoStack = [...undoStack.slice(-99), dragging.before];
			redoStack = [];
			dirty = true;
		}
		dragging = null;
	}

	// Pinch to zoom (two fingers on the timeline), or Ctrl + wheel.
	const touches = new Map<number, number>();
	let pinch: { distance: number; zoom: number } | null = null;

	function onTouchDown(e: PointerEvent) {
		if (e.pointerType !== 'touch') return;
		touches.set(e.pointerId, e.clientX);
		if (touches.size === 2) {
			const [a, b] = [...touches.values()];
			pinch = { distance: Math.abs(a - b) || 1, zoom };
		}
	}

	function onTouchMove(e: PointerEvent) {
		if (!touches.has(e.pointerId)) return;
		touches.set(e.pointerId, e.clientX);
		if (pinch && touches.size === 2) {
			const [a, b] = [...touches.values()];
			setZoom((pinch.zoom * Math.abs(a - b)) / pinch.distance);
		}
	}

	function onTouchUp(e: PointerEvent) {
		touches.delete(e.pointerId);
		if (touches.size < 2) pinch = null;
	}

	function onWheel(e: WheelEvent) {
		if (!e.ctrlKey) return;
		e.preventDefault();
		setZoom(zoom * (e.deltaY < 0 ? 1.25 : 0.8), timeFromClient(e.clientX));
	}

	// --- pieces list ----------------------------------------------------------------------------

	const roles = $derived(roleOptions([...usedRoles, ...segments.map((s) => s.role)]));
	const labelsFor = (s: Segment) => labelsBySong[String(s.projectId)] ?? [];

	function toggleLabel(i: number, labelId: number) {
		edit((s) => ({ labelIds: s.labelIds.includes(labelId) ? s.labelIds.filter((x) => x !== labelId) : [...s.labelIds, labelId] }), i);
	}

	// --- saving ---------------------------------------------------------------------------------

	/** The clip kept whole alongside the pieces: the whole take if there is one, or else the
	 * clip the editor was opened from (its pieces are then new clips). */
	const wholeId = $derived(keepWhole ? (wholeClip?.id ?? clip?.id ?? null) : null);

	/** The clip each kept piece saves as (null = a new clip). The clip that was opened stays
	 * the first kept piece (spec 5.10) unless it is the whole take or is still a piece. */
	const saveAs = $derived.by(() => {
		const ids = new Map<string, number | null>();
		const used = new Set<number>(wholeId ? [wholeId] : []);
		for (const s of kept) {
			const take = s.clipId !== null && !used.has(s.clipId) ? s.clipId : null;
			if (take !== null) used.add(take);
			ids.set(s.key, take);
		}
		if (clip && !used.has(clip.id)) {
			const first = kept.find((s) => ids.get(s.key) === null);
			if (first) ids.set(first.key, clip.id);
		}
		return ids;
	});

	const summary = $derived.by(() => {
		const ids = [...saveAs.values()];
		const fresh = ids.filter((c) => c === null).length;
		const keptIds = new Set([...ids, wholeId]);
		const trashed = (recording?.clips ?? []).filter((c) => !keptIds.has(c.id)).length;
		return { pieces: kept.length + (wholeId ? 1 : 0), fresh, trashed };
	});

	async function save() {
		if (!clip || !recording) return;
		saving = true;
		saveError = '';
		const pieces = kept.map((s) => ({
			id: saveAs.get(s.key) ?? null,
			start_seconds: Math.round(s.start * 1000) / 1000,
			end_seconds: Math.round(s.end * 1000) / 1000,
			label_ids: s.labelIds,
			role: s.role,
			title: s.title
		}));
		if (wholeId) {
			const w = wholeClip ?? recording.clips.find((c) => c.id === wholeId);
			pieces.unshift({ id: wholeId, start_seconds: 0, end_seconds: duration, label_ids: w?.label_ids ?? clip.label_ids, role: w?.role ?? clip.role ?? null, title: w?.title ?? clip.title });
		}
		const listed = new Set(pieces.map((p) => p.id));
		const removed = recording.clips.map((c) => c.id).filter((c) => !listed.has(c));
		try {
			await api.put<Recording>(`/recordings/${recording.id}/clips`, { from_clip: clip.id, pieces, removed });
			dirty = false;
			await goto(clip.project_id === null ? '/uncategorized' : `/projects/${clip.project_id}`);
		} catch (e) {
			saveError = e instanceof ApiError ? e.message : 'Could not save.';
		} finally {
			saving = false;
		}
	}

	beforeNavigate((nav) => {
		if (!dirty || saving) return;
		// Closing or reloading the tab: cancelling asks with the browser's own dialog.
		if (nav.type === 'leave') nav.cancel();
		else if (!confirm('Leave without saving these edits?')) nav.cancel();
	});

	const back = $derived(clip ? (clip.project_id === null ? '/uncategorized' : `/projects/${clip.project_id}`) : '/');
</script>

<div class="page">
	<a href={back} class="back"><Icon name="arrow-left" size={18} /> {clip?.project_name ?? 'Back'}</a>
	<h1>Trim &amp; split</h1>

	{#if loadError}
		<p class="error" role="alert">{loadError}</p>
	{:else if !recording || !clip}
		<div class="loading" aria-busy="true"></div>
	{:else if waiting}
		<div class="note"><span class="spinner" aria-hidden="true"></span> Getting the recording ready… This page carries on by itself.</div>
	{:else}
		<p class="intro muted">
			{recording.original_filename} · {clock(duration)}. Cut it into pieces and put each in its part; drop the dead space.
			Nothing is cut from the file, so you can change this again later.
		</p>

		<div class="stage" class:audio={recording.media_type === 'audio'}>
			{#if recording.media_type === 'video'}
				<!-- svelte-ignore a11y_media_has_caption, a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions -->
				<video
					bind:this={player}
					src={recording.media_url}
					playsinline
					preload="auto"
					onclick={togglePlay}
					onplay={() => (playing = true)}
					onpause={() => (playing = false)}
					ontimeupdate={(e) => (now = e.currentTarget.currentTime)}
				></video>
			{:else}
				<audio
					bind:this={player}
					src={recording.media_url}
					preload="auto"
					onplay={() => (playing = true)}
					onpause={() => (playing = false)}
					ontimeupdate={(e) => (now = e.currentTarget.currentTime)}
				></audio>
			{/if}
		</div>

		<div class="transport">
			<button class="btn btn-icon" aria-label="Back to the start of the piece" title="Back to the start of the piece" onclick={rewind}>
				<Icon name="skip-back" size={18} />
			</button>
			<button class="btn btn-primary btn-icon" aria-label={playing ? 'Pause' : 'Play'} onclick={togglePlay}>
				<Icon name={playing ? 'pause' : 'play'} />
			</button>
			<button class="btn skip" aria-label="Forward 15 seconds" title="Forward 15 seconds (not past the end of the piece)" onclick={skipAhead}>
				+15 s
			</button>
			<span class="time">{clock(now)} / {clock(duration)}</span>
			<label class="toggle"><input type="checkbox" bind:checked={loopPiece} /> Loop the piece</label>
			<div class="speeds" role="group" aria-label="Speed">
				{#each SPEEDS as s (s)}
					<button class="btn btn-sm" aria-pressed={speed === s} onclick={() => (speed = s)}>{s}×</button>
				{/each}
			</div>
			<span class="spacer"></span>
			<div class="zoom" role="group" aria-label="Zoom">
				<button class="btn btn-sm btn-icon" aria-label="Zoom out" disabled={zoom <= 1} onclick={() => setZoom(zoom / 1.6)}><Icon name="minus" size={16} /></button>
				<button class="btn btn-sm btn-icon" aria-label="Zoom in" disabled={zoom >= maxZoom} onclick={() => setZoom(zoom * 1.6)}><Icon name="plus" size={16} /></button>
			</div>
		</div>

		<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
		<div
			class="timeline"
			bind:this={viewport}
			bind:clientWidth={viewWidth}
			onclick={onTimelineClick}
			onpointerdown={onTouchDown}
			onpointermove={onTouchMove}
			onpointerup={onTouchUp}
			onpointercancel={onTouchUp}
			onwheel={onWheel}
		>
			<div class="track" style:width="{innerWidth}px">
				<div
					class="ruler"
					role="slider"
					tabindex="0"
					aria-label="Position in the piece"
					aria-valuemin={bounds.start}
					aria-valuemax={bounds.end}
					aria-valuenow={now}
					aria-valuetext={clock(now)}
					onpointerdown={onScrubDown}
					onpointermove={onScrubMove}
					onpointerup={onScrubUp}
					onpointercancel={onScrubUp}
					onkeydown={onScrubKey}
				>
					<span class="ruler-range" style:left="{x(bounds.start)}px" style:width="{x(bounds.end) - x(bounds.start)}px"></span>
					{#each ticks as tick (tick.t)}
						<span class="tick" style:left="{tick.left}px">{tick.label}</span>
					{/each}
					<span class="knob" style:left="{x(now)}px"></span>
				</div>
				{#if filmstrip.length}
					<div class="frames" aria-hidden="true">
						{#each filmstrip as f, k (k)}
							<span
								class="frame"
								style:left="{f.left}px"
								style:width="{f.width}px"
								style:background-image="url({recording.preview_url})"
								style:background-size={f.bgSize}
								style:background-position={f.bgPos}
							></span>
						{/each}
					</div>
				{/if}
				<div class="wave">
					{#if peaks}
						<canvas bind:this={canvas} aria-hidden="true"></canvas>
					{:else}
						<p class="muted wave-note">The waveform is on its way…</p>
					{/if}
				</div>
				{#each segments as s, i (s.key)}
					<span class="seg" class:dropped={!s.keep} class:selected={i === selected} style:left="{x(s.start)}px" style:width="{x(s.end) - x(s.start)}px"></span>
				{/each}
				{#each segments.slice(0, -1) as s, i (s.key)}
					<button
						class="cut"
						class:selected={selectedCut === i}
						style:left="{x(s.end)}px"
						aria-label="Cut at {clock(s.end)}"
						onpointerdown={(e) => onCutDown(e, i)}
						onpointermove={onCutMove}
						onpointerup={onCutUp}
						onpointercancel={onCutUp}
						onclick={(e) => (e.stopPropagation(), (selectedCut = i))}
					><span></span></button>
				{/each}
				{#if segments[0]?.keep}
					<button
						class="cut edge head"
						style:left="0px"
						aria-label="Drag to trim the start"
						onpointerdown={(e) => onCutDown(e, 'head')}
						onpointermove={onCutMove}
						onpointerup={onCutUp}
						onpointercancel={onCutUp}
						onclick={(e) => e.stopPropagation()}
					><span></span></button>
				{/if}
				{#if segments.at(-1)?.keep}
					<button
						class="cut edge tail"
						style:left="{innerWidth}px"
						aria-label="Drag to trim the end"
						onpointerdown={(e) => onCutDown(e, 'tail')}
						onpointermove={onCutMove}
						onpointerup={onCutUp}
						onpointercancel={onCutUp}
						onclick={(e) => e.stopPropagation()}
					><span></span></button>
				{/if}
				<span class="playhead" style:left="{x(now)}px" aria-hidden="true"></span>
			</div>
		</div>

		<div class="tools">
			<button class="btn btn-sm" onclick={splitHere}><Icon name="scissors" size={16} /> Split here</button>
			<button class="btn btn-sm" disabled={!segments[selected]?.keep} onclick={startHere}>Start here</button>
			<button class="btn btn-sm" disabled={!segments[selected]?.keep} onclick={endHere}>End here</button>
			{#if selectedCut !== null}
				<span class="cut-tools">
					<button class="btn btn-sm btn-icon" aria-label="Move the cut back 0.1 s" onclick={() => nudgeCut(-0.1)}><Icon name="chevron-left" size={16} /></button>
					<span class="time">{clock(segments[selectedCut]?.end ?? 0)}</span>
					<button class="btn btn-sm btn-icon" aria-label="Move the cut forward 0.1 s" onclick={() => nudgeCut(0.1)}><Icon name="chevron-right" size={16} /></button>
					<button class="btn btn-sm" onclick={deleteCut}>Remove cut</button>
				</span>
			{/if}
			<span class="spacer"></span>
			<button class="btn btn-sm btn-icon" aria-label="Undo" disabled={!undoStack.length} onclick={undo}><Icon name="undo" size={16} /></button>
			<button class="btn btn-sm btn-icon redo" aria-label="Redo" disabled={!redoStack.length} onclick={redo}><Icon name="undo" size={16} /></button>
		</div>
		<div class="tools">
			<span class="tools-label">Find gaps</span>
			<button class="btn btn-sm" disabled={!peaks} onclick={findSilence}>Trim silence</button>
			<button class="btn btn-sm" disabled={!peaks} onclick={findGaps}>Split at gaps</button>
		</div>
		{#if message}<p class="message" role="status">{message}</p>{/if}

		<label class="whole toggle">
			<input type="checkbox" checked={keepWhole} onchange={toggleWhole} />
			Keep the whole take too <span class="muted">(it stays a clip in its parts, alongside the pieces)</span>
		</label>

		<ol class="pieces">
			{#each segments as s, i (s.key)}
				<li class:dropped={!s.keep} class:selected={i === selected}>
					<div class="piece-head">
						<button class="piece-time" onclick={() => ((selected = i), seek(s.start))}>
							{clock(s.start)}–{clock(s.end)} <span class="muted">({(s.end - s.start).toFixed(1)} s)</span>
						</button>
						{#if s.keep}
							<span class="badge">{saveAs.get(s.key) == null ? 'New clip' : saveAs.get(s.key) === clip.id ? 'This clip' : 'Clip'}</span>
						{:else}
							<span class="muted">Dropped</span>
						{/if}
						<span class="spacer"></span>
						<button class="btn btn-sm btn-ghost" disabled={!s.keep && s.end - s.start < MIN_PIECE} onclick={() => toggleKeep(i)}>
							{s.keep ? 'Drop' : 'Keep'}
						</button>
					</div>
					{#if s.keep}
						<div class="chips" role="group" aria-label="Parts for this piece">
							{#each labelsFor(s) as l (l.id)}
								<button class="chip" aria-pressed={s.labelIds.includes(l.id)} onclick={() => toggleLabel(i, l.id)}>{l.name}</button>
							{/each}
						</div>
						<div class="piece-fields">
							<label>
								<span class="visually-hidden">Role</span>
								<select class="field" value={s.role ?? ''} onchange={(e) => edit(() => ({ role: e.currentTarget.value || null }), i)}>
									<option value="">No role</option>
									{#each roles as r (r)}<option value={r} selected={sameRole(s.role, r)}>{r}</option>{/each}
								</select>
							</label>
							<label class="title-field">
								<span class="visually-hidden">Title</span>
								<input class="field" placeholder="Title (optional)" maxlength="200" value={s.title} onchange={(e) => edit(() => ({ title: e.currentTarget.value }), i)} />
							</label>
						</div>
					{/if}
				</li>
			{/each}
		</ol>

		{#if saveError}<p class="error" role="alert">{saveError}</p>{/if}
		<div class="save-bar">
			<span class="muted">
				{summary.pieces}
				{summary.pieces === 1 ? 'clip' : 'clips'}{#if summary.fresh}&nbsp;({summary.fresh} new){/if}{#if summary.trashed}&nbsp;· {summary.trashed} to Trash{/if}
			</span>
			<span class="spacer"></span>
			<a class="btn btn-sm btn-ghost" href={back}>Cancel</a>
			<button class="btn btn-primary" disabled={saving || summary.pieces === 0} onclick={save}>{saving ? 'Saving…' : 'Save'}</button>
		</div>
	{/if}
</div>

<style>
	.page {
		max-width: 1100px;
		margin: 0 auto;
		padding: 0.9rem var(--gutter) 6rem;
	}
	.back {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
		margin-bottom: 0.6rem;
		color: var(--text-muted);
		font-size: 0.9rem;
		font-weight: 550;
	}
	h1 {
		font-size: 1.55rem;
		margin-bottom: 0.4rem;
	}
	.intro {
		margin: 0 0 0.8rem;
		font-size: 0.9rem;
	}
	.loading {
		height: 40vh;
	}
	.note {
		display: flex;
		align-items: center;
		gap: 0.6rem;
		padding: 1.5rem 0;
		color: var(--text-muted);
	}
	.spinner {
		width: 22px;
		height: 22px;
		border-radius: 50%;
		border: 3px solid var(--surface-3);
		border-top-color: var(--accent);
		animation: spin 0.8s linear infinite;
	}
	@keyframes spin {
		to {
			rotate: 360deg;
		}
	}
	.stage {
		display: grid;
		place-items: center;
		border-radius: var(--radius);
		overflow: hidden;
		background: #000;
	}
	.stage video {
		display: block;
		width: 100%;
		max-height: 40vh;
		cursor: pointer;
	}
	.stage.audio {
		min-height: 0;
		background: none;
	}
	.transport,
	.tools {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.45rem;
		margin-top: 0.6rem;
	}
	.time {
		font-size: 0.88rem;
		font-weight: 600;
		font-variant-numeric: tabular-nums;
	}
	.spacer {
		flex: 1;
	}
	.zoom,
	.cut-tools {
		display: inline-flex;
		align-items: center;
		gap: 0.3rem;
	}
	.redo :global(svg) {
		transform: scaleX(-1);
	}
	.tools-label {
		color: var(--text-faint);
		font-size: 0.78rem;
		font-weight: 650;
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}
	.message {
		margin: 0.5rem 0 0;
		color: var(--accent-strong);
		font-size: 0.9rem;
	}

	/* --- timeline --- */
	.timeline {
		position: relative;
		margin-top: 0.7rem;
		overflow-x: auto;
		overflow-y: hidden;
		border: 1px solid var(--border);
		border-radius: var(--radius-sm);
		background: var(--surface);
		touch-action: pan-x;
		cursor: pointer;
	}
	.track {
		position: relative;
		min-width: 100%;
	}
	/* The ruler: drag or tap it to move the red marker within the selected piece */
	.ruler {
		position: relative;
		height: 26px;
		border-bottom: 1px solid var(--border);
		background: var(--surface-2);
		cursor: ew-resize;
		touch-action: none;
		user-select: none;
		-webkit-user-select: none;
	}
	.ruler:focus-visible {
		outline-offset: -2px;
	}
	.ruler-range {
		position: absolute;
		top: 0;
		bottom: 0;
		background: rgb(240 166 70 / 0.16);
		border-inline: 1px solid rgb(240 166 70 / 0.5);
		pointer-events: none;
	}
	.tick {
		position: absolute;
		top: 0;
		bottom: 0;
		padding: 0 0 0 4px;
		border-left: 1px solid rgb(255 255 255 / 0.25);
		color: var(--text-faint);
		font-size: 0.68rem;
		font-weight: 600;
		line-height: 26px;
		font-variant-numeric: tabular-nums;
		white-space: nowrap;
		pointer-events: none;
	}
	.knob {
		position: absolute;
		top: 3px;
		z-index: 2;
		width: 16px;
		height: 20px;
		margin-left: -8px;
		border-radius: 4px 4px 8px 8px;
		background: #ff5a5a;
		box-shadow: 0 1px 4px rgb(0 0 0 / 0.5);
		pointer-events: none;
	}
	.frames {
		position: relative;
		height: 44px;
		overflow: hidden;
		border-bottom: 1px solid var(--border);
	}
	.frame {
		position: absolute;
		top: 0;
		height: 44px;
		background-repeat: no-repeat;
		opacity: 0.85;
	}
	.wave {
		height: 72px;
	}
	.wave canvas {
		display: block;
		height: 72px;
	}
	.wave-note {
		margin: 0;
		padding: 1.6rem 0.8rem;
		font-size: 0.85rem;
	}
	.seg {
		position: absolute;
		top: 27px; /* below the ruler */
		bottom: 0;
		pointer-events: none;
	}
	.seg.dropped {
		background: repeating-linear-gradient(135deg, rgb(0 0 0 / 0.45) 0 6px, rgb(0 0 0 / 0.25) 6px 12px);
	}
	.seg.selected {
		box-shadow: inset 0 0 0 2px var(--accent);
	}
	.cut {
		position: absolute;
		top: 27px; /* below the ruler, which belongs to the red marker */
		bottom: 0;
		width: 24px;
		margin-left: -12px;
		padding: 0;
		border: 0;
		background: transparent;
		cursor: ew-resize;
		touch-action: none;
	}
	.cut span {
		display: block;
		width: 3px;
		height: 100%;
		margin: 0 auto;
		border-radius: 2px;
		background: #fff;
		box-shadow: 0 0 4px rgb(0 0 0 / 0.6);
	}
	.cut.selected span {
		background: var(--accent-strong);
		width: 4px;
	}
	/* The handles at the recording's start and end sit inside the track */
	.cut.edge {
		width: 18px;
		margin-left: 0;
	}
	.cut.edge.tail {
		margin-left: -18px;
	}
	.cut.edge span {
		width: 6px;
		margin: 0;
		border-radius: 0 3px 3px 0;
		background: var(--accent);
	}
	.cut.edge.tail span {
		margin-left: auto;
		border-radius: 3px 0 0 3px;
	}
	.speeds {
		display: inline-flex;
		gap: 0.2rem;
	}
	.speeds .btn[aria-pressed='true'] {
		background: var(--accent-soft);
		border-color: rgb(240 166 70 / 0.45);
		color: var(--accent-strong);
	}
	.playhead {
		position: absolute;
		top: 0;
		bottom: 0;
		z-index: 1;
		width: 2px;
		margin-left: -1px;
		background: #ff5a5a;
		pointer-events: none;
	}
	.skip {
		min-width: 3.4rem;
		font-weight: 650;
		font-variant-numeric: tabular-nums;
	}

	/* --- pieces --- */
	.whole {
		display: flex;
		margin-top: 0.9rem;
		font-size: 0.92rem;
	}
	.pieces {
		display: grid;
		gap: 0.5rem;
		margin: 0.8rem 0 0;
		padding: 0;
		list-style: none;
	}
	.pieces li {
		display: grid;
		gap: 0.45rem;
		padding: 0.6rem 0.75rem;
		border: 1px solid var(--border);
		border-radius: var(--radius-sm);
		background: var(--surface);
	}
	.pieces li.selected {
		border-color: rgb(240 166 70 / 0.55);
	}
	.pieces li.dropped {
		background: transparent;
		border-style: dashed;
	}
	.piece-head {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}
	.piece-time {
		padding: 0;
		border: 0;
		background: none;
		color: var(--text);
		font-weight: 650;
		font-variant-numeric: tabular-nums;
		cursor: pointer;
	}
	.badge {
		padding: 0.05rem 0.45rem;
		border-radius: 999px;
		background: var(--accent-soft);
		color: var(--accent-strong);
		font-size: 0.72rem;
		font-weight: 700;
	}
	.chips {
		display: flex;
		flex-wrap: wrap;
		gap: 0.3rem;
	}
	.chip {
		padding: 0.25rem 0.65rem;
		border-radius: 999px;
		border: 1px solid var(--border);
		background: transparent;
		color: var(--text-muted);
		font-size: 0.82rem;
		font-weight: 600;
		cursor: pointer;
	}
	.chip[aria-pressed='true'] {
		background: var(--accent-soft);
		border-color: rgb(240 166 70 / 0.45);
		color: var(--accent-strong);
	}
	.piece-fields {
		display: flex;
		flex-wrap: wrap;
		gap: 0.4rem;
	}
	.piece-fields select {
		width: auto;
		min-height: 34px;
		padding: 0.25rem 0.5rem;
	}
	.title-field {
		flex: 1 1 200px;
	}
	.title-field .field {
		min-height: 34px;
		padding: 0.25rem 0.6rem;
	}
	.save-bar {
		position: sticky;
		bottom: 0;
		display: flex;
		align-items: center;
		gap: 0.5rem;
		margin-top: 1rem;
		padding: 0.7rem 0 calc(0.7rem + env(safe-area-inset-bottom));
		background: linear-gradient(transparent, var(--bg) 30%);
	}
</style>
