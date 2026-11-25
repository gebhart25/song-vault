<script lang="ts">
	import { ApiError, api } from '#lib/api.ts';
	import ClipTile from '#lib/components/ClipTile.svelte';
	import ClipPlayer from '#lib/components/ClipPlayer.svelte';
	import Icon from '#lib/components/Icon.svelte';
	import { refreshCounts } from '#lib/counts.svelte.ts';
	import { dateTime, duration, plural } from '#lib/format.ts';
	import type { ClipDetail, InboxClip, Label, ProjectDetail, ProjectSummary } from '#lib/types.ts';
	import { uploads } from '#lib/uploads.svelte.ts';

	// Sort recordings that aren't in a song yet: play one, send it to a project,
	// and the next one comes up.

	let clips = $state<InboxClip[]>([]);
	let loaded = $state(false);
	let error = $state('');
	let currentId = $state<number | null>(null);
	let detail = $state<ClipDetail | null>(null);
	let autoplay = $state(false);

	let projects = $state<ProjectSummary[]>([]);
	let parts = $state<Label[]>([]);
	let selectedParts = $state<number[]>([]);
	let query = $state('');
	let creating = $state(false);
	let newName = $state('');
	let titleDraft = $state('');
	let busy = $state(false);
	let actionError = $state('');
	let lastMove = $state<{ clipId: number; projectId: number; projectName: string } | null>(null);

	const index = $derived(clips.findIndex((c) => c.id === currentId));
	const shown = $derived(
		query.trim()
			? projects.filter((p) => p.name.toLowerCase().includes(query.trim().toLowerCase()))
			: projects
	);

	async function loadClips() {
		try {
			clips = await api.get<InboxClip[]>('/clips?view=uncategorized');
			error = '';
			if (!clips.some((c) => c.id === currentId)) currentId = clips[0]?.id ?? null;
		} catch (e) {
			error = e instanceof ApiError ? e.message : 'Could not load the recordings.';
		} finally {
			loaded = true;
		}
	}

	async function loadProjects() {
		try {
			projects = await api.get<ProjectSummary[]>('/projects');
		} catch {
			// keep the old list
		}
	}

	$effect(() => {
		void uploads.version; // new uploads land here
		loadClips();
	});
	$effect(() => {
		loadProjects();
		api.get<Label[]>('/labels').then((l) => (parts = l)).catch(() => {});
	});

	// Thumbnails finishing show up without a reload.
	$effect(() => {
		if (!clips.some((c) => c.processing === 'pending')) return;
		const timer = setInterval(loadClips, 5000);
		return () => clearInterval(timer);
	});

	// The clip being played; keep checking while its playable copy is made.
	$effect(() => {
		const id = currentId;
		let timer: ReturnType<typeof setTimeout> | undefined;
		let stopped = false;
		detail = null;
		if (id === null) return;
		const load = async () => {
			try {
				const d = await api.get<ClipDetail>(`/clips/${id}`);
				if (stopped) return;
				detail = d;
				titleDraft = d.title;
				if (d.processing === 'pending') timer = setTimeout(load, 4000);
			} catch (e) {
				if (!stopped) actionError = e instanceof ApiError ? e.message : 'Could not load the clip.';
			}
		};
		load();
		return () => {
			stopped = true;
			clearTimeout(timer);
		};
	});

	function show(id: number, play = false) {
		autoplay = play;
		currentId = id;
		selectedParts = [];
		actionError = '';
	}

	function step(direction: -1 | 1) {
		const next = clips[index + direction];
		if (next) show(next.id);
	}

	/** Take the clip out of the list and show the one that took its place. */
	function advancePast(id: number) {
		const i = clips.findIndex((c) => c.id === id);
		clips = clips.filter((c) => c.id !== id);
		const next = clips[Math.min(i, clips.length - 1)];
		if (next) show(next.id);
		else currentId = null;
		refreshCounts();
	}

	async function moveTo(project: { id: number; name: string }) {
		if (currentId === null || busy) return;
		const clipId = currentId;
		busy = true;
		actionError = '';
		try {
			await api.patch<ClipDetail>(`/clips/${clipId}`, { project_id: project.id, label_ids: selectedParts });
			lastMove = { clipId, projectId: project.id, projectName: project.name };
			advancePast(clipId);
			loadProjects(); // the song just moved to the top (most recently updated)
		} catch (e) {
			actionError = e instanceof ApiError ? e.message : 'Could not move the clip.';
		} finally {
			busy = false;
		}
	}

	async function createAndMove(event: SubmitEvent) {
		event.preventDefault();
		if (!newName.trim() || busy) return;
		busy = true;
		actionError = '';
		let project: ProjectDetail;
		try {
			project = await api.post<ProjectDetail>('/projects', { name: newName });
		} catch (e) {
			actionError = e instanceof ApiError ? e.message : 'Could not create the project.';
			busy = false;
			return;
		}
		busy = false;
		newName = '';
		creating = false;
		await moveTo(project);
	}

	async function undo() {
		if (!lastMove) return;
		const { clipId } = lastMove;
		lastMove = null;
		try {
			await api.patch(`/clips/${clipId}`, { project_id: null, label_ids: [] });
			await loadClips();
			show(clipId);
			refreshCounts();
		} catch (e) {
			actionError = e instanceof ApiError ? e.message : 'Could not undo.';
		}
	}

	async function trash() {
		if (currentId === null) return;
		if (!confirm('Move this recording to Trash? You can restore it from Trash for 30 days.')) return;
		const clipId = currentId;
		try {
			await api.del(`/clips/${clipId}`);
			lastMove = null;
			advancePast(clipId);
		} catch (e) {
			actionError = e instanceof ApiError ? e.message : 'Could not delete.';
		}
	}

	async function saveTitle() {
		if (!detail || titleDraft.trim() === detail.title) return;
		try {
			detail = await api.patch<ClipDetail>(`/clips/${detail.id}`, { title: titleDraft });
			clips = clips.map((c) => (c.id === detail?.id ? { ...c, title: detail.title } : c));
		} catch (e) {
			actionError = e instanceof ApiError ? e.message : 'Could not save the title.';
		}
	}

	function togglePart(id: number) {
		selectedParts = selectedParts.includes(id) ? selectedParts.filter((x) => x !== id) : [...selectedParts, id];
	}
</script>

<div class="page">
	<a href="/" class="back"><Icon name="arrow-left" size={18} /> Projects</a>

	<div class="title-row">
		<div>
			<h1>Uncategorized {#if clips.length > 0}<span class="count">{clips.length}</span>{/if}</h1>
			<p class="muted">Play each recording and send it to a song.</p>
		</div>
		<button class="btn" onclick={() => uploads.pick({ projectId: null, labelIds: [], label: 'Uncategorized' })}>
			<Icon name="upload" size={18} /> Upload
		</button>
	</div>

	{#if lastMove}
		<div class="moved" role="status">
			<Icon name="check" size={18} />
			<span>Sent to <a href="/projects/{lastMove.projectId}">{lastMove.projectName}</a></span>
			<button class="link" onclick={undo}>Undo</button>
		</div>
	{/if}

	{#if error}
		<p class="error" role="alert">{error}</p>
	{:else if !loaded}
		<div class="loading" aria-busy="true"></div>
	{:else if clips.length === 0}
		<div class="empty">
			<div class="empty-icon"><Icon name="check" size={26} /></div>
			<h2>All sorted</h2>
			<p class="muted">Uploads that aren't sent to a song land here, so uploading never waits on organizing.</p>
			<a class="btn" href="/">Back to Projects</a>
		</div>
	{:else}
		<div class="sorter">
			<section class="player" aria-label="Recording">
				<div class="stage" class:audio={detail?.media_type === 'audio'}>
					{#if !detail}
						<div class="waiting"><span class="spinner" aria-hidden="true"></span></div>
					{:else if detail.media_url}
						{#key detail.id}
							<ClipPlayer
								src={detail.media_url}
								kind={detail.media_type}
								poster={detail.thumbnail_url}
								start={detail.start_seconds}
								end={detail.end_seconds}
								{autoplay}
							>
								{#snippet art()}<div class="audio-art"><Icon name="mic" size={40} stroke={1.5} /></div>{/snippet}
							</ClipPlayer>
						{/key}
					{:else if detail.processing === 'pending'}
						<div class="waiting">
							<span class="spinner" aria-hidden="true"></span>
							<p>Making a playable copy…</p>
						</div>
					{:else}
						<div class="waiting"><p>This recording can't be played yet.</p></div>
					{/if}
				</div>

				<div class="stepper">
					<button class="btn btn-sm btn-icon" aria-label="Previous recording" disabled={index <= 0} onclick={() => step(-1)}>
						<Icon name="skip-back" size={16} />
					</button>
					<span class="position">{index + 1} of {clips.length}</span>
					<button class="btn btn-sm btn-icon" aria-label="Next recording" disabled={index >= clips.length - 1} onclick={() => step(1)}>
						<Icon name="skip-forward" size={16} />
					</button>
				</div>

				{#if detail}
					<div class="clip-info">
						<label class="visually-hidden" for="clip-title">Title</label>
						<input
							id="clip-title"
							class="field"
							placeholder="Add a title (optional)"
							maxlength="200"
							bind:value={titleDraft}
							onblur={saveTitle}
							onkeydown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
						/>
						<p class="facts">
							{dateTime(detail.recorded_at ?? detail.uploaded_at)}
							{#if detail.duration_seconds}· {duration(detail.duration_seconds)}{/if}
							· <span title={detail.original_filename}>{detail.original_filename}</span>
						</p>
					</div>
				{/if}
			</section>

			<section class="assign" aria-labelledby="send-to">
				<h2 id="send-to">Send to a song</h2>
				{#if actionError}<p class="error" role="alert">{actionError}</p>{/if}

				<div class="group">
					<span class="group-label">Part <span class="optional">optional</span></span>
					<div class="chips">
						{#each parts as l (l.id)}
							<button class="chip" aria-pressed={selectedParts.includes(l.id)} onclick={() => togglePart(l.id)}>
								{l.name}
							</button>
						{/each}
					</div>
				</div>

				<div class="group">
					<span class="group-label">Song</span>
					{#if projects.length > 6}
						<label class="visually-hidden" for="project-search">Find a song</label>
						<input id="project-search" class="field" type="search" placeholder="Find a song" bind:value={query} />
					{/if}
					<ul class="projects">
						{#each shown as p (p.id)}
							<li>
								<button
									class="project"
									aria-label="Send to {p.name}"
									disabled={busy || currentId === null}
									onclick={() => moveTo(p)}
								>
									<span class="project-name">{p.name}</span>
									<span class="project-meta">{plural(p.clip_count, 'clip')}</span>
									<Icon name="send" size={18} />
								</button>
							</li>
						{:else}
							{#if projects.length > 0}<li class="none">No song matches “{query}”.</li>{/if}
						{/each}
					</ul>

					{#if creating}
						<form class="create" onsubmit={createAndMove}>
							<label class="visually-hidden" for="new-song">New song name</label>
							<!-- svelte-ignore a11y_autofocus -->
							<input
								id="new-song"
								class="field"
								placeholder="New song name"
								maxlength="200"
								bind:value={newName}
								onkeydown={(e) => e.key === 'Escape' && (creating = false)}
								autofocus
							/>
							<button class="btn btn-primary" type="submit" disabled={busy || !newName.trim()}>Create and send</button>
						</form>
					{:else}
						<button class="btn new-song" onclick={() => ((creating = true), (newName = query.trim()))}>
							<Icon name="plus" size={18} /> New song
						</button>
					{/if}
				</div>

				<div class="secondary">
					<button class="btn btn-sm btn-ghost" disabled={index >= clips.length - 1} onclick={() => step(1)}>Skip for now</button>
					<button class="btn btn-sm btn-danger" onclick={trash}><Icon name="trash" size={16} /> Move to Trash</button>
				</div>
			</section>
		</div>

		{#if clips.length > 1}
			<section class="queue" aria-labelledby="queue-title">
				<h2 id="queue-title">All uncategorized</h2>
				<ul>
					{#each clips as clip (clip.id)}
						<li class:current={clip.id === currentId}>
							<ClipTile {clip} onplay={() => show(clip.id, true)} />
						</li>
					{/each}
				</ul>
			</section>
		{/if}
	{/if}
</div>

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
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 1rem;
		margin-bottom: 1rem;
	}
	h1 {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		font-size: 1.6rem;
	}
	.title-row p {
		margin: 0.3rem 0 0;
		font-size: 0.93rem;
	}
	.count {
		min-width: 1.7rem;
		padding: 0.05rem 0.5rem;
		border-radius: 999px;
		background: var(--accent-soft);
		color: var(--accent-strong);
		font-size: 0.85rem;
		font-weight: 700;
		text-align: center;
	}
	.loading {
		height: 50vh;
	}

	.moved {
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
	.moved a {
		color: var(--text);
		font-weight: 600;
		text-decoration: underline;
	}
	.moved .link {
		margin-left: auto;
	}
	.link {
		border: 0;
		padding: 0;
		background: none;
		color: var(--accent-strong);
		font-weight: 600;
		text-decoration: underline;
		cursor: pointer;
	}

	.sorter {
		display: grid;
		gap: 1.25rem;
	}
	@media (min-width: 900px) {
		.sorter {
			grid-template-columns: minmax(0, 1.35fr) minmax(300px, 1fr);
			align-items: start;
		}
	}
	.stage {
		display: grid;
		place-items: center;
		min-height: 220px;
		border-radius: var(--radius);
		overflow: hidden;
		background: #000;
	}
	.stage.audio {
		background: linear-gradient(160deg, #2f281d, #16140f);
	}
	.audio-art {
		display: grid;
		place-items: center;
		width: 88px;
		height: 88px;
		border-radius: 22px;
		background: rgb(240 166 70 / 0.16);
		color: var(--accent);
	}
	.waiting {
		display: grid;
		justify-items: center;
		gap: 0.5rem;
		padding: 2.5rem 1rem;
		color: var(--text-muted);
		text-align: center;
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
	.stepper {
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 0.75rem;
		margin-top: 0.6rem;
	}
	.position {
		min-width: 5.5rem;
		text-align: center;
		color: var(--text-muted);
		font-size: 0.9rem;
		font-variant-numeric: tabular-nums;
	}
	.clip-info {
		display: grid;
		gap: 0.35rem;
		margin-top: 0.6rem;
	}
	.facts {
		margin: 0;
		color: var(--text-faint);
		font-size: 0.85rem;
		overflow-wrap: anywhere;
	}

	.assign {
		display: grid;
		gap: 1rem;
		padding: 1rem;
		border-radius: var(--radius);
		background: var(--surface);
		border: 1px solid var(--border);
	}
	.assign h2 {
		font-size: 1.1rem;
	}
	.group {
		display: grid;
		gap: 0.45rem;
	}
	.group-label {
		font-size: 0.8rem;
		font-weight: 650;
		color: var(--text-muted);
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}
	.optional {
		margin-left: 0.3rem;
		font-weight: 500;
		text-transform: none;
		letter-spacing: 0;
		color: var(--text-faint);
	}
	.chips {
		display: flex;
		flex-wrap: wrap;
		gap: 0.35rem;
	}
	.chip {
		padding: 0.3rem 0.75rem;
		border-radius: 999px;
		border: 1px solid var(--border);
		background: transparent;
		color: var(--text-muted);
		font-size: 0.85rem;
		font-weight: 600;
		cursor: pointer;
	}
	.chip:hover {
		color: var(--text);
	}
	.chip[aria-pressed='true'] {
		background: var(--accent-soft);
		border-color: rgb(240 166 70 / 0.45);
		color: var(--accent-strong);
	}
	.projects {
		display: grid;
		gap: 0.35rem;
		max-height: 340px;
		margin: 0;
		padding: 0;
		overflow-y: auto;
		list-style: none;
	}
	.project {
		display: flex;
		align-items: center;
		gap: 0.6rem;
		width: 100%;
		min-height: 46px;
		padding: 0.5rem 0.75rem;
		border-radius: var(--radius-sm);
		border: 1px solid var(--border);
		background: var(--surface-2);
		color: var(--text);
		text-align: left;
		cursor: pointer;
	}
	.project:hover:not(:disabled) {
		border-color: rgb(240 166 70 / 0.5);
		background: var(--surface-3);
	}
	.project:disabled {
		opacity: 0.6;
		cursor: default;
	}
	.project-name {
		flex: 1;
		font-weight: 600;
		overflow-wrap: anywhere;
	}
	.project-meta {
		color: var(--text-faint);
		font-size: 0.82rem;
		white-space: nowrap;
	}
	.project :global(svg) {
		color: var(--text-faint);
	}
	.none {
		color: var(--text-faint);
		font-size: 0.9rem;
	}
	.create {
		display: flex;
		flex-wrap: wrap;
		gap: 0.4rem;
	}
	.create .field {
		flex: 1 1 200px;
	}
	.new-song {
		justify-self: start;
	}
	.secondary {
		display: flex;
		justify-content: space-between;
		gap: 0.5rem;
		padding-top: 0.75rem;
		border-top: 1px solid var(--border);
	}

	.queue {
		margin-top: 1.75rem;
	}
	.queue h2 {
		margin-bottom: 0.7rem;
		font-size: 1rem;
		color: var(--text-muted);
	}
	.queue ul {
		display: flex;
		flex-wrap: wrap;
		gap: 0.6rem;
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.queue li {
		width: var(--tile-w);
		border-radius: 12px;
		outline-offset: 2px;
	}
	.queue li.current {
		outline: 2px solid var(--accent);
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
		margin: 0 0 0.5rem;
		max-width: 40ch;
	}
	.empty-icon {
		display: grid;
		place-items: center;
		width: 52px;
		height: 52px;
		border-radius: 15px;
		background: rgb(92 201 138 / 0.14);
		color: #86e0ab;
	}
</style>
