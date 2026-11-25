<script lang="ts">
	import type { ClipSummary } from '#lib/types.ts';
	import ClipTile, { type MenuAt } from './ClipTile.svelte';
	import Icon from './Icon.svelte';

	// A horizontal strip of clips: newest on the left, older to the right (spec 4.1, 4.4).
	// The server puts the Current take first; a divider sets it apart from the rest (4.3).
	let {
		clips,
		onplay,
		onmenu,
		selected
	}: {
		clips: ClipSummary[];
		onplay?: (clip: ClipSummary) => void;
		onmenu?: (clip: ClipSummary, at: MenuAt) => void;
		/** Clip ids picked for a bulk change; undefined when not picking. */
		selected?: ReadonlySet<number>;
	} = $props();

	const pinned = $derived(clips.findIndex((c) => c.status !== 'current'));

	let scroller: HTMLDivElement | undefined = $state();
	let atStart = $state(true);
	let atEnd = $state(true);
	let hiddenRight = $state(0);

	function measure() {
		if (!scroller) return;
		const { scrollLeft, scrollWidth, clientWidth } = scroller;
		atStart = scrollLeft <= 2;
		atEnd = scrollLeft + clientWidth >= scrollWidth - 2;
		const tiles = Array.from(scroller.querySelectorAll<HTMLElement>('.tile'));
		const right = scrollLeft + clientWidth;
		hiddenRight = tiles.filter((t) => t.offsetLeft + t.offsetWidth / 2 > right).length;
	}

	$effect(() => {
		if (!scroller) return;
		void clips.length; // re-measure when clips change
		measure();
		const ro = new ResizeObserver(measure);
		ro.observe(scroller);
		return () => ro.disconnect();
	});

	function page(direction: 1 | -1) {
		scroller?.scrollBy({ left: direction * scroller.clientWidth * 0.8, behavior: 'smooth' });
	}
</script>

<div class="strip" class:fade-left={!atStart} class:fade-right={!atEnd}>
	<div class="scroller" bind:this={scroller} onscroll={measure}>
		{#each clips as clip, i (clip.id)}
			{#if i > 0 && i === pinned}<span class="pin-divider" aria-hidden="true"></span>{/if}
			<ClipTile {clip} {onplay} {onmenu} selected={selected ? selected.has(clip.id) : undefined} />
		{/each}
	</div>
	{#if !atStart}
		<button class="arrow left" onclick={() => page(-1)} aria-label="Show newer clips">
			<Icon name="chevron-left" size={20} />
		</button>
	{/if}
	{#if !atEnd}
		<button class="arrow right" onclick={() => page(1)} aria-label="Show older clips">
			<Icon name="chevron-right" size={20} />
		</button>
		{#if hiddenRight > 0}<span class="more">+{hiddenRight} older</span>{/if}
	{/if}
</div>

<style>
	.strip {
		position: relative;
	}
	.scroller {
		display: flex;
		gap: 0.6rem;
		overflow-x: auto;
		overscroll-behavior-x: contain;
		scroll-snap-type: x mandatory;
		scroll-padding-inline: var(--gutter);
		padding: 0.15rem var(--gutter) 0.35rem;
		scrollbar-width: none;
	}
	.scroller::-webkit-scrollbar {
		display: none;
	}
	.pin-divider {
		flex: 0 0 2px;
		align-self: stretch;
		margin: 0.6rem 0.1rem;
		border-radius: 2px;
		background: var(--accent);
		opacity: 0.7;
	}
	.strip.fade-right .scroller {
		mask-image: linear-gradient(to right, #000 calc(100% - 64px), transparent);
	}
	.strip.fade-left.fade-right .scroller {
		mask-image: linear-gradient(to right, transparent, #000 48px, #000 calc(100% - 64px), transparent);
	}
	.strip.fade-left:not(.fade-right) .scroller {
		mask-image: linear-gradient(to right, transparent, #000 48px);
	}
	.more {
		position: absolute;
		right: 0.6rem;
		top: 0.55rem;
		padding: 0.1rem 0.5rem;
		border-radius: 999px;
		font-size: 0.72rem;
		font-weight: 650;
		background: rgb(17 19 23 / 0.85);
		border: 1px solid var(--border);
		color: var(--text-muted);
		pointer-events: none;
	}
	.arrow {
		position: absolute;
		top: 50%;
		translate: 0 -50%;
		display: none;
		place-items: center;
		width: 36px;
		height: 36px;
		border-radius: 50%;
		border: 1px solid var(--border);
		background: rgb(32 36 44 / 0.95);
		color: var(--text);
		cursor: pointer;
		box-shadow: 0 4px 14px rgb(0 0 0 / 0.4);
	}
	.arrow.left {
		left: 0.4rem;
	}
	.arrow.right {
		right: 0.4rem;
	}
	/* Desktop: arrow buttons appear on hover at the strip edges. Touch: native swipe. */
	@media (hover: hover) and (pointer: fine) {
		.strip:hover .arrow {
			display: grid;
		}
	}
</style>
