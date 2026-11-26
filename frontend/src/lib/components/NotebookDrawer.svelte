<script lang="ts">
	import { plural } from '#lib/format.ts';
	import { notebook, type LocalNote } from '#lib/notebook.svelte.ts';
	import { foldText, lyricText, searchWords, segmentsOf } from '#lib/notebook-text.ts';
	import Icon from './Icon.svelte';

	// The Notebook drawer on the Lyrics page's Write tab (spec 5.11, step 2): find a line in
	// the Notebook and put it in at the cursor, or as a new section, without leaving the page.
	let {
		projectId,
		canInsert,
		oninsert,
		onclose
	}: {
		projectId: number;
		/** A lyrics section has had the cursor in it, so "At cursor" has somewhere to go */
		canInsert: boolean;
		oninsert: (note: LocalNote, text: string, where: 'cursor' | 'section') => void;
		onclose: () => void;
	} = $props();

	const HIDE_USED_KEY = 'songvault.hideUsed';

	let tab = $state<'song' | 'all'>('song');
	let query = $state('');
	let hideUsed = $state(localStorage.getItem(HIDE_USED_KEY) === '1');
	$effect(() => {
		localStorage.setItem(HIDE_USED_KEY, hideUsed ? '1' : '0');
	});

	const live = $derived(
		notebook.notes
			.filter((n) => !n.trash)
			.sort(
				(a, b) =>
					Number(b.starred) - Number(a.starred) || b.created_at.localeCompare(a.created_at) || a.uid.localeCompare(b.uid)
			)
	);
	const songNotes = $derived(live.filter((n) => n.songs.some((s) => s.id === projectId)));
	const words = $derived(searchWords(query));
	const allNotes = $derived(
		live.filter((n) => (!hideUsed || n.status !== 'used') && words.every((w) => foldText(n.body).includes(w)))
	);
	const shown = $derived((tab === 'song' ? songNotes : allNotes).slice(0, 100));

	// Open on All notes when the song has none of its own yet.
	let chosenTab = false;
	$effect(() => {
		if (!chosenTab && notebook.loaded) {
			chosenTab = true;
			if (songNotes.length === 0) tab = 'all';
		}
	});
</script>

<svelte:window onkeydown={(e) => e.key === 'Escape' && onclose()} />

<aside class="drawer no-print" aria-label="Notebook">
	<div class="head">
		<h2><Icon name="notes" size={18} /> Notebook</h2>
		<button class="btn btn-ghost btn-sm btn-icon" aria-label="Close the Notebook" onclick={onclose}><Icon name="x" /></button>
	</div>
	<div class="tabs" role="group" aria-label="Which notes">
		<button aria-pressed={tab === 'song'} onclick={() => (tab = 'song')}>This song <span class="n">{songNotes.length}</span></button>
		<button aria-pressed={tab === 'all'} onclick={() => (tab = 'all')}>All notes</button>
	</div>
	{#if tab === 'all'}
		<div class="filters">
			<label class="search">
				<Icon name="search" size={16} />
				<input type="search" bind:value={query} placeholder="Search notes" aria-label="Search notes" />
			</label>
			<button class="chip" aria-pressed={hideUsed} onclick={() => (hideUsed = !hideUsed)}>Hide used</button>
		</div>
	{/if}
	{#if !canInsert}<p class="hint">Tap in a section first to put a note in at the cursor.</p>{/if}

	<ul class="list">
		{#each shown as note (note.uid)}
			{@const text = lyricText(note.body)}
			<li>
				<p class="text">{#each segmentsOf(note.body) as s, i (i)}{#if s.tag}<span class="tag">{s.text}</span>{:else}{s.text}{/if}{/each}</p>
				{#if note.status === 'used'}<span class="used">Used</span>{/if}
				<div class="actions">
					<button class="btn btn-sm" disabled={!canInsert || !text} onclick={() => oninsert(note, text, 'cursor')}>At cursor</button>
					<button class="btn btn-sm" disabled={!text} onclick={() => oninsert(note, text, 'section')}>New section</button>
				</div>
			</li>
		{:else}
			<li class="none">
				{#if tab === 'song'}
					No notes in this song yet. Find one under All notes, or use “Use in a song…” in the Notebook.
				{:else if live.length === 0}
					The Notebook is empty.
				{:else}
					No notes match.
				{/if}
			</li>
		{/each}
	</ul>
	{#if (tab === 'song' ? songNotes : allNotes).length > shown.length}
		<p class="more">Showing the first {plural(shown.length, 'note')}. Search to find others.</p>
	{/if}
</aside>

<style>
	.drawer {
		position: fixed;
		z-index: 35;
		display: flex;
		flex-direction: column;
		gap: 0.55rem;
		padding: 0.7rem 0.8rem calc(0.8rem + env(safe-area-inset-bottom));
		background: var(--surface);
		border: 1px solid var(--border);
		box-shadow: 0 16px 40px rgb(0 0 0 / 0.5);
		/* Phones: a sheet over the bottom part of the page */
		inset: auto 0 0 0;
		max-height: 62dvh;
		border-radius: 16px 16px 0 0;
		border-bottom: 0;
	}
	@media (min-width: 900px) {
		.drawer {
			inset: calc(var(--header-h) + 0.75rem) 0.75rem 0.75rem auto;
			width: 340px;
			max-height: none;
			border-radius: var(--radius);
			border-bottom: 1px solid var(--border);
		}
	}
	.head {
		display: flex;
		align-items: center;
		justify-content: space-between;
	}
	h2 {
		display: flex;
		align-items: center;
		gap: 0.45rem;
		font-size: 1.05rem;
	}
	.tabs {
		display: flex;
		gap: 0.25rem;
		padding: 0.2rem;
		border-radius: var(--radius-sm);
		background: var(--surface-2);
	}
	.tabs button {
		flex: 1;
		padding: 0.35rem 0.5rem;
		border: 0;
		border-radius: 6px;
		background: transparent;
		color: var(--text-muted);
		font-weight: 600;
		font-size: 0.88rem;
		cursor: pointer;
	}
	.tabs button[aria-pressed='true'] {
		background: var(--surface-3);
		color: var(--text);
	}
	.n {
		color: var(--text-faint);
		font-weight: 500;
	}
	.filters {
		display: flex;
		gap: 0.4rem;
	}
	.search {
		display: flex;
		align-items: center;
		flex: 1;
		gap: 0.4rem;
		padding: 0 0.6rem;
		border: 1px solid var(--border);
		border-radius: var(--radius-sm);
		color: var(--text-faint);
	}
	.search input {
		flex: 1;
		min-width: 0;
		height: 34px;
		border: 0;
		background: transparent;
		color: var(--text);
		font: inherit;
		outline: none;
	}
	.chip {
		flex-shrink: 0;
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
	.hint,
	.more {
		margin: 0;
		color: var(--text-faint);
		font-size: 0.8rem;
	}
	.list {
		display: grid;
		align-content: start;
		gap: 0.45rem;
		flex: 1;
		min-height: 0;
		overflow-y: auto;
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.list li {
		display: grid;
		gap: 0.35rem;
		padding: 0.55rem 0.65rem;
		border-radius: var(--radius-sm);
		background: var(--surface-2);
	}
	.list li.none {
		color: var(--text-muted);
		font-size: 0.88rem;
		background: none;
	}
	.text {
		display: -webkit-box;
		-webkit-box-orient: vertical;
		-webkit-line-clamp: 5;
		line-clamp: 5;
		overflow: hidden;
		margin: 0;
		white-space: pre-wrap;
		overflow-wrap: anywhere;
		font-size: 0.92rem;
		line-height: 1.45;
	}
	.tag {
		color: var(--accent-strong);
		font-weight: 600;
	}
	.used {
		justify-self: start;
		padding: 0.05rem 0.45rem;
		border-radius: 999px;
		background: rgb(92 201 138 / 0.14);
		color: #86e0ab;
		font-size: 0.72rem;
		font-weight: 650;
	}
	.actions {
		display: flex;
		gap: 0.35rem;
	}
</style>
