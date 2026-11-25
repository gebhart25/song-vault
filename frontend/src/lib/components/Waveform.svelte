<script lang="ts">
	// An audio clip's waveform (spec 5.9), drawn from its peaks (0-100). With `progress`
	// the played part is lit up; with `onseek` it works as a seek bar (tap, drag, arrow keys).
	let {
		peaks,
		bars = peaks.length,
		progress = null,
		loop = null,
		onseek,
		label = 'Waveform'
	}: {
		peaks: number[];
		/** Draw this many bars (fewer than the peaks: each bar is the loudest of its slice) */
		bars?: number;
		/** 0-1 */
		progress?: number | null;
		/** The A/B loop, as 0-1 fractions */
		loop?: [number, number] | null;
		onseek?: (fraction: number) => void;
		label?: string;
	} = $props();

	const shown = $derived.by(() => {
		if (bars >= peaks.length) return peaks;
		const out: number[] = [];
		for (let i = 0; i < bars; i++) {
			const from = Math.floor((i * peaks.length) / bars);
			const to = Math.max(from + 1, Math.floor(((i + 1) * peaks.length) / bars));
			out.push(Math.max(...peaks.slice(from, to)));
		}
		return out;
	});
	const width = $derived(shown.length * 3);
	let dragging = false;

	function seekTo(e: PointerEvent) {
		const r = (e.currentTarget as Element).getBoundingClientRect();
		onseek?.(Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)));
	}

	function onkeydown(e: KeyboardEvent) {
		if (!onseek || progress === null) return;
		const step = e.key === 'ArrowRight' ? 0.05 : e.key === 'ArrowLeft' ? -0.05 : 0;
		if (!step) return;
		e.preventDefault();
		onseek(Math.min(1, Math.max(0, progress + step)));
	}
</script>

{#snippet drawing()}
	<svg viewBox="0 0 {width} 100" preserveAspectRatio="none" aria-hidden="true">
		{#if loop}
			<rect class="loop" x={loop[0] * width} y="0" width={Math.max(1, (loop[1] - loop[0]) * width)} height="100" />
		{/if}
		{#each shown as peak, i (i)}
			{@const h = Math.max(6, peak)}
			<rect
				class="bar"
				class:played={progress !== null && (i + 0.5) / shown.length <= progress}
				x={i * 3}
				y={(100 - h) / 2}
				width="2"
				height={h}
			/>
		{/each}
	</svg>
{/snippet}

{#if onseek}
	<div
		class="wave seek"
		role="slider"
		tabindex="0"
		aria-label={label}
		aria-valuemin={0}
		aria-valuemax={100}
		aria-valuenow={Math.round((progress ?? 0) * 100)}
		{onkeydown}
		onpointerdown={(e) => {
			dragging = true;
			e.currentTarget.setPointerCapture(e.pointerId);
			seekTo(e);
		}}
		onpointermove={(e) => dragging && seekTo(e)}
		onpointerup={() => (dragging = false)}
		onpointercancel={() => (dragging = false)}
	>
		{@render drawing()}
	</div>
{:else}
	<div class="wave">{@render drawing()}</div>
{/if}

<style>
	.wave {
		width: 100%;
		height: 100%;
	}
	.seek {
		cursor: pointer;
		touch-action: none;
		border-radius: 6px;
	}
	svg {
		display: block;
		width: 100%;
		height: 100%;
	}
	.bar {
		fill: var(--wave-color, rgb(201 164 107 / 0.55));
	}
	.bar.played {
		fill: var(--wave-played, var(--accent));
	}
	.loop {
		fill: var(--accent-soft);
	}
</style>
