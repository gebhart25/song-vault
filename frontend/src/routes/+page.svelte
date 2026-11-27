<script lang="ts">
	import { untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { ApiError, api } from '#lib/api.ts';
	import Icon from '#lib/components/Icon.svelte';
	import ProjectMenu from '#lib/components/ProjectMenu.svelte';
	import { counts } from '#lib/counts.svelte.ts';
	import { plural, shortDate } from '#lib/format.ts';
	import { contextMenu, type MenuAt } from '#lib/longpress.ts';
	import {
		PROJECT_KINDS,
		PROJECT_STATUSES,
		kindLabel,
		statusLabel,
		type ProjectDetail,
		type ProjectKind,
		type ProjectStatus,
		type ProjectSummary
	} from '#lib/types.ts';

	let projects = $state<ProjectSummary[]>([]);
	let loaded = $state(false);
	let loadError = $state('');

	let query = $state('');
	let statusFilter = $state<ProjectStatus | ''>('');
	/** Originals, covers… (spec 3.1) */
	let kindFilter = $state<ProjectKind | ''>('');
	let includeArchived = $state(false);

	let creating = $state(false);
	let newName = $state('');
	let newKind = $state<ProjectKind>('original');
	let createError = $state('');
	let createBusy = $state(false);

	const filtering = $derived(query.trim() !== '' || statusFilter !== '' || kindFilter !== '' || includeArchived);

	// Long-press or right-click a card: set its status or type, or change its picture (spec 3.1).
	let menuFor = $state<{ project: ProjectSummary; at: MenuAt } | null>(null);

	/** A status or type set from the menu: the card changes where it is (no reload, so the
	 * grid doesn't shuffle while recategorizing); it leaves a filter it no longer matches. */
	function changed(detail: ProjectDetail) {
		if ((statusFilter && detail.status !== statusFilter) || (kindFilter && detail.kind !== kindFilter)) {
			projects = projects.filter((p) => p.id !== detail.id);
			return;
		}
		projects = projects.map((p) =>
			p.id === detail.id ? { ...p, status: detail.status, kind: detail.kind, updated_at: detail.updated_at } : p
		);
	}

	// Reload whenever the filters change; typing in the search box is debounced.
	$effect(() => {
		const params = new URLSearchParams();
		if (query.trim()) params.set('q', query.trim());
		if (statusFilter) params.set('status', statusFilter);
		if (kindFilter) params.set('kind', kindFilter);
		if (includeArchived) params.set('include_archived', 'true');
		const qs = params.toString();

		let cancelled = false;
		const timer = setTimeout(
			async () => {
				try {
					const result = await api.get<ProjectSummary[]>(`/projects${qs ? `?${qs}` : ''}`);
					if (!cancelled) {
						projects = result;
						loadError = '';
					}
				} catch (e) {
					if (!cancelled) loadError = e instanceof ApiError ? e.message : 'Could not load projects.';
				} finally {
					if (!cancelled) loaded = true;
				}
			},
			untrack(() => loaded) ? 200 : 0
		);
		return () => {
			cancelled = true;
			clearTimeout(timer);
		};
	});

	function startCreate() {
		creating = true;
		newName = '';
		// Filtering to covers and making a new song: it's likely a cover too.
		newKind = kindFilter || 'original';
		createError = '';
	}

	async function create(event: SubmitEvent) {
		event.preventDefault();
		if (!newName.trim()) return;
		createBusy = true;
		createError = '';
		try {
			const project = await api.post<ProjectDetail>('/projects', { name: newName, kind: newKind });
			await goto(`/projects/${project.id}`);
		} catch (e) {
			createError = e instanceof ApiError ? e.message : 'Could not create the project.';
		} finally {
			createBusy = false;
		}
	}

	// A stable accent per project for covers without a thumbnail yet.
	function hue(name: string): number {
		let h = 0;
		for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) % 360;
		return h;
	}
</script>

<div class="page">
	<div class="title-row">
		<h1>Projects</h1>
		{#if !creating}
			<button class="btn btn-primary" onclick={startCreate}>
				<Icon name="plus" size={18} /> New project
			</button>
		{/if}
	</div>

	{#if creating}
		<form class="create" onsubmit={create}>
			<label class="visually-hidden" for="new-name">Song name</label>
			<!-- svelte-ignore a11y_autofocus -->
			<input
				id="new-name"
				class="field"
				placeholder="Song name, e.g. Midnight Drive"
				maxlength="200"
				bind:value={newName}
				onkeydown={(e) => e.key === 'Escape' && (creating = false)}
				autofocus
			/>
			<label class="visually-hidden" for="new-kind">Type</label>
			<select id="new-kind" class="field kind-select" bind:value={newKind}>
				{#each PROJECT_KINDS as k (k.value)}<option value={k.value}>{k.label}</option>{/each}
			</select>
			<div class="create-actions">
				<button class="btn btn-primary" type="submit" disabled={createBusy || !newName.trim()}>
					Create
				</button>
				<button class="btn btn-ghost" type="button" onclick={() => (creating = false)}>Cancel</button>
			</div>
			{#if createError}<p class="error" role="alert">{createError}</p>{/if}
		</form>
	{/if}

	<div class="filters">
		<div class="search">
			<Icon name="search" size={18} />
			<label class="visually-hidden" for="search">Search projects</label>
			<input id="search" class="field" type="search" placeholder="Search songs and notes" bind:value={query} />
		</div>
		<div class="chips" role="group" aria-label="Filter by status">
			<button class="chip" aria-pressed={statusFilter === ''} onclick={() => (statusFilter = '')}>All</button>
			{#each PROJECT_STATUSES as s (s.value)}
				<button
					class="chip"
					aria-pressed={statusFilter === s.value}
					onclick={() => (statusFilter = statusFilter === s.value ? '' : s.value)}
				>
					{s.label}
				</button>
			{/each}
		</div>
		<div class="chips" role="group" aria-label="Filter by type">
			<button class="chip" aria-pressed={kindFilter === ''} onclick={() => (kindFilter = '')}>All types</button>
			{#each PROJECT_KINDS as k (k.value)}
				<button
					class="chip"
					aria-pressed={kindFilter === k.value}
					title={k.hint}
					onclick={() => (kindFilter = kindFilter === k.value ? '' : k.value)}
				>
					{k.label}
				</button>
			{/each}
		</div>
		<label class="toggle">
			<input type="checkbox" bind:checked={includeArchived} /> Show archived
		</label>
	</div>

	{#if loadError}
		<p class="error" role="alert">{loadError}</p>
	{:else if !loaded}
		<div class="grid" aria-hidden="true">
			{#each [1, 2, 3] as i (i)}<div class="card skeleton"></div>{/each}
		</div>
	{:else}
		<!-- Recordings that aren't in a song yet: open it to play and sort them.
		     First while there's something to sort, last once it's empty. -->
		{#snippet uncategorizedCard()}
			<li>
				<a class="card uncategorized" href="/uncategorized">
					<div class="cover">
						{#if counts.uncategorized_cover_url && counts.uncategorized > 0}
							<img src={counts.uncategorized_cover_url} alt="" loading="lazy" />
						{:else}
							<Icon name="inbox" size={30} stroke={1.6} />
						{/if}
					</div>
					<div class="body">
						<div class="name-row">
							<h2>Uncategorized</h2>
							{#if counts.uncategorized > 0}
								<span class="pill to-sort">To sort</span>
							{/if}
						</div>
						<p class="meta">
							{#if counts.uncategorized > 0}
								{plural(counts.uncategorized, 'clip')} · Updated {shortDate(counts.uncategorized_updated_at)}
							{:else}
								All sorted
							{/if}
						</p>
					</div>
				</a>
			</li>
		{/snippet}
		{#if !filtering || projects.length > 0}
			<ul class="grid">
				{#if !filtering && counts.uncategorized > 0}{@render uncategorizedCard()}{/if}
				{#each projects as p (p.id)}
				<li>
					<a class="card" href="/projects/{p.id}" class:archived={p.archived} use:contextMenu={(at) => (menuFor = { project: p, at })}>
						<div class="cover" style:--h={hue(p.name)}>
							{#if p.cover_thumbnail_url}
								<img src={p.cover_thumbnail_url} alt="" loading="lazy" draggable="false" />
							{:else}
								<Icon name="music" size={30} stroke={1.6} />
							{/if}
						</div>
						<div class="body">
							<div class="name-row">
								<h2>{p.name}</h2>
								<span class="pill" data-status={p.status}>{statusLabel(p.status)}</span>
							</div>
							<p class="meta">
								{plural(p.clip_count, 'clip')} · Updated {shortDate(p.updated_at)}
								<span class="kind" data-kind={p.kind}>{kindLabel(p.kind)}</span>
								{#if p.archived}<span class="pill">Archived</span>{/if}
							</p>
						</div>
					</a>
				</li>
				{/each}
				{#if !filtering && counts.uncategorized === 0}{@render uncategorizedCard()}{/if}
			</ul>
		{/if}
		{#if projects.length === 0}
			<div class="empty">
				{#if filtering}
					<p>No projects match.</p>
				{:else}
					<div class="empty-icon"><Icon name="music" size={28} /></div>
					<h2>No songs yet</h2>
					<p class="muted">Create a project for each song idea, then add your recordings to it.</p>
					{#if !creating}
						<button class="btn btn-primary" onclick={startCreate}>
							<Icon name="plus" size={18} /> New project
						</button>
					{/if}
				{/if}
			</div>
		{/if}
		{#if !filtering}
			<p class="trash-link">
				<a href="/trash"
					><Icon name="trash" size={16} /> Trash{#if counts.trash > 0}&nbsp;<span class="muted">({counts.trash})</span>{/if}</a
				>
			</p>
		{/if}
	{/if}
</div>

{#if menuFor}
	<ProjectMenu project={menuFor.project} at={menuFor.at} onclose={() => (menuFor = null)} onchange={changed} />
{/if}

<style>
	.page {
		max-width: 1180px;
		margin: 0 auto;
		padding: 1.25rem 1rem;
	}
	.title-row {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 1rem;
		margin-bottom: 1rem;
	}
	h1 {
		font-size: 1.6rem;
	}

	.create {
		display: flex;
		flex-wrap: wrap;
		gap: 0.6rem;
		padding: 0.9rem;
		margin-bottom: 1rem;
		border-radius: var(--radius);
		background: var(--surface);
		border: 1px solid var(--border);
	}
	.create .field {
		flex: 1 1 260px;
	}
	.create-actions {
		display: flex;
		gap: 0.4rem;
	}
	.create .error {
		flex-basis: 100%;
	}

	.filters {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.75rem 1rem;
		margin-bottom: 1.25rem;
	}
	.search {
		position: relative;
		flex: 1 1 260px;
		max-width: 420px;
		color: var(--text-faint);
	}
	.search :global(svg) {
		position: absolute;
		left: 0.7rem;
		top: 50%;
		translate: 0 -50%;
		pointer-events: none;
	}
	.search .field {
		padding-left: 2.3rem;
	}
	.chips {
		display: flex;
		gap: 0.35rem;
		overflow-x: auto;
		scrollbar-width: none;
		max-width: 100%;
	}
	.chip {
		padding: 0.3rem 0.75rem;
		border-radius: 999px;
		border: 1px solid var(--border);
		background: transparent;
		color: var(--text-muted);
		font-size: 0.85rem;
		font-weight: 550;
		cursor: pointer;
		white-space: nowrap;
	}
	.chip:hover {
		color: var(--text);
	}
	.chip[aria-pressed='true'] {
		background: var(--accent-soft);
		border-color: rgb(240 166 70 / 0.45);
		color: var(--accent-strong);
	}
	.create .kind-select {
		flex: 0 0 auto;
		width: auto;
	}
	/* A song's type on its card (Original, Cover…) */
	.kind {
		padding: 0.05rem 0.5rem;
		border-radius: 999px;
		border: 1px solid var(--border);
		color: var(--text-muted);
		font-size: 0.75rem;
		font-weight: 650;
	}
	.kind[data-kind='original'] {
		border-color: rgb(240 166 70 / 0.45);
		color: var(--accent-strong);
	}
	.kind[data-kind='cover'] {
		border-color: rgb(156 194 255 / 0.4);
		color: #9cc2ff;
	}
	.kind[data-kind='cowrite'] {
		border-color: rgb(134 224 171 / 0.4);
		color: #86e0ab;
	}
	.kind[data-kind='arrangement'] {
		border-color: rgb(208 178 255 / 0.4);
		color: #d0b2ff;
	}

	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
		gap: 1rem;
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.card {
		display: flex;
		flex-direction: column;
		height: 100%;
		/* A long-press opens the card's menu, not the phone's link menu or a text selection */
		-webkit-touch-callout: none;
		-webkit-user-select: none;
		user-select: none;
		border-radius: var(--radius);
		overflow: hidden;
		background: var(--surface);
		border: 1px solid var(--border);
		transition:
			border-color 0.15s,
			translate 0.15s;
	}
	.card:hover {
		border-color: #3a4150;
		translate: 0 -1px;
	}
	.card.archived {
		opacity: 0.6;
	}
	.cover {
		display: grid;
		place-items: center;
		aspect-ratio: 16 / 9;
		/* Without this, a portrait thumbnail stretches the card to its own height. */
		overflow: hidden;
		color: hsl(var(--h) 55% 72% / 0.75);
		background:
			radial-gradient(120% 120% at 0% 0%, hsl(var(--h) 45% 32% / 0.65), transparent 60%),
			linear-gradient(135deg, hsl(var(--h) 30% 16%), #15181d);
	}
	.cover img {
		width: 100%;
		height: 100%;
		object-fit: cover;
	}
	.uncategorized .cover {
		color: var(--text-faint);
		background:
			repeating-linear-gradient(135deg, rgb(255 255 255 / 0.025) 0 10px, transparent 10px 20px),
			linear-gradient(135deg, #1d2129, #15181d);
	}
	.to-sort {
		background: var(--accent-soft);
		color: var(--accent-strong);
	}
	.grid + .empty {
		margin-top: 1rem;
	}
	.trash-link {
		margin: 1.75rem 0 0;
		font-size: 0.9rem;
	}
	.trash-link a {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
		color: var(--text-muted);
		font-weight: 550;
	}
	.trash-link a:hover {
		color: var(--text);
	}
	.body {
		padding: 0.8rem 0.95rem 0.95rem;
	}
	.name-row {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 0.6rem;
	}
	.name-row h2 {
		font-size: 1.05rem;
		overflow-wrap: anywhere;
	}
	.meta {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		margin: 0.35rem 0 0;
		font-size: 0.85rem;
		color: var(--text-muted);
	}
	.skeleton {
		height: 220px;
		background: linear-gradient(90deg, var(--surface), var(--surface-2), var(--surface));
		background-size: 200% 100%;
		animation: shimmer 1.4s infinite linear;
	}
	@keyframes shimmer {
		to {
			background-position: -200% 0;
		}
	}

	.empty {
		display: grid;
		justify-items: center;
		gap: 0.6rem;
		padding: 3.5rem 1rem;
		text-align: center;
		border: 1px dashed var(--border);
		border-radius: var(--radius);
	}
	.empty p {
		margin: 0;
		max-width: 34ch;
	}
	.empty h2 {
		font-size: 1.15rem;
	}
	.empty .btn {
		margin-top: 0.6rem;
	}
	.empty-icon {
		display: grid;
		place-items: center;
		width: 56px;
		height: 56px;
		border-radius: 16px;
		background: var(--accent-soft);
		color: var(--accent);
	}

	@media (max-width: 560px) {
		.grid {
			grid-template-columns: 1fr;
		}
		.cover {
			aspect-ratio: 3 / 1;
		}
	}
</style>
