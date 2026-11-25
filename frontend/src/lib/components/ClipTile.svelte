<script lang="ts" module>
	/** Where a tile's menu was asked for. `sheet`: it came from a touch, so show a bottom sheet. */
	export interface MenuAt {
		x: number;
		y: number;
		sheet: boolean;
	}
</script>

<script lang="ts">
	import { duration, shortDate } from '#lib/format.ts';
	import type { ClipSummary } from '#lib/types.ts';
	import Icon from './Icon.svelte';
	import Waveform from './Waveform.svelte';

	let {
		clip,
		onplay,
		onmenu,
		selected
	}: {
		clip: ClipSummary;
		onplay?: (clip: ClipSummary) => void;
		/** Long-press (phone) or right-click (desktop) opens the tile's menu (spec 4.3). */
		onmenu?: (clip: ClipSummary, at: MenuAt) => void;
		/** Set while picking clips for a bulk change; undefined otherwise. */
		selected?: boolean;
	} = $props();

	const date = $derived(shortDate(clip.recorded_at ?? clip.uploaded_at));
	const label = $derived(
		[
			clip.title || (clip.media_type === 'audio' ? 'Audio clip' : 'Video clip'),
			clip.role,
			clip.status === 'current' && 'current',
			date
		]
			.filter(Boolean)
			.join(', ')
	);

	// Chrome on Android fires contextmenu on a long-press; the timer covers browsers that don't.
	const LONG_PRESS_MS = 520;
	let pressTimer: ReturnType<typeof setTimeout> | undefined;
	let pressAt: { x: number; y: number } | null = null;
	let openedAt = 0;
	let swallowClick = false;

	function openMenu(x: number, y: number, sheet: boolean) {
		cancelPress();
		if (!onmenu || Date.now() - openedAt < 800) return; // the same press, reported twice
		openedAt = Date.now();
		swallowClick = sheet; // lifting the finger mustn't also play the clip
		onmenu(clip, { x, y, sheet });
	}

	function cancelPress() {
		clearTimeout(pressTimer);
		pressAt = null;
	}

	function oncontextmenu(e: MouseEvent) {
		if (!onmenu) return;
		e.preventDefault();
		const touch = (e as PointerEvent).pointerType === 'touch' || matchMedia('(pointer: coarse)').matches;
		if (e.clientX === 0 && e.clientY === 0) {
			// From the keyboard (menu key, Shift+F10): open it at the tile.
			const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
			openMenu(r.left + r.width / 2, r.top + r.height / 2, touch);
		} else openMenu(e.clientX, e.clientY, touch);
	}

	function onpointerdown(e: PointerEvent) {
		swallowClick = false;
		if (!onmenu || e.pointerType !== 'touch') return;
		const at = { x: e.clientX, y: e.clientY };
		pressAt = at;
		clearTimeout(pressTimer);
		pressTimer = setTimeout(() => pressAt === at && openMenu(at.x, at.y, true), LONG_PRESS_MS);
	}

	// Hovering a video tile with a mouse scrubs through its preview strip (spec 5.4).
	let scrub = $state<{ at: number; w: number; h: number } | null>(null);
	let strip = $state<{ w: number; h: number } | null>(null);

	/** Frames across in the recording's preview grid (the server's PREVIEW_COLUMNS). */
	const PREVIEW_COLUMNS = 10;

	/** Where the grid goes so the frame under the pointer covers the tile, like the thumbnail.
	 * The grid covers the whole recording; a clip only shows the frames from its own stretch. */
	const stripStyle = $derived.by(() => {
		const frames = clip.preview_frames ?? 0;
		if (!scrub || !strip || frames < 1) return null;
		const columns = Math.min(frames, PREVIEW_COLUMNS);
		const rows = Math.ceil(frames / columns);
		const frameW = strip.w / columns;
		const frameH = strip.h / rows;
		const total = clip.recording_duration ?? 0;
		const start = clip.start_seconds ?? 0;
		const end = clip.end_seconds ?? total;
		const at = start + scrub.at * Math.max(end - start, 0);
		const i = Math.min(frames - 1, Math.floor(total ? (at / total) * frames : scrub.at * frames));
		const scale = Math.max(scrub.w / frameW, scrub.h / frameH);
		return {
			width: `${strip.w * scale}px`,
			height: `${strip.h * scale}px`,
			left: `${-(i % columns) * frameW * scale + (scrub.w - frameW * scale) / 2}px`,
			top: `${-Math.floor(i / columns) * frameH * scale + (scrub.h - frameH * scale) / 2}px`
		};
	});

	function onpointermove(e: PointerEvent) {
		if (pressAt && Math.hypot(e.clientX - pressAt.x, e.clientY - pressAt.y) > 10) cancelPress();
		if (e.pointerType !== 'mouse' || !clip.preview_url || selected !== undefined) return;
		const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
		scrub = { at: Math.min(0.999, Math.max(0, (e.clientX - r.left) / r.width)), w: r.width, h: r.height };
	}

	function onpointerleave() {
		cancelPress();
		scrub = null;
	}

	function onclick() {
		if (swallowClick) {
			swallowClick = false;
			return;
		}
		onplay?.(clip);
	}
</script>

<button
	class="tile"
	class:discarded={clip.status === 'discarded'}
	class:selecting={selected !== undefined}
	class:selected
	aria-label={selected !== undefined ? `Select ${label}` : label}
	aria-pressed={selected}
	title={clip.title || undefined}
	{onclick}
	{oncontextmenu}
	{onpointerdown}
	{onpointermove}
	onpointerup={cancelPress}
	onpointercancel={cancelPress}
	{onpointerleave}
>
	{#if clip.thumbnail_url}
		<img src={clip.thumbnail_url} alt="" loading="lazy" draggable="false" />
	{:else if clip.media_type === 'audio' && clip.waveform}
		<span class="placeholder audio with-wave">
			<Icon name="mic" size={18} stroke={1.8} />
			<span class="tile-wave"><Waveform peaks={clip.waveform} bars={30} /></span>
		</span>
	{:else}
		<span class="placeholder" class:audio={clip.media_type === 'audio'}>
			<Icon name={clip.media_type === 'audio' ? 'mic' : 'video'} size={26} stroke={1.6} />
		</span>
	{/if}
	{#if scrub && clip.preview_url}
		<span class="scrub" aria-hidden="true">
			<img
				src={clip.preview_url}
				alt=""
				draggable="false"
				class:ready={stripStyle}
				style:width={stripStyle?.width}
				style:height={stripStyle?.height}
				style:left={stripStyle?.left}
				style:top={stripStyle?.top}
				onload={(e) => {
					const img = e.currentTarget as HTMLImageElement;
					strip = { w: img.naturalWidth, h: img.naturalHeight };
				}}
			/>
			<span class="scrub-bar" style:width="{scrub.at * 100}%"></span>
		</span>
	{/if}
	{#if clip.status === 'current'}
		<span class="badge" title={clip.role ? `Current ${clip.role} take` : undefined}>Current{#if clip.role}&nbsp;· {clip.role}{/if}</span>
	{:else if clip.role}
		<span class="badge role">{clip.role}</span>
	{/if}
	{#if selected !== undefined}
		<span class="check" aria-hidden="true">{#if selected}<Icon name="check" size={16} stroke={3} />{/if}</span>
	{:else if clip.processing === 'pending'}
		<span class="state" title="Making a playable copy"><span class="dot"></span>Processing</span>
	{:else if clip.processing === 'failed'}
		<span class="state failed" title="Processing failed; open the clip to retry">!</span>
	{/if}
	<span class="overlay">
		<span>{date}</span>
		{#if clip.duration_seconds != null}<span>{duration(clip.duration_seconds)}</span>{/if}
	</span>
</button>

<style>
	.tile {
		position: relative;
		flex: 0 0 var(--tile-w);
		width: var(--tile-w);
		aspect-ratio: 3 / 4;
		padding: 0;
		border: 1px solid var(--border);
		border-radius: 10px;
		overflow: hidden;
		background: var(--surface-2);
		cursor: pointer;
		scroll-snap-align: start;
		user-select: none;
		-webkit-user-select: none;
		-webkit-touch-callout: none;
		transition:
			border-color 0.12s,
			scale 0.12s;
	}
	.selecting img,
	.selecting .placeholder {
		transition: opacity 0.12s;
	}
	.selecting:not(.selected) img,
	.selecting:not(.selected) .placeholder {
		opacity: 0.6;
	}
	.selected {
		border-color: var(--accent);
		box-shadow: inset 0 0 0 2px var(--accent);
	}
	.selected:hover {
		border-color: var(--accent);
	}
	.check {
		position: absolute;
		top: 0.4rem;
		right: 0.4rem;
		display: grid;
		place-items: center;
		width: 24px;
		height: 24px;
		border-radius: 50%;
		border: 2px solid #fff;
		background: rgb(0 0 0 / 0.35);
		color: var(--accent-ink);
		box-shadow: 0 1px 4px rgb(0 0 0 / 0.5);
	}
	.selected .check {
		border-color: var(--accent);
		background: var(--accent);
	}
	.tile:hover {
		border-color: #4a5262;
	}
	.tile:active {
		scale: 0.98;
	}
	img {
		width: 100%;
		height: 100%;
		object-fit: cover;
		display: block;
	}
	.placeholder {
		display: grid;
		place-items: center;
		width: 100%;
		height: 100%;
		color: var(--text-faint);
		background: linear-gradient(160deg, #262b35, #191c22);
	}
	.placeholder.audio {
		color: #c9a46b;
		background: linear-gradient(160deg, #2f281d, #1b1914);
	}
	.placeholder.with-wave {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 0.6rem;
		padding: 0 0.6rem 1.6rem;
	}
	.tile-wave {
		width: 100%;
		height: 38%;
	}
	/* The preview strip, moved so the frame under the pointer fills the tile */
	.scrub {
		position: absolute;
		inset: 0;
		overflow: hidden;
		background: #000;
		pointer-events: none;
	}
	.scrub img {
		position: absolute;
		max-width: none;
		width: auto;
		height: auto;
		object-fit: fill;
		visibility: hidden;
	}
	.scrub img.ready {
		visibility: visible;
	}
	.scrub-bar {
		position: absolute;
		left: 0;
		bottom: 0;
		height: 3px;
		background: var(--accent);
	}
	.overlay {
		position: absolute;
		inset: auto 0 0 0;
		display: flex;
		justify-content: space-between;
		gap: 0.25rem;
		padding: 1.4rem 0.45rem 0.35rem;
		font-size: 0.75rem;
		font-weight: 650;
		font-variant-numeric: tabular-nums;
		color: #fff;
		text-shadow: 0 1px 2px rgb(0 0 0 / 0.6);
		background: linear-gradient(transparent, rgb(0 0 0 / 0.72));
	}
	.badge {
		position: absolute;
		top: 0.4rem;
		left: 0.4rem;
		padding: 0.05rem 0.45rem;
		border-radius: 999px;
		font-size: 0.7rem;
		font-weight: 700;
		background: var(--accent);
		color: var(--accent-ink);
		max-width: calc(100% - 0.8rem);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.badge.role {
		background: rgb(17 19 23 / 0.82);
		color: var(--text);
		font-weight: 650;
	}
	.discarded {
		opacity: 0.45;
		filter: grayscale(0.7);
	}
	.state {
		position: absolute;
		top: 0.4rem;
		right: 0.4rem;
		display: inline-flex;
		align-items: center;
		gap: 0.3rem;
		padding: 0.05rem 0.45rem;
		border-radius: 999px;
		font-size: 0.68rem;
		font-weight: 650;
		background: rgb(17 19 23 / 0.8);
		color: var(--text-muted);
	}
	.state.failed {
		background: var(--danger);
		color: #fff;
		min-width: 1.3rem;
		justify-content: center;
	}
	.dot {
		width: 6px;
		height: 6px;
		border-radius: 50%;
		background: var(--accent);
		animation: blink 1s ease-in-out infinite alternate;
	}
	@keyframes blink {
		to {
			opacity: 0.25;
		}
	}
</style>
