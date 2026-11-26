<script lang="ts">
	import { ApiError, api } from '#lib/api.ts';
	import ClipPlayer from '#lib/components/ClipPlayer.svelte';
	import ClipTile from '#lib/components/ClipTile.svelte';
	import Icon from '#lib/components/Icon.svelte';
	import { refreshCounts } from '#lib/counts.svelte.ts';
	import { dateTime, duration, plural, shortDate } from '#lib/format.ts';
	import { notebook } from '#lib/notebook.svelte.ts';
	import type { ClipDetail, Trash, TrashClip, TrashNote, TrashProject } from '#lib/types.ts';

	// Trash (spec 3.1, 3.2): restore deleted songs, recordings and notes (5.11) within
	// 30 days. After that the server deletes them for good.

	let trash = $state<Trash | null>(null);
	let error = $state('');
	let actionError = $state('');
	let busy = $state<string | null>(null);
	let restored = $state<{ name: string; href: string } | null>(null);
	let preview = $state<TrashClip | null>(null);
	let previewDetail = $state<ClipDetail | null>(null);

	async function load() {
		try {
			trash = await api.get<Trash>('/trash');
			error = '';
		} catch (e) {
			error = e instanceof ApiError ? e.message : 'Could not load Trash.';
		}
	}

	$effect(() => {
		load();
	});

	$effect(() => {
		const clip = preview;
		previewDetail = null;
		if (!clip) return;
		api.get<ClipDetail>(`/clips/${clip.id}`)
			.then((d) => {
				if (preview?.id === clip.id) previewDetail = d;
			})
			.catch(() => {});
	});

	const empty = $derived(
		!!trash && trash.projects.length === 0 && trash.clips.length === 0 && trash.notes.length === 0
	);

	function goneIn(purgeAt: string): string {
		const days = Math.ceil((new Date(purgeAt).getTime() - Date.now()) / 86_400_000);
		if (days <= 0) return 'Deleted for good today';
		if (days === 1) return 'Deleted for good tomorrow';
		return `Deleted for good in ${days} days`;
	}

	function clipName(c: TrashClip): string {
		return c.title || `${c.media_type === 'audio' ? 'Audio' : 'Video'} clip, ${shortDate(c.recorded_at ?? c.uploaded_at)}`;
	}

	async function restoreProject(p: TrashProject) {
		busy = `p${p.id}`;
		actionError = '';
		try {
			await api.post(`/projects/${p.id}/restore`);
			restored = { name: p.name, href: `/projects/${p.id}` };
			await load();
			refreshCounts();
		} catch (e) {
			actionError = e instanceof ApiError ? e.message : 'Could not restore.';
		} finally {
			busy = null;
		}
	}

	async function restoreNote(n: TrashNote) {
		busy = `n${n.uid}`;
		actionError = '';
		try {
			await api.post(`/notes/${encodeURIComponent(n.uid)}/restore`);
			restored = { name: 'the note', href: '/notebook' };
			await load();
			refreshCounts();
			void notebook.sync(); // so this device has it back
		} catch (e) {
			actionError = e instanceof ApiError ? e.message : 'Could not restore.';
		} finally {
			busy = null;
		}
	}

	async function restoreClip(c: TrashClip) {
		busy = `c${c.id}`;
		actionError = '';
		try {
			await api.post(`/clips/${c.id}/restore`);
			restored = {
				name: clipName(c),
				href: c.project_id !== null ? `/projects/${c.project_id}` : '/uncategorized'
			};
			if (preview?.id === c.id) preview = null;
			await load();
			refreshCounts();
		} catch (e) {
			actionError = e instanceof ApiError ? e.message : 'Could not restore.';
		} finally {
			busy = null;
		}
	}
</script>

<svelte:window onkeydown={(e) => e.key === 'Escape' && (preview = null)} />

<div class="page">
	<a href="/" class="back"><Icon name="arrow-left" size={18} /> Projects</a>

	<div class="title-row">
		<h1>Trash</h1>
		<p class="muted">
			Restore anything here within {trash?.days ?? 30} days. After that it's deleted for good, original files included.
		</p>
	</div>

	{#if restored}
		<div class="restored" role="status">
			<Icon name="check" size={18} />
			<span>Restored <a href={restored.href}>{restored.name}</a></span>
		</div>
	{/if}
	{#if actionError}<p class="error banner" role="alert">{actionError}</p>{/if}

	{#if error}
		<p class="error" role="alert">{error}</p>
	{:else if !trash}
		<div class="loading" aria-busy="true"></div>
	{:else if empty}
		<div class="empty">
			<div class="empty-icon"><Icon name="trash" size={26} /></div>
			<h2>Trash is empty</h2>
			<p class="muted">Songs, recordings and notes you delete wait here for {trash.days} days before they're gone.</p>
		</div>
	{:else}
		{#if trash.projects.length > 0}
			<section aria-labelledby="songs-title">
				<h2 id="songs-title">Songs</h2>
				<ul class="songs">
					{#each trash.projects as p (p.id)}
						<li class="song">
							<div class="cover">
								{#if p.cover_thumbnail_url}
									<img src={p.cover_thumbnail_url} alt="" loading="lazy" />
								{:else}
									<Icon name="music" size={22} stroke={1.6} />
								{/if}
							</div>
							<div class="song-body">
								<span class="song-name">{p.name}</span>
								<span class="meta">
									{plural(p.clip_count, 'recording')} · moved to Trash {shortDate(p.deleted_at)}
								</span>
								<span class="gone">{goneIn(p.purge_at)}</span>
							</div>
							<button class="btn btn-sm" disabled={busy !== null} onclick={() => restoreProject(p)}>
								<Icon name="undo" size={16} /> Restore
							</button>
						</li>
					{/each}
				</ul>
			</section>
		{/if}

		{#if trash.clips.length > 0}
			<section aria-labelledby="clips-title">
				<h2 id="clips-title">Recordings</h2>
				<ul class="grid">
					{#each trash.clips as c (c.id)}
						<li>
							<ClipTile clip={c} onplay={() => (preview = c)} />
							<span class="where" title={c.project_name ?? 'Uncategorized'}>{c.project_name ?? 'Uncategorized'}</span>
							<span class="gone">{goneIn(c.purge_at)}</span>
							{#if c.project_in_trash}
								<span class="hint">Restore “{c.project_name}” first</span>
							{:else}
								<button class="btn btn-sm restore" disabled={busy !== null} onclick={() => restoreClip(c)}>
									<Icon name="undo" size={16} /> Restore
								</button>
							{/if}
						</li>
					{/each}
				</ul>
			</section>
		{/if}

		{#if trash.notes.length > 0}
			<section aria-labelledby="notes-title">
				<h2 id="notes-title">Notes</h2>
				<ul class="songs">
					{#each trash.notes as n (n.uid)}
						<li class="song">
							<div class="song-body">
								<span class="note-text">{n.body || '(empty)'}</span>
								<span class="meta">From the Notebook · moved to Trash {shortDate(n.deleted_at)}</span>
								<span class="gone">{goneIn(n.purge_at)}</span>
							</div>
							<button class="btn btn-sm" disabled={busy !== null} onclick={() => restoreNote(n)}>
								<Icon name="undo" size={16} /> Restore
							</button>
						</li>
					{/each}
				</ul>
			</section>
		{/if}
	{/if}
</div>

{#if preview}
	<div class="overlay" role="presentation" onclick={(e) => e.target === e.currentTarget && (preview = null)}>
		<div class="dialog" role="dialog" aria-modal="true" aria-label="Preview {clipName(preview)}">
			<div class="dialog-bar">
				<span class="dialog-title">{clipName(preview)}</span>
				<button class="btn btn-ghost btn-icon" aria-label="Close" onclick={() => (preview = null)}><Icon name="x" /></button>
			</div>
			<div class="stage">
				{#if !previewDetail}
					<div class="waiting"><span class="spinner" aria-hidden="true"></span></div>
				{:else if previewDetail.media_url}
					<ClipPlayer
						src={previewDetail.media_url}
						kind={previewDetail.media_type}
						poster={previewDetail.thumbnail_url}
						start={previewDetail.start_seconds}
						end={previewDetail.end_seconds}
						autoplay
					/>
				{:else}
					<div class="waiting"><p>No playable copy.</p></div>
				{/if}
			</div>
			<div class="dialog-foot">
				<span class="muted small">
					{#if previewDetail}
						{dateTime(previewDetail.recorded_at ?? previewDetail.uploaded_at)}
						{#if previewDetail.duration_seconds}· {duration(previewDetail.duration_seconds)}{/if}
						· {previewDetail.original_filename}
					{/if}
				</span>
				{#if !preview.project_in_trash}
					<button class="btn btn-primary btn-sm" disabled={busy !== null} onclick={() => preview && restoreClip(preview)}>
						<Icon name="undo" size={16} /> Restore
					</button>
				{/if}
			</div>
		</div>
	</div>
{/if}

<style>
	.page {
		max-width: 1180px;
		margin: 0 auto;
		padding: 0.9rem var(--gutter) 2rem;
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
	.back:hover {
		color: var(--text);
	}
	.title-row {
		margin-bottom: 1.1rem;
	}
	h1 {
		font-size: 1.6rem;
	}
	.title-row p {
		margin: 0.3rem 0 0;
		font-size: 0.93rem;
		max-width: 60ch;
	}
	section {
		margin-bottom: 1.75rem;
	}
	section h2 {
		margin-bottom: 0.7rem;
		font-size: 1rem;
		color: var(--text-muted);
	}
	.loading {
		height: 40vh;
	}
	.banner {
		margin-bottom: 0.9rem;
	}
	.restored {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		margin-bottom: 0.9rem;
		padding: 0.55rem 0.8rem;
		border-radius: var(--radius-sm);
		background: rgb(92 201 138 / 0.12);
		color: #86e0ab;
		font-size: 0.93rem;
	}
	.restored a {
		color: var(--text);
		font-weight: 600;
		text-decoration: underline;
	}

	.songs {
		display: grid;
		gap: 0.5rem;
		margin: 0;
		padding: 0;
		list-style: none;
		max-width: 760px;
	}
	.song {
		display: flex;
		align-items: center;
		gap: 0.8rem;
		padding: 0.6rem 0.75rem;
		border-radius: var(--radius);
		background: var(--surface);
		border: 1px solid var(--border);
	}
	.cover {
		display: grid;
		place-items: center;
		flex-shrink: 0;
		width: 72px;
		aspect-ratio: 16 / 9;
		overflow: hidden;
		border-radius: 6px;
		background: var(--surface-2);
		color: var(--text-faint);
	}
	.cover img {
		width: 100%;
		height: 100%;
		object-fit: cover;
	}
	.song-body {
		display: grid;
		flex: 1;
		min-width: 0;
		gap: 0.1rem;
	}
	.song-name {
		font-weight: 650;
		overflow-wrap: anywhere;
	}
	.meta {
		color: var(--text-muted);
		font-size: 0.85rem;
	}
	.note-text {
		display: -webkit-box;
		-webkit-box-orient: vertical;
		-webkit-line-clamp: 3;
		line-clamp: 3;
		overflow: hidden;
		white-space: pre-wrap;
		overflow-wrap: anywhere;
	}
	.gone {
		color: var(--text-faint);
		font-size: 0.78rem;
	}

	.grid {
		display: flex;
		flex-wrap: wrap;
		gap: 1rem 0.6rem;
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.grid li {
		display: grid;
		align-content: start;
		gap: 0.25rem;
		width: var(--tile-w);
	}
	.where {
		font-size: 0.8rem;
		font-weight: 600;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.hint {
		color: var(--text-faint);
		font-size: 0.78rem;
	}
	.restore {
		justify-self: start;
		margin-top: 0.15rem;
	}

	.empty {
		display: grid;
		justify-items: center;
		gap: 0.55rem;
		padding: 3rem 1rem;
		text-align: center;
		border: 1px dashed var(--border);
		border-radius: var(--radius);
	}
	.empty h2 {
		font-size: 1.1rem;
	}
	.empty p {
		margin: 0;
		max-width: 40ch;
	}
	.empty-icon {
		display: grid;
		place-items: center;
		width: 52px;
		height: 52px;
		border-radius: 15px;
		background: var(--surface-2);
		color: var(--text-muted);
	}

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
		width: min(100%, 720px);
		max-height: calc(100dvh - 2rem);
		overflow: hidden;
		border-radius: var(--radius);
		background: var(--surface);
		border: 1px solid var(--border);
	}
	.dialog-bar,
	.dialog-foot {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
		padding: 0.4rem 0.5rem 0.4rem 0.9rem;
	}
	.dialog-foot {
		padding: 0.7rem 0.9rem;
	}
	.dialog-title {
		font-weight: 650;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.small {
		font-size: 0.85rem;
		overflow-wrap: anywhere;
	}
	.stage {
		display: grid;
		place-items: center;
		min-height: 200px;
		background: #000;
	}
	.waiting {
		display: grid;
		justify-items: center;
		padding: 2.5rem 1rem;
		color: var(--text-muted);
	}
	.waiting p {
		margin: 0;
	}
	.spinner {
		width: 28px;
		height: 28px;
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
</style>
