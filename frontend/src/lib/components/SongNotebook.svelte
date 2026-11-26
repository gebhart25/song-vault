<script lang="ts">
	import { plural } from '#lib/format.ts';
	import { notebook } from '#lib/notebook.svelte.ts';
	import { noteDate, segmentsOf } from '#lib/notebook-text.ts';
	import Icon from './Icon.svelte';

	// The song page's Notebook panel (spec 5.11, step 2): the notes linked to this song,
	// next to Lyrics & chords. Shown only when there are some.
	let { projectId }: { projectId: number } = $props();

	const OPEN_KEY = 'songvault.songNotebookOpen';
	let open = $state(localStorage.getItem(OPEN_KEY) === '1');
	$effect(() => {
		localStorage.setItem(OPEN_KEY, open ? '1' : '0');
	});

	const notes = $derived(
		notebook.notes
			.filter((n) => !n.trash && n.songs.some((s) => s.id === projectId))
			.sort((a, b) => b.created_at.localeCompare(a.created_at))
	);
</script>

{#if notes.length > 0}
	<button class="btn btn-sm" aria-expanded={open} aria-controls="song-notebook" onclick={() => (open = !open)}>
		<Icon name="notes" size={16} /> Notebook <span class="muted">· {plural(notes.length, 'note')}</span>
		<span class="chevron" class:up={open}><Icon name="chevron-down" size={15} /></span>
	</button>
	{#if open}
		<section id="song-notebook" class="panel" aria-label="Notes from the Notebook">
			<ul>
				{#each notes as n (n.uid)}
					<li>
						<p class="text">{#each segmentsOf(n.body) as s, i (i)}{#if s.tag}<span class="tag">{s.text}</span>{:else}{s.text}{/if}{/each}</p>
						<div class="foot">
							<span>{noteDate(n.created_at)}</span>
							{#if n.status === 'used'}<span class="used">Used</span>{/if}
							<button
								class="unlink"
								onclick={() => notebook.update(n.uid, { songs: n.songs.filter((s) => s.id !== projectId) })}
							>
								Remove from this song
							</button>
						</div>
					</li>
				{/each}
			</ul>
			<a class="open" href="/notebook">Open the Notebook <Icon name="chevron-right" size={15} /></a>
		</section>
	{/if}
{/if}

<style>
	.chevron {
		display: inline-grid;
		transition: rotate 0.15s;
	}
	.chevron.up {
		rotate: 180deg;
	}
	.panel {
		flex-basis: 100%;
		display: grid;
		gap: 0.5rem;
		margin-top: 0.2rem;
		padding: 0.6rem;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--surface);
	}
	ul {
		display: grid;
		gap: 0.45rem;
		max-height: 22rem;
		overflow-y: auto;
		margin: 0;
		padding: 0;
		list-style: none;
	}
	li {
		padding: 0.55rem 0.7rem 0.35rem;
		border-radius: var(--radius-sm);
		background: var(--surface-2);
	}
	.text {
		display: -webkit-box;
		-webkit-box-orient: vertical;
		-webkit-line-clamp: 6;
		line-clamp: 6;
		overflow: hidden;
		margin: 0;
		white-space: pre-wrap;
		overflow-wrap: anywhere;
		line-height: 1.45;
		font-size: 0.95rem;
	}
	.tag {
		color: var(--accent-strong);
		font-weight: 600;
	}
	.foot {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		margin-top: 0.3rem;
		color: var(--text-faint);
		font-size: 0.78rem;
	}
	.used {
		padding: 0.05rem 0.45rem;
		border-radius: 999px;
		background: rgb(92 201 138 / 0.14);
		color: #86e0ab;
		font-weight: 650;
	}
	.unlink {
		margin-left: auto;
		padding: 0.2rem 0;
		border: 0;
		background: none;
		color: var(--text-muted);
		font-size: 0.78rem;
		cursor: pointer;
	}
	.unlink:hover {
		color: var(--text);
	}
	.open {
		display: inline-flex;
		align-items: center;
		gap: 0.2rem;
		justify-self: start;
		color: var(--accent-strong);
		font-size: 0.88rem;
		font-weight: 650;
	}
</style>
