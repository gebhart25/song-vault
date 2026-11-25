<script lang="ts">
	import { strumSlots, type StrumKind } from '#lib/strum.ts';

	// One strumming pattern drawn as arrows, with the beat counted under it when the
	// strums fill the bar evenly (spec 5.2). Free text is shown as typed.
	let { pattern, timeSignature = null }: { pattern: string; timeSignature?: string | null } = $props();

	const GLYPHS: Record<StrumKind, string> = { down: '↓', up: '↑', mute: '✕', rest: '·' };
	const SPOKEN: Record<StrumKind, string> = { down: 'down', up: 'up', mute: 'muted', rest: 'miss' };

	const parsed = $derived(strumSlots(pattern, timeSignature));
	const spoken = $derived(
		parsed ? parsed.slots.map((s) => (s.soft ? `light ${SPOKEN[s.kind]}` : SPOKEN[s.kind])).join(', ') : ''
	);
</script>

{#if parsed}
	<span class="strum" class:counted={parsed.counted} role="img" aria-label="{spoken} ({pattern})">
		{#each parsed.slots as s, i (i)}
			<span
				class="slot"
				data-kind={s.kind}
				class:soft={s.soft}
				class:beat={parsed.counted ? /^\d/.test(s.count) && i > 0 : s.gap}
			>
				<span class="glyph">{GLYPHS[s.kind]}</span>
				{#if parsed.counted}<span class="count" class:on-beat={/^\d/.test(s.count)}>{s.count}</span>{/if}
			</span>
		{/each}
	</span>
{:else}
	<span class="free">{pattern}</span>
{/if}

<style>
	.strum {
		display: inline-flex;
		flex-wrap: wrap;
		align-items: flex-start;
		row-gap: 0.3rem;
		vertical-align: top;
	}
	.slot {
		display: grid;
		justify-items: center;
		min-width: 1.3em;
	}
	/* A little room before each beat (or each typed group) so the bar reads in beats. */
	.slot.beat {
		margin-left: 0.4em;
	}
	.glyph {
		font-size: 1.15em;
		font-weight: 800;
		line-height: 1.15;
		color: var(--accent-strong);
	}
	.slot[data-kind='rest'] .glyph {
		color: var(--text-faint);
	}
	.slot[data-kind='mute'] .glyph {
		font-size: 0.85em;
		line-height: 1.5;
		color: var(--text-muted);
	}
	.soft .glyph {
		opacity: 0.55;
	}
	.count {
		font-size: 0.68rem;
		font-weight: 600;
		color: var(--text-faint);
		line-height: 1.2;
	}
	.count.on-beat {
		color: var(--text-muted);
	}
	.free {
		font-weight: 600;
	}
</style>
