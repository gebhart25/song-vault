<script lang="ts">
	import { untrack } from 'svelte';
	import { ApiError } from '#lib/api.ts';
	import { plural } from '#lib/format.ts';
	import { findRhymes, type RhymeGroup, type Rhymes } from '#lib/words.ts';
	import Icon from './Icon.svelte';

	// Rhymes for a word (spec 5.11, writing helpers): perfect and near rhymes, grouped by
	// syllables, from the dictionary on the server. Everyday words are listed first.
	let { word = '', onclose }: { word?: string; onclose: () => void } = $props();

	let query = $state(untrack(() => word));
	let result = $state<Rhymes | null>(null);
	let error = $state('');
	let loading = $state(false);
	let input: HTMLInputElement | undefined = $state();

	$effect(() => {
		const q = query.trim();
		result = null;
		error = '';
		if (!q) return;
		loading = true;
		const timer = setTimeout(() => {
			findRhymes(q)
				.then((r) => {
					if (query.trim() === q) result = r;
				})
				.catch((e) => {
					if (query.trim() !== q) return;
					error =
						e instanceof ApiError && e.status === 0
							? 'Rhymes need the home network.'
							: e instanceof ApiError
								? e.message
								: 'Could not find rhymes.';
				})
				.finally(() => {
					if (query.trim() === q) loading = false;
				});
		}, 250);
		return () => clearTimeout(timer);
	});

	$effect(() => {
		if (!untrack(() => word)) input?.focus();
	});

	const syllableLabel = (n: number) => plural(n, 'syllable');
	const count = (groups: RhymeGroup[]) => groups.reduce((n, g) => n + g.words.length + g.more, 0);
</script>

<svelte:window onkeydown={(e) => e.key === 'Escape' && onclose()} />

<div class="overlay" role="presentation" onclick={(e) => e.target === e.currentTarget && onclose()}>
	<div class="dialog" role="dialog" aria-modal="true" aria-labelledby="rhymes-title">
		<div class="bar">
			<h2 id="rhymes-title">Rhymes</h2>
			<button class="btn btn-ghost btn-sm btn-icon" aria-label="Close" onclick={onclose}><Icon name="x" /></button>
		</div>
		<label class="search">
			<Icon name="search" size={16} />
			<input bind:this={input} type="search" bind:value={query} placeholder="A word" aria-label="Find rhymes for" autocapitalize="off" />
		</label>

		<div class="results" aria-live="polite">
			{#if !query.trim()}
				<p class="muted">Type a word to see what rhymes with it.</p>
			{:else if error}
				<p class="muted">{error}</p>
			{:else if loading || !result}
				<div class="loading" aria-busy="true"></div>
			{:else if !result.known}
				<p class="muted">“{result.word}” isn’t in the dictionary, so there are no rhymes to show.</p>
			{:else}
				<p class="about">
					<strong>{result.word}</strong> · {syllableLabel(result.syllables ?? 1)}
				</p>
				{#each [{ title: 'Perfect rhymes', groups: result.perfect }, { title: 'Near rhymes', groups: result.near }] as kind (kind.title)}
					<section>
						<h3>{kind.title} <span class="n">{count(kind.groups)}</span></h3>
						{#if kind.groups.length === 0}
							<p class="muted small">None.</p>
						{/if}
						{#each kind.groups as g (g.syllables)}
							<div class="group">
								<span class="syl">{syllableLabel(g.syllables)}</span>
								<ul class="words">
									{#each g.words as w (w)}<li>{w}</li>{/each}
									{#if g.more}<li class="more">and {g.more} more</li>{/if}
								</ul>
							</div>
						{/each}
					</section>
				{/each}
				<p class="muted small">Everyday words come first; the dictionary also has many names.</p>
			{/if}
		</div>
	</div>
</div>

<style>
	.overlay {
		position: fixed;
		inset: 0;
		z-index: 45;
		display: grid;
		place-items: center;
		padding: 1rem;
		background: rgb(0 0 0 / 0.65);
	}
	.dialog {
		display: grid;
		grid-template-rows: auto auto 1fr;
		gap: 0.7rem;
		width: min(100%, 560px);
		height: min(640px, calc(100dvh - 2rem));
		padding: 0.9rem 1.1rem 1rem;
		border-radius: var(--radius);
		background: var(--surface);
		border: 1px solid var(--border);
	}
	.bar {
		display: flex;
		align-items: center;
		justify-content: space-between;
	}
	h2 {
		font-size: 1.15rem;
	}
	.search {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		padding: 0 0.7rem;
		border: 1px solid var(--border);
		border-radius: var(--radius-sm);
		color: var(--text-faint);
	}
	.search input {
		flex: 1;
		min-width: 0;
		height: 40px;
		border: 0;
		background: transparent;
		color: var(--text);
		font: inherit;
		font-size: 1rem;
		outline: none;
	}
	.search:focus-within {
		border-color: rgb(240 166 70 / 0.55);
	}
	.results {
		min-width: 0;
		min-height: 0;
		overflow-y: auto;
	}
	.results p {
		margin: 0;
	}
	.about {
		margin-bottom: 0.6rem !important;
		color: var(--text-muted);
	}
	.about strong {
		color: var(--text);
		font-size: 1.05rem;
	}
	section {
		margin-bottom: 1rem;
	}
	h3 {
		margin-bottom: 0.45rem;
		font-size: 0.8rem;
		font-weight: 700;
		letter-spacing: 0.05em;
		text-transform: uppercase;
		color: var(--text-muted);
	}
	.n {
		color: var(--text-faint);
		font-weight: 600;
	}
	.group {
		display: grid;
		gap: 0.15rem;
		margin-bottom: 0.6rem;
	}
	.syl {
		color: var(--text-faint);
		font-size: 0.75rem;
		font-weight: 650;
	}
	.words {
		display: flex;
		flex-wrap: wrap;
		gap: 0.1rem 0.85rem;
		margin: 0;
		padding: 0;
		list-style: none;
		line-height: 1.6;
		font-size: 0.98rem;
	}
	.more {
		color: var(--text-faint);
	}
	.small {
		font-size: 0.82rem;
	}
	.loading {
		height: 120px;
	}
</style>
