<script lang="ts" module>
	export type SongDialogMode = 'use' | 'lyrics' | 'start';
</script>

<script lang="ts">
	import { untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { ApiError, api } from '#lib/api.ts';
	import { plural } from '#lib/format.ts';
	import { notebook, type LocalNote, type NoteSong } from '#lib/notebook.svelte.ts';
	import { firstLine, foldText, lyricText } from '#lib/notebook-text.ts';
	import type { LyricSection, Lyrics, ProjectDetail, ProjectSummary } from '#lib/types.ts';
	import Icon from './Icon.svelte';

	// A note into songs (spec 5.11, step 2): link it to songs, add it to a song's lyrics,
	// or start a new song from it. The lyrics are changed with the lyrics page's own
	// save (and its check for edits made elsewhere); then the note is linked and marked Used.
	let {
		note,
		mode,
		onclose,
		ondone
	}: {
		note: LocalNote;
		mode: SongDialogMode;
		onclose: () => void;
		/** Finished: a message for the toast, and where "Open" goes */
		ondone: (message: string, href?: string) => void;
	} = $props();

	const TITLES: Record<SongDialogMode, string> = {
		use: 'Use in a song',
		lyrics: 'Add to lyrics',
		start: 'Start a song from this'
	};

	let projects = $state<ProjectSummary[] | null>(null);
	let loadError = $state('');
	let error = $state('');
	let busy = $state(false);
	let query = $state('');

	// The dialog works on the note as it was when it opened.
	const opened = untrack(() => {
		const live = note.songs.filter((s) => !s.in_trash);
		return {
			songs: note.songs.map((s) => s.id),
			words: lyricText(note.body),
			name: firstLine(note.body) || 'New song',
			// Add to lyrics starts with the note's song, if it's in just one
			song: mode === 'lyrics' && live.length === 1 ? live[0].id : null
		};
	});
	const words = opened.words;

	// Use in a song: which songs (all of them at once)
	let chosen = $state<number[]>(opened.songs);
	// Add to lyrics: the song, its sections, and where the words go
	let songId = $state<number | null>(opened.song);
	let lyrics = $state<Lyrics | null>(null);
	let place = $state<string>('new');
	let label = $state('Idea');
	// Start a song
	let name = $state(opened.name);

	$effect(() => {
		if (mode === 'start') return;
		api.get<ProjectSummary[]>('/projects?include_archived=true')
			.then((p) => (projects = p))
			.catch((e) => (loadError = e instanceof ApiError ? e.message : 'Could not load the songs.'));
	});

	$effect(() => {
		const id = songId;
		lyrics = null;
		place = 'new';
		if (id === null) return;
		api.get<Lyrics>(`/projects/${id}/lyrics`)
			.then((l) => {
				if (songId === id) lyrics = l;
			})
			.catch(() => (error = 'Could not load that song’s lyrics.'));
	});

	const shown = $derived.by(() => {
		const q = foldText(query.trim());
		const list = projects ?? [];
		return q ? list.filter((p) => foldText(p.name).includes(q)) : list;
	});
	const song = $derived(projects?.find((p) => p.id === songId) ?? null);
	const lyricSections = $derived(lyrics?.sections.filter((s) => s.kind === 'lyrics') ?? []);

	function toggle(id: number) {
		chosen = chosen.includes(id) ? chosen.filter((x) => x !== id) : [...chosen, id];
	}

	const newId = () => Math.random().toString(36).slice(2, 10);

	function saveSongs() {
		const byId = new Map((projects ?? []).map((p) => [p.id, p.name]));
		const songs: NoteSong[] = chosen.map(
			(id) => note.songs.find((s) => s.id === id) ?? { id, name: byId.get(id) ?? 'Song', in_trash: false }
		);
		notebook.update(note.uid, { songs });
		ondone(songs.length ? `In ${plural(songs.length, 'song')}` : 'Not in any song now');
		onclose();
	}

	/** The song's lyrics with the note's words added where asked. */
	function withWords(sections: LyricSection[]): LyricSection[] {
		const target = place === 'new' ? null : sections.find((s) => s.id === place);
		if (!target) return [...sections, { id: newId(), label: label.trim() || 'Idea', kind: 'lyrics', text: words }];
		return sections.map((s) =>
			s.id === target.id ? { ...s, text: s.text.trim() ? `${s.text.replace(/\n+$/, '')}\n${words}` : words } : s
		);
	}

	async function addToLyrics() {
		if (!song || busy) return;
		busy = true;
		error = '';
		try {
			// Start from the latest version; if it changes on the way, try once more.
			for (let attempt = 0; ; attempt++) {
				const latest = await api.get<Lyrics>(`/projects/${song.id}/lyrics`);
				try {
					await api.put<Lyrics>(`/projects/${song.id}/lyrics`, { sections: withWords(latest.sections), revision: latest.revision });
					break;
				} catch (e) {
					if (!(e instanceof ApiError && e.status === 409) || attempt > 0) throw e;
				}
			}
			notebook.usedIn(note.uid, { id: song.id, name: song.name, in_trash: false });
			ondone(`Added to the lyrics of ${song.name}`, `/projects/${song.id}/lyrics`);
			onclose();
		} catch (e) {
			error = e instanceof ApiError ? e.message : 'Could not add it to the lyrics.';
		} finally {
			busy = false;
		}
	}

	async function startSong() {
		if (busy || !name.trim()) return;
		busy = true;
		error = '';
		try {
			const created = await api.post<ProjectDetail>('/projects', { name: name.trim() });
			if (words) {
				await api.put<Lyrics>(`/projects/${created.id}/lyrics`, {
					sections: [{ id: newId(), label: label.trim() || 'Idea', kind: 'lyrics', text: words }],
					revision: 0
				});
			}
			notebook.usedIn(note.uid, { id: created.id, name: created.name, in_trash: false });
			onclose();
			await goto(`/projects/${created.id}/lyrics`);
		} catch (e) {
			error = e instanceof ApiError ? e.message : 'Could not start the song.';
		} finally {
			busy = false;
		}
	}
</script>

<svelte:window onkeydown={(e) => e.key === 'Escape' && onclose()} />

<div class="overlay" role="presentation" onclick={(e) => e.target === e.currentTarget && onclose()}>
	<div class="dialog" role="dialog" aria-modal="true" aria-labelledby="song-dialog-title">
		<div class="bar">
			<h2 id="song-dialog-title">{TITLES[mode]}</h2>
			<button class="btn btn-ghost btn-sm btn-icon" aria-label="Close" onclick={onclose}><Icon name="x" /></button>
		</div>

		{#if mode !== 'use'}
			<div class="preview" aria-label="The words that go in">
				{#if words}{words}{:else}<span class="muted">This note is only tags: there are no words to add.</span>{/if}
			</div>
		{/if}

		{#if mode === 'start'}
			<label class="field-label">
				<span>Song name</span>
				<input class="field" bind:value={name} maxlength="200" />
			</label>
			{#if words}
				<label class="field-label">
					<span>First section</span>
					<input class="field" bind:value={label} maxlength="60" placeholder="Idea" list="note-section-labels" />
				</label>
			{/if}
		{:else if mode === 'lyrics' && song}
			<div class="chosen">
				<Icon name="music" size={16} />
				<strong>{song.name}</strong>
				<button class="btn btn-ghost btn-sm" onclick={() => (songId = null)}>Change</button>
			</div>
			{#if !lyrics}
				<div class="loading" aria-busy="true"></div>
			{:else}
				<fieldset class="places">
					<legend>Where it goes</legend>
					<label class="place">
						<input type="radio" name="place" value="new" bind:group={place} />
						<span>A new section</span>
						{#if place === 'new'}
							<input class="field label-input" bind:value={label} maxlength="60" placeholder="Idea" aria-label="Section label" list="note-section-labels" />
						{/if}
					</label>
					{#each lyricSections as s (s.id)}
						<label class="place">
							<input type="radio" name="place" value={s.id} bind:group={place} />
							<span>At the end of <strong>{s.label || 'Untitled section'}</strong></span>
						</label>
					{/each}
				</fieldset>
			{/if}
		{:else}
			<label class="search">
				<Icon name="search" size={16} />
				<input type="search" bind:value={query} placeholder="Find a song" aria-label="Find a song" />
			</label>
			{#if loadError}
				<p class="error" role="alert">{loadError}</p>
			{:else if !projects}
				<div class="loading" aria-busy="true"></div>
			{:else if shown.length === 0}
				<p class="muted">{projects.length ? 'No songs match.' : 'No songs yet. Use “Start a song from this” instead.'}</p>
			{:else}
				<ul class="songs" role="list">
					{#each shown as p (p.id)}
						<li>
							{#if mode === 'use'}
								<label class="song">
									<input type="checkbox" checked={chosen.includes(p.id)} onchange={() => toggle(p.id)} />
									<span class="name">{p.name}</span>
									{#if p.archived}<span class="muted small">Archived</span>{/if}
								</label>
							{:else}
								<button class="song" onclick={() => (songId = p.id)}>
									<span class="name">{p.name}</span>
									{#if note.songs.some((s) => s.id === p.id)}<span class="muted small">Linked</span>{/if}
									<Icon name="chevron-right" size={16} />
								</button>
							{/if}
						</li>
					{/each}
				</ul>
			{/if}
		{/if}

		<datalist id="note-section-labels">
			{#each ['Idea', 'Intro', 'Verse 1', 'Verse 2', 'Pre-Chorus', 'Chorus', 'Bridge', 'Outro'] as o (o)}<option value={o}></option>{/each}
		</datalist>

		{#if error}<p class="error" role="alert">{error}</p>{/if}

		<div class="foot">
			<button class="btn" onclick={onclose}>Cancel</button>
			{#if mode === 'use'}
				<button class="btn btn-primary" disabled={!projects} onclick={saveSongs}>Save</button>
			{:else if mode === 'lyrics'}
				<button class="btn btn-primary" disabled={!song || !lyrics || !words || busy} onclick={addToLyrics}>
					{busy ? 'Adding…' : 'Add'}
				</button>
			{:else}
				<button class="btn btn-primary" disabled={!name.trim() || busy} onclick={startSong}>
					{busy ? 'Starting…' : 'Start the song'}
				</button>
			{/if}
		</div>
	</div>
</div>

<style>
	.overlay {
		position: fixed;
		inset: 0;
		z-index: 40;
		display: grid;
		place-items: center;
		padding: 1rem;
		background: rgb(0 0 0 / 0.65);
	}
	.dialog {
		display: grid;
		gap: 0.75rem;
		width: min(100%, 520px);
		max-height: calc(100dvh - 2rem);
		overflow-y: auto;
		padding: 0.9rem 1.1rem 1.1rem;
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
	.preview {
		max-height: 9.5rem;
		overflow-y: auto;
		padding: 0.6rem 0.75rem;
		border-radius: var(--radius-sm);
		background: var(--surface-2);
		white-space: pre-wrap;
		overflow-wrap: anywhere;
		font-size: 0.92rem;
		line-height: 1.45;
	}
	.field-label {
		display: grid;
		gap: 0.3rem;
	}
	.field-label > span,
	legend {
		color: var(--text-muted);
		font-size: 0.8rem;
		font-weight: 650;
		letter-spacing: 0.04em;
		text-transform: uppercase;
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
		height: 38px;
		border: 0;
		background: transparent;
		color: var(--text);
		font: inherit;
		outline: none;
	}
	.songs {
		display: grid;
		max-height: 45dvh;
		overflow-y: auto;
		margin: 0;
		padding: 0;
		list-style: none;
		border: 1px solid var(--border);
		border-radius: var(--radius-sm);
	}
	.songs li + li {
		border-top: 1px solid var(--border);
	}
	.song {
		display: flex;
		align-items: center;
		gap: 0.6rem;
		width: 100%;
		padding: 0.6rem 0.75rem;
		border: 0;
		background: none;
		color: inherit;
		font: inherit;
		text-align: left;
		cursor: pointer;
	}
	.song:hover {
		background: var(--surface-2);
	}
	.name {
		flex: 1;
		min-width: 0;
		overflow-wrap: anywhere;
	}
	.small {
		font-size: 0.8rem;
	}
	.chosen {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}
	.chosen strong {
		flex: 1;
	}
	.places {
		display: grid;
		gap: 0.2rem;
		margin: 0;
		padding: 0;
		border: 0;
	}
	legend {
		margin-bottom: 0.35rem;
	}
	.place {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.55rem;
		padding: 0.4rem 0.2rem;
		cursor: pointer;
	}
	.label-input {
		flex: 1 1 140px;
		min-width: 0;
		height: 34px;
	}
	.loading {
		height: 120px;
	}
	.foot {
		display: flex;
		justify-content: flex-end;
		gap: 0.5rem;
		margin-top: 0.2rem;
	}
</style>
