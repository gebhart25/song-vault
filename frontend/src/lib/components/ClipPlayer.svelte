<script lang="ts">
	import type { Snippet } from 'svelte';
	import { countdown } from '#lib/countdown.ts';
	import { duration as clock } from '#lib/format.ts';
	import Icon from './Icon.svelte';

	// Plays one clip: a stretch of a recording (spec 5.10). The file is the whole
	// recording; the player keeps to [start, end) and counts time from the clip's start.
	// Its own controls (the browser's would show the whole recording), fullscreen
	// included, and the A/B loop (spec 5.3) so the two never fight over the playhead.
	let {
		src,
		kind,
		poster = null,
		start = 0,
		end = null,
		speed = 1,
		autoplay = false,
		loop = null,
		resume = null,
		element = $bindable(),
		time = $bindable(0),
		length = $bindable(0),
		paused = $bindable(true),
		countIn = 0,
		art
	}: {
		src: string;
		kind: 'video' | 'audio';
		poster?: string | null;
		/** Where the clip starts and ends in the recording, in seconds (end null = the end) */
		start?: number;
		end?: number | null;
		speed?: number;
		autoplay?: boolean;
		/** Loop between these, in seconds into the clip */
		loop?: [number, number] | null;
		/** Pick up here once the file has loaded (after switching between video and audio only) */
		resume?: { time: number; play: boolean } | null;
		element?: HTMLMediaElement;
		/** Seconds into the clip */
		time?: number;
		/** The clip's length */
		length?: number;
		paused?: boolean;
		/** Seconds to count down before playing (0 = play straight away), to get your hands
		 * on the instrument and play along (spec 3.5) */
		countIn?: number;
		/** Shown above the controls of an audio clip */
		art?: Snippet;
	} = $props();

	let box: HTMLDivElement | undefined = $state();
	let fileLength = $state(0);
	let resumed: object | null = null;
	const stop = $derived(end ?? fileLength);

	$effect(() => {
		length = Math.max(0, stop - start);
	});

	$effect(() => {
		if (element) element.playbackRate = speed;
	});

	function onloadedmetadata() {
		if (!element) return;
		fileLength = element.duration || 0;
		element.playbackRate = speed;
		const pickUp = resume && resume !== resumed ? resume : null;
		resumed = resume;
		element.currentTime = start + (pickUp?.time ?? 0);
		if (pickUp?.play) element.play().catch(() => {}); // carrying on: no countdown
		else if (!pickUp && autoplay) play();
	}

	// --- the countdown before playing -----------------------------------------------------

	/** Seconds left before playing starts; null when not counting */
	let counting = $state<number | null>(null);
	let cancelCount: (() => void) | null = null;

	/** Play, after the countdown if it's on. */
	function play() {
		if (!element) return;
		if (countIn <= 0) {
			element.play().catch(() => {});
			return;
		}
		if (counting !== null) return;
		// Show where it will start from while counting.
		if (stop && (element.currentTime >= stop - 0.05 || element.currentTime < start - 0.05)) element.currentTime = start;
		cancelCount = countdown(
			(left) => (counting = left),
			() => {
				cancelCount = null;
				counting = null;
				element?.play().catch(() => {});
			},
			countIn
		);
	}

	function cancelCountdown() {
		cancelCount?.();
		cancelCount = null;
		counting = null;
	}

	$effect(() => () => cancelCount?.());

	/** Back to the start when playing from the end or from outside the stretch. */
	function onplay() {
		paused = false;
		if (counting !== null) cancelCountdown(); // started some other way (lock screen, headphones)
		if (!element || !stop) return;
		if (element.currentTime >= stop - 0.05 || element.currentTime < start - 0.05) element.currentTime = start;
		fade(element); // straight away, so playing from a cut starts silent
	}

	/** Seconds of fade in and out where a clip is cut from its recording, so the cut doesn't click */
	const FADE = 0.04;

	/** The sound fades in and out at a cut, not at the recording's own start and end.
	 * (Phones that ignore the volume setting just don't fade.) */
	function fade(el: HTMLMediaElement) {
		if (!stop) return;
		const t = el.currentTime;
		const fadeIn = start > 0.05 ? (t - start) / FADE : 1;
		const fadeOut = stop < fileLength - 0.05 ? (stop - t) / FADE : 1;
		el.volume = Math.max(0, Math.min(1, fadeIn, fadeOut));
	}

	// Keep to the stretch (and the loop), checked every frame while playing: tighter than
	// timeupdate, which only comes 4 times a second.
	$effect(() => {
		if (!element || paused || !stop) return;
		const el = element;
		const from = start;
		const to = stop;
		const a = loop ? from + loop[0] : null;
		const b = loop ? from + loop[1] : null;
		let frame = requestAnimationFrame(function tick() {
			if (a !== null && b !== null && el.currentTime >= b) el.currentTime = a;
			else if (el.currentTime >= to) {
				el.pause();
				el.currentTime = to;
			}
			fade(el);
			frame = requestAnimationFrame(tick);
		});
		return () => cancelAnimationFrame(frame);
	});

	// A loop that runs to the end of the file: the media stops, so start it again.
	function onended() {
		if (loop && element) {
			element.currentTime = start + loop[0];
			element.play().catch(() => {});
		}
	}

	function ontimeupdate() {
		if (element) time = Math.min(Math.max(element.currentTime - start, 0), length);
	}

	function toggle() {
		if (!element) return;
		if (counting !== null) cancelCountdown();
		else if (element.paused) play();
		else element.pause();
	}

	function seek(to: number) {
		if (element) element.currentTime = start + Math.min(Math.max(to, 0), length);
	}

	function fullscreen() {
		if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
		else box?.requestFullscreen().catch(() => {});
	}

	function onkeydown(e: KeyboardEvent) {
		if (e.key === ' ' && e.target === box) {
			e.preventDefault();
			toggle();
		}
	}
</script>

<!-- Space plays and pauses once the player has focus. -->
<!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
<div class="player" class:audio={kind === 'audio'} bind:this={box} tabindex="0" role="group" aria-label="Player" {onkeydown}>
	{#if kind === 'video'}
		<!-- svelte-ignore a11y_media_has_caption, a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions -->
		<video
			bind:this={element}
			{src}
			poster={poster ?? undefined}
			playsinline
			preload="metadata"
			onclick={toggle}
			{onloadedmetadata}
			{onplay}
			onpause={() => (paused = true)}
			{ontimeupdate}
			{onended}
		></video>
	{:else}
		{@render art?.()}
		<audio bind:this={element} {src} preload="metadata" {onloadedmetadata} {onplay} onpause={() => (paused = true)} {ontimeupdate} {onended}></audio>
	{/if}
	{#if counting !== null}
		<button class="count" aria-label="Playing in {counting} seconds. Cancel" onclick={cancelCountdown}>
			<span class="count-n" aria-hidden="true">{counting}</span>
			<span class="count-note" aria-hidden="true">Tap to cancel</span>
		</button>
	{/if}
	<div class="controls">
		<button class="play" aria-label={counting !== null ? 'Cancel the countdown' : paused ? 'Play' : 'Pause'} onclick={toggle}>
			<Icon name={counting !== null ? 'x' : paused ? 'play' : 'pause'} size={20} />
		</button>
		<span class="time">{clock(time)}</span>
		<input
			class="scrub"
			type="range"
			min="0"
			max={length || 0}
			step="0.01"
			value={time}
			aria-label="Position"
			aria-valuetext="{clock(time)} of {clock(length)}"
			oninput={(e) => seek(Number(e.currentTarget.value))}
			style:--at="{length ? (time / length) * 100 : 0}%"
		/>
		<span class="time">{clock(length)}</span>
		{#if kind === 'video'}
			<button class="icon" aria-label="Full screen" onclick={fullscreen}><Icon name="fullscreen" size={18} /></button>
		{/if}
	</div>
</div>

<style>
	.player {
		position: relative;
		display: grid;
		width: 100%;
		background: #000;
	}
	.player:focus-visible {
		outline-offset: -2px;
	}
	.player.audio {
		justify-items: center;
		gap: 1rem;
		padding: 1.5rem 1rem 0.5rem;
		background: linear-gradient(160deg, #2f281d, #16140f);
	}
	video {
		display: block;
		width: 100%;
		max-height: 62vh;
		background: #000;
		cursor: pointer;
	}
	.player:fullscreen {
		align-content: center;
	}
	.player:fullscreen video {
		max-height: calc(100vh - 56px);
	}
	/* The countdown, over the picture (the controls stay usable) */
	.count {
		position: absolute;
		inset: 0 0 52px 0;
		z-index: 1;
		display: grid;
		place-content: center;
		justify-items: center;
		gap: 0.3rem;
		padding: 0;
		border: 0;
		background: rgb(0 0 0 / 0.5);
		color: #fff;
		text-shadow: 0 2px 10px rgb(0 0 0 / 0.6);
		cursor: pointer;
	}
	.count-n {
		font-size: clamp(3rem, 14vw, 6rem);
		font-weight: 800;
		line-height: 1;
		font-variant-numeric: tabular-nums;
	}
	.count-note {
		font-size: 0.9rem;
		font-weight: 600;
	}
	.controls {
		display: flex;
		align-items: center;
		gap: 0.55rem;
		width: 100%;
		padding: 0.45rem 0.7rem 0.55rem;
		background: rgb(0 0 0 / 0.55);
		color: #fff;
	}
	.audio .controls {
		background: transparent;
		padding-inline: 0;
	}
	.play,
	.icon {
		display: grid;
		place-items: center;
		flex-shrink: 0;
		width: 34px;
		height: 34px;
		padding: 0;
		border: 0;
		border-radius: 50%;
		background: transparent;
		color: inherit;
		cursor: pointer;
	}
	.play {
		background: rgb(255 255 255 / 0.14);
	}
	.play:hover,
	.icon:hover {
		background: rgb(255 255 255 / 0.22);
	}
	.play :global(svg) {
		fill: currentColor;
	}
	.time {
		flex-shrink: 0;
		min-width: 2.6rem;
		font-size: 0.8rem;
		font-weight: 600;
		font-variant-numeric: tabular-nums;
		text-align: center;
	}
	.scrub {
		flex: 1;
		min-width: 0;
		height: 22px;
		margin: 0;
		background: transparent;
		cursor: pointer;
		-webkit-appearance: none;
		appearance: none;
	}
	.scrub::-webkit-slider-runnable-track {
		height: 4px;
		border-radius: 2px;
		background: linear-gradient(to right, var(--accent) var(--at), rgb(255 255 255 / 0.3) var(--at));
	}
	.scrub::-moz-range-track {
		height: 4px;
		border-radius: 2px;
		background: linear-gradient(to right, var(--accent) var(--at), rgb(255 255 255 / 0.3) var(--at));
	}
	.scrub::-webkit-slider-thumb {
		-webkit-appearance: none;
		width: 14px;
		height: 14px;
		margin-top: -5px;
		border-radius: 50%;
		background: #fff;
	}
	.scrub::-moz-range-thumb {
		width: 14px;
		height: 14px;
		border: 0;
		border-radius: 50%;
		background: #fff;
	}
</style>
