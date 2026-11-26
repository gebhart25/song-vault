<script lang="ts">
	import { untrack } from 'svelte';
	import { SvelteSet } from 'svelte/reactivity';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { ApiError, api } from '#lib/api.ts';
	import ClipMenu from '#lib/components/ClipMenu.svelte';
	import ClipPanel from '#lib/components/ClipPanel.svelte';
	import ClipStrip from '#lib/components/ClipStrip.svelte';
	import type { MenuAt } from '#lib/components/ClipTile.svelte';
	import Icon from '#lib/components/Icon.svelte';
	import SongNotebook from '#lib/components/SongNotebook.svelte';
	import SongNotes from '#lib/components/SongNotes.svelte';
	import { refreshCounts } from '#lib/counts.svelte.ts';
	import { plural, shortDate, toLocalInput } from '#lib/format.ts';
	import { nextPartName } from '#lib/parts.ts';
	import { roleOptions, sameRole } from '#lib/roles.ts';
	import {
		CLIP_STATUSES,
		PROJECT_STATUSES,
		type ClipStatus,
		type ClipSummary,
		type Label,
		type ProjectDetail,
		type ProjectStatus,
		type ProjectSummary,
		type Row
	} from '#lib/types.ts';
	import { uploads } from '#lib/uploads.svelte.ts';

	const SHOW_EMPTY_KEY = 'songvault.showEmptyParts';
	const HIDE_DISCARDED_KEY = 'songvault.hideDiscarded';

	const id = $derived(Number(page.params.id));

	let project = $state<ProjectDetail | null>(null);
	let loadError = $state('');
	let actionError = $state('');

	let showEmpty = $state(localStorage.getItem(SHOW_EMPTY_KEY) === '1');
	let hideDiscarded = $state(localStorage.getItem(HIDE_DISCARDED_KEY) === '1');
	let arranging = $state(false);
	let menuOpen = $state(false);
	let partsMenuOpen = $state(false);
	let renaming = $state(false);
	let nameDraft = $state('');
	let addingPart = $state(false);
	let partName = $state('');
	let openId = $state<number | null>(null);

	// Filters for this visit (spec 3.4): which parts, statuses and recorded dates to show.
	let filterOpen = $state(false);
	let partFilter = $state<number[]>([]);
	let statusFilter = $state<ClipStatus[]>([]);
	/** Roles to show, lowercased; '' = takes without a role */
	let roleFilter = $state<string[]>([]);
	let fromDate = $state('');
	let toDate = $state('');

	// The tile menu (4.3) and bulk select (5.5).
	let menuFor = $state<{ clip: ClipSummary; at: MenuAt } | null>(null);
	let selecting = $state(false);
	const selected = new SvelteSet<number>();
	let bulkMenu = $state<'song' | 'parts' | 'status' | null>(null);
	let bulkProjects = $state<ProjectSummary[] | null>(null);
	let bulkBusy = $state(false);

	// A row's menu (spec 3.3): record into it, add another (Verse -> Verse 1, Verse 2), rename.
	let rowMenu = $state<number | null>(null);
	let renamingRow = $state<number | null>(null);
	let rowNameDraft = $state('');
	/** Parts just added: shown even while empty parts are hidden */
	const revealed = new SvelteSet<number>();

	$effect(() => {
		localStorage.setItem(SHOW_EMPTY_KEY, showEmpty ? '1' : '0');
		localStorage.setItem(HIDE_DISCARDED_KEY, hideDiscarded ? '1' : '0');
	});

	$effect(() => {
		const projectId = id;
		project = null;
		loadError = '';
		untrack(() => {
			clearFilters();
			endSelect();
			menuFor = null;
			openId = null;
			rowMenu = renamingRow = null;
			revealed.clear();
		});
		api
			.get<ProjectDetail>(`/projects/${projectId}`)
			.then((p) => {
				if (projectId === id) setProject(p);
			})
			.catch((e) => {
				loadError = e instanceof ApiError && e.status === 404 ? 'This project doesn’t exist.' : 'Could not load the project.';
			});
	});

	function setProject(p: ProjectDetail) {
		project = p;
	}

	async function reload() {
		try {
			const p = await api.get<ProjectDetail>(`/projects/${id}`);
			if (p.id === id) project = p;
		} catch {
			// keep showing what we have
		}
	}

	// New uploads into this song, and thumbnails finishing, show up without a reload.
	let seenVersion = uploads.version;
	$effect(() => {
		if (uploads.version !== seenVersion) {
			seenVersion = uploads.version;
			reload();
		}
	});
	const allClips = $derived(project ? [...project.unlabeled, ...project.rows.flatMap((r) => r.clips)] : []);
	const anyPending = $derived(allClips.some((c) => c.processing === 'pending'));
	$effect(() => {
		if (!anyPending) return;
		const timer = setInterval(reload, 5000);
		return () => clearInterval(timer);
	});

	function uploadTo(row: Row | null) {
		if (!project) return;
		uploads.pick({
			projectId: project.id,
			labelIds: row ? [row.label_id] : [],
			label: row ? `${project.name} · ${row.name}` : project.name
		});
	}

	function clipChanged() {
		reload();
		refreshCounts();
	}

	// --- what's shown -------------------------------------------------------------------

	const clipFilterOn = $derived(statusFilter.length > 0 || roleFilter.length > 0 || !!fromDate || !!toDate);
	const filterCount = $derived(
		partFilter.length + statusFilter.length + roleFilter.length + (fromDate || toDate ? 1 : 0)
	);
	/** The roles used in this song, for the role filter */
	const songRoles = $derived(roleOptions(allClips.map((c) => c.role)).filter((r) => allClips.some((c) => sameRole(c.role, r))));
	const anyWithoutRole = $derived(songRoles.length > 0 && allClips.some((c) => !c.role));
	// Asking for Discarded in the status filter shows them even with "Hide discarded" on.
	const hidingDiscarded = $derived(hideDiscarded && !statusFilter.includes('discarded'));
	const discardedCount = $derived(new Set(allClips.filter((c) => c.status === 'discarded').map((c) => c.id)).size);

	const localDay = (iso: string) => toLocalInput(iso).slice(0, 10);

	function shows(c: ClipSummary): boolean {
		if (hidingDiscarded && c.status === 'discarded') return false;
		if (statusFilter.length > 0 && !statusFilter.includes(c.status)) return false;
		if (roleFilter.length > 0 && !roleFilter.includes((c.role ?? '').trim().toLowerCase())) return false;
		if (fromDate || toDate) {
			const day = localDay(c.recorded_at ?? c.uploaded_at);
			if (fromDate && day < fromDate) return false;
			if (toDate && day > toDate) return false;
		}
		return true;
	}

	const viewRows = $derived(project ? project.rows.map((row) => ({ row, clips: row.clips.filter(shows) })) : []);
	const visibleRows = $derived(
		arranging
			? viewRows
			: viewRows.filter(({ row, clips }) => {
					if (partFilter.length > 0) {
						// A part that was asked for shows even when it's empty.
						return partFilter.includes(row.label_id) && (clips.length > 0 || !clipFilterOn);
					}
					if (clips.length > 0) return true;
					if (clipFilterOn) return false;
					// Only discarded takes, hidden: keep the row and say so.
					return row.clips.length > 0 || showEmpty || revealed.has(row.label_id);
				})
	);
	const unlabeled = $derived(project ? project.unlabeled.filter(shows) : []);
	const showUnlabeled = $derived(!arranging && partFilter.length === 0 && unlabeled.length > 0);
	const emptyParts = $derived(project ? project.rows.filter((r) => r.clips.length === 0).length : 0);
	const shownIds = $derived(new Set([...(showUnlabeled ? unlabeled : []), ...visibleRows.flatMap((v) => v.clips)].map((c) => c.id)));

	const filterSummary = $derived.by(() => {
		if (!project) return '';
		const p = project;
		const parts = partFilter.map((lid) => p.rows.find((r) => r.label_id === lid)?.name).filter(Boolean);
		const statuses = statusFilter.map((s) => CLIP_STATUSES.find((x) => x.value === s)?.label);
		const roles = roleFilter.map((r) => (r ? (songRoles.find((x) => x.toLowerCase() === r) ?? r) : 'No role'));
		const fmt = (day: string) => shortDate(`${day}T12:00:00`);
		const dates = fromDate && toDate ? `${fmt(fromDate)} – ${fmt(toDate)}` : fromDate ? `since ${fmt(fromDate)}` : toDate ? `until ${fmt(toDate)}` : '';
		return [parts.join(', '), statuses.join(', '), roles.join(', '), dates].filter(Boolean).join(' · ');
	});

	function toggleIn<T>(list: T[], value: T): T[] {
		return list.includes(value) ? list.filter((x) => x !== value) : [...list, value];
	}

	function clearFilters() {
		partFilter = [];
		statusFilter = [];
		roleFilter = [];
		fromDate = '';
		toDate = '';
	}

	// --- tiles: play, menu, select ------------------------------------------------------

	function play(c: ClipSummary) {
		if (!selecting) {
			openId = c.id;
			return;
		}
		if (selected.has(c.id)) selected.delete(c.id);
		else selected.add(c.id);
	}

	function openMenu(clip: ClipSummary, at: MenuAt) {
		menuFor = { clip, at };
	}

	function startSelect(first?: number) {
		openId = null;
		arranging = false;
		selecting = true;
		selected.clear();
		if (first !== undefined) selected.add(first);
	}

	function endSelect() {
		selecting = false;
		selected.clear();
		bulkMenu = null;
	}

	const allShownSelected = $derived(shownIds.size > 0 && [...shownIds].every((cid) => selected.has(cid)));

	function selectAll() {
		if (allShownSelected) selected.clear();
		else for (const cid of shownIds) selected.add(cid);
	}

	/** Parts that at least one picked clip is in (to offer "take out of"). */
	const selectedParts = $derived(
		project ? project.rows.filter((r) => r.clips.some((c) => selected.has(c.id))) : []
	);

	function openBulkMenu(which: 'song' | 'parts' | 'status') {
		bulkMenu = bulkMenu === which ? null : which;
		if (which === 'song' && !bulkProjects) {
			api.get<ProjectSummary[]>('/projects?include_archived=true')
				.then((p) => (bulkProjects = p))
				.catch(() => (actionError = 'Could not load your songs.'));
		}
	}

	async function bulk(body: Record<string, unknown>, path = '/clips/bulk') {
		if (selected.size === 0 || bulkBusy) return;
		bulkBusy = true;
		actionError = '';
		try {
			await api.post(path, { clip_ids: [...selected], ...body });
			bulkMenu = null;
			await reload();
			refreshCounts();
			// Clips that left this song (moved or trashed) drop out of the selection.
			const here = new Set(allClips.map((c) => c.id));
			for (const cid of [...selected]) if (!here.has(cid)) selected.delete(cid);
		} catch (e) {
			actionError = e instanceof ApiError ? e.message : 'Something went wrong.';
		} finally {
			bulkBusy = false;
		}
	}

	function bulkTrash() {
		const n = selected.size;
		if (!confirm(`Move ${plural(n, 'clip')} to Trash? You can restore ${n === 1 ? 'it' : 'them'} from Trash for 30 days.`)) return;
		bulk({}, '/clips/bulk/trash');
	}

	// --- the song itself ------------------------------------------------------------------

	async function run<T>(action: () => Promise<T>): Promise<T | undefined> {
		actionError = '';
		try {
			return await action();
		} catch (e) {
			actionError = e instanceof ApiError ? e.message : 'Something went wrong.';
			return undefined;
		}
	}

	async function update(changes: Partial<ProjectDetail>) {
		const p = await run(() => api.patch<ProjectDetail>(`/projects/${id}`, changes));
		if (p) setProject(p);
		return p;
	}

	async function saveRows(rows: Row[]) {
		if (!project) return;
		project.rows = rows; // optimistic
		const p = await run(() =>
			api.put<ProjectDetail>(`/projects/${id}/rows`, {
				rows: rows.map((r) => ({ label_id: r.label_id, collapsed: r.collapsed }))
			})
		);
		if (p) project.rows = p.rows;
	}

	function toggleCollapsed(row: Row) {
		if (!project) return;
		saveRows(project.rows.map((r) => (r.label_id === row.label_id ? { ...r, collapsed: !r.collapsed } : r)));
	}

	function move(row: Row, direction: -1 | 1) {
		if (!project) return;
		const rows = [...project.rows];
		const from = rows.findIndex((r) => r.label_id === row.label_id);
		const to = from + direction;
		// Full Song is pinned at index 0.
		if (to < 1 || to >= rows.length || rows[from].is_full_song) return;
		[rows[from], rows[to]] = [rows[to], rows[from]];
		saveRows(rows);
	}

	function startArranging() {
		partsMenuOpen = false;
		endSelect();
		arranging = true;
	}

	function startAddPart() {
		partsMenuOpen = false;
		addingPart = !addingPart;
	}

	async function addPart(event: SubmitEvent) {
		event.preventDefault();
		if (!partName.trim()) return;
		const created = await run(() => api.post<Label>('/labels', { name: partName, project_id: id }));
		if (created) {
			partName = '';
			addingPart = false;
			revealed.add(created.id);
			const p = await run(() => api.get<ProjectDetail>(`/projects/${id}`));
			if (p) setProject(p);
		}
	}

	async function addAnother(row: Row) {
		rowMenu = null;
		const p = await run(() => api.post<ProjectDetail>(`/projects/${id}/rows/${row.label_id}/another`));
		if (!p) return;
		const before = new Set(project?.rows.map((r) => r.label_id));
		for (const r of p.rows) if (!before.has(r.label_id)) revealed.add(r.label_id);
		setProject(p);
	}

	function startRowRename(row: Row) {
		rowMenu = null;
		rowNameDraft = row.name;
		renamingRow = row.label_id;
	}

	async function saveRowRename(event: SubmitEvent, row: Row) {
		event.preventDefault();
		const name = rowNameDraft.trim();
		if (name && name !== row.name) {
			const p = await run(() => api.patch<ProjectDetail>(`/projects/${id}/rows/${row.label_id}`, { name }));
			if (!p) return;
			setProject(p);
		}
		renamingRow = null;
	}

	async function deletePart(row: Row) {
		rowMenu = null;
		if (!confirm(`Delete the part “${row.name}”?`)) return;
		const ok = await run(async () => {
			await api.del(`/labels/${row.label_id}`);
			return true;
		});
		if (ok) {
			const p = await run(() => api.get<ProjectDetail>(`/projects/${id}`));
			if (p) setProject(p);
		}
	}

	function startRename() {
		if (!project) return;
		menuOpen = false;
		nameDraft = project.name;
		renaming = true;
	}

	async function saveRename(event: SubmitEvent) {
		event.preventDefault();
		if (!project) return;
		if (nameDraft.trim() && nameDraft.trim() !== project.name) {
			if (!(await update({ name: nameDraft }))) return;
		}
		renaming = false;
	}

	function exportZip() {
		menuOpen = false;
		// A download: the browser saves the zip and stays on this page.
		window.location.href = `/api/projects/${id}/export`;
	}

	async function toggleArchived() {
		menuOpen = false;
		if (project) await update({ archived: !project.archived });
	}

	async function moveToTrash() {
		menuOpen = false;
		if (!project) return;
		if (!confirm(`Move “${project.name}” to Trash? It and its recordings can be restored from Trash for 30 days.`)) return;
		const ok = await run(async () => {
			await api.del(`/projects/${id}`);
			return true;
		});
		if (ok) await goto('/');
	}

	function closeMenus(event: MouseEvent) {
		const target = event.target as Element;
		const wrap = target.closest('.menu-wrap');
		if (menuOpen && wrap?.id !== 'project-menu') menuOpen = false;
		if (partsMenuOpen && wrap?.id !== 'parts-menu') partsMenuOpen = false;
		if (rowMenu !== null && wrap?.id !== `row-menu-${rowMenu}`) rowMenu = null;
		if (bulkMenu && !target.closest('.bulk-bar')) bulkMenu = null;
	}

	function onkeydown(e: KeyboardEvent) {
		if (e.key !== 'Escape' || menuFor || openId !== null) return;
		if (bulkMenu) bulkMenu = null;
		else if (selecting) endSelect();
	}
</script>

<svelte:window onclick={closeMenus} {onkeydown} />

<div class="page" class:with-panel={openId !== null} class:selecting>
	<a href="/" class="back"><Icon name="arrow-left" size={18} /> Projects</a>

	{#if loadError}
		<p class="error" role="alert">{loadError}</p>
	{:else if !project}
		<div class="loading" aria-busy="true"></div>
	{:else}
		<header class="head">
			<div class="title-row">
				{#if renaming}
					<form class="rename" onsubmit={saveRename}>
						<label class="visually-hidden" for="rename">Song name</label>
						<!-- svelte-ignore a11y_autofocus -->
						<input
							id="rename"
							class="field"
							maxlength="200"
							bind:value={nameDraft}
							onkeydown={(e) => e.key === 'Escape' && (renaming = false)}
							autofocus
						/>
						<button class="btn btn-primary btn-sm" type="submit">Save</button>
						<button class="btn btn-ghost btn-sm" type="button" onclick={() => (renaming = false)}>Cancel</button>
					</form>
				{:else}
					<h1>{project.name}</h1>
				{/if}

				<div class="head-actions">
					<label class="visually-hidden" for="status">Status</label>
					<select
						id="status"
						class="field status"
						data-status={project.status}
						value={project.status}
						onchange={(e) => update({ status: e.currentTarget.value as ProjectStatus })}
					>
						{#each PROJECT_STATUSES as s (s.value)}
							<option value={s.value}>{s.label}</option>
						{/each}
					</select>

					<div class="menu-wrap" id="project-menu">
						<button
							class="btn btn-icon"
							aria-label="Project actions"
							aria-haspopup="menu"
							aria-expanded={menuOpen}
							onclick={() => (menuOpen = !menuOpen)}
						>
							<Icon name="more" />
						</button>
						{#if menuOpen}
							<div class="menu" role="menu">
								<button role="menuitem" onclick={startRename}><Icon name="edit" size={18} /> Rename</button>
								<button role="menuitem" onclick={exportZip}><Icon name="download" size={18} /> Export as zip</button>
								<button role="menuitem" onclick={toggleArchived}>
									<Icon name="archive" size={18} />
									{project.archived ? 'Unarchive' : 'Archive'}
								</button>
								<button role="menuitem" class="danger" onclick={moveToTrash}>
									<Icon name="trash" size={18} /> Move to Trash
								</button>
							</div>
						{/if}
					</div>
				</div>
			</div>
			<p class="meta">
				{plural(project.clip_count, 'clip')} · Updated {shortDate(project.updated_at)}
				{#if project.archived}<span class="pill">Archived</span>{/if}
			</p>

			<div class="quick-links">
				<a class="btn btn-sm" href="/projects/{project.id}/lyrics">
					<Icon name="lyrics" size={16} /> Lyrics &amp; chords
					{#if project.lyric_sections > 0}<span class="muted">· {plural(project.lyric_sections, 'section')}</span>{/if}
				</a>
				<SongNotebook projectId={project.id} />
			</div>

			<SongNotes {project} partNames={project.rows.map((r) => r.name)} onsave={update} />
		</header>

		{#if actionError}<p class="error banner" role="alert">{actionError}</p>{/if}

		<div class="toolbar">
			<div class="toggles">
				<label class="toggle">
					<input type="checkbox" bind:checked={showEmpty} disabled={arranging} />
					Show empty parts{#if emptyParts > 0 && !showEmpty && !arranging}&nbsp;<span class="muted">({emptyParts})</span>{/if}
				</label>
				<label class="toggle">
					<input type="checkbox" bind:checked={hideDiscarded} disabled={arranging} />
					Hide discarded{#if discardedCount > 0}&nbsp;<span class="muted">({discardedCount})</span>{/if}
				</label>
			</div>
			<div class="toolbar-actions">
				{#if arranging}
					<button class="btn btn-sm active" onclick={() => (arranging = false)}>
						<Icon name="check" size={16} /> Done
					</button>
				{:else}
					<button class="btn btn-sm btn-primary" onclick={() => uploadTo(null)}>
						<Icon name="upload" size={16} /> Upload
					</button>
					<a class="btn btn-sm record" href="/record?song={project.id}" aria-label="Record">
						<Icon name="record" size={16} /> <span class="lbl">Record</span>
					</a>
					<button
						class="btn btn-sm"
						class:active={filterOpen || filterCount > 0}
						aria-expanded={filterOpen}
						aria-controls="filters"
						onclick={() => (filterOpen = !filterOpen)}
					>
						<Icon name="filter" size={16} /> Filter{#if filterCount > 0}<span class="n">{filterCount}</span>{/if}
					</button>
					<button class="btn btn-sm" class:active={selecting} aria-pressed={selecting} onclick={() => (selecting ? endSelect() : startSelect())}>
						<Icon name="select" size={16} /> Select
					</button>
					<div class="menu-wrap" id="parts-menu">
						<button
							class="btn btn-sm btn-icon"
							aria-label="Arrange or add parts"
							title="Arrange or add parts"
							aria-haspopup="menu"
							aria-expanded={partsMenuOpen}
							onclick={() => (partsMenuOpen = !partsMenuOpen)}
						>
							<Icon name="rows" size={17} />
						</button>
						{#if partsMenuOpen}
							<div class="menu" role="menu">
								<button role="menuitem" onclick={startArranging}><Icon name="rows" size={18} /> Arrange parts</button>
								<button role="menuitem" onclick={startAddPart}><Icon name="plus" size={18} /> Add part</button>
							</div>
						{/if}
					</div>
				{/if}
			</div>
		</div>

		{#if filterOpen && !arranging}
			<div class="filters" id="filters">
				<div class="filter-group">
					<span class="filter-label" id="filter-parts">Parts</span>
					<div class="chips" role="group" aria-labelledby="filter-parts">
						{#each project.rows.filter((r) => r.clips.length > 0 || partFilter.includes(r.label_id)) as r (r.label_id)}
							<button class="chip" aria-pressed={partFilter.includes(r.label_id)} onclick={() => (partFilter = toggleIn(partFilter, r.label_id))}>
								{r.name}
							</button>
						{:else}
							<span class="muted">No clips in any part yet</span>
						{/each}
					</div>
				</div>
				<div class="filter-group">
					<span class="filter-label" id="filter-status">Status</span>
					<div class="chips" role="group" aria-labelledby="filter-status">
						{#each CLIP_STATUSES as s (s.value)}
							<button class="chip" aria-pressed={statusFilter.includes(s.value)} onclick={() => (statusFilter = toggleIn(statusFilter, s.value))}>
								{s.label}
							</button>
						{/each}
					</div>
				</div>
				{#if songRoles.length > 0}
					<div class="filter-group">
						<span class="filter-label" id="filter-role">Role</span>
						<div class="chips" role="group" aria-labelledby="filter-role">
							{#each songRoles as r (r)}
								<button class="chip" aria-pressed={roleFilter.includes(r.toLowerCase())} onclick={() => (roleFilter = toggleIn(roleFilter, r.toLowerCase()))}>
									{r}
								</button>
							{/each}
							{#if anyWithoutRole}
								<button class="chip" aria-pressed={roleFilter.includes('')} onclick={() => (roleFilter = toggleIn(roleFilter, ''))}>No role</button>
							{/if}
						</div>
					</div>
				{/if}
				<div class="filter-group">
					<span class="filter-label">Recorded</span>
					<div class="dates">
						<label>
							<span class="muted">From</span>
							<input class="field" type="date" bind:value={fromDate} max={toDate || undefined} />
						</label>
						<label>
							<span class="muted">To</span>
							<input class="field" type="date" bind:value={toDate} min={fromDate || undefined} />
						</label>
					</div>
				</div>
				<div class="filter-actions">
					{#if filterCount > 0}<button class="btn btn-sm btn-ghost" onclick={clearFilters}>Clear filters</button>{/if}
					<button class="btn btn-sm" onclick={() => (filterOpen = false)}>Close</button>
				</div>
			</div>
		{:else if filterCount > 0 && !arranging}
			<p class="filter-summary">
				<Icon name="filter" size={15} /> <span>{filterSummary}</span>
				<button class="link" onclick={clearFilters}>Clear</button>
			</p>
		{/if}

		{#if selecting}
			<p class="select-hint">Tap clips to select them, then pick what to do below.</p>
		{/if}

		{#if showUnlabeled}
			<section class="row">
				<div class="row-head">
					<span class="row-title unlabeled">No part yet <span class="count">{unlabeled.length}</span></span>
				</div>
				<ClipStrip clips={unlabeled} onplay={play} onmenu={selecting ? undefined : openMenu} selected={selecting ? selected : undefined} />
			</section>
		{/if}

		{#if !arranging && visibleRows.length === 0 && !showUnlabeled}
			{#if project.clip_count === 0}
				<div class="empty">
					<div class="empty-icon"><Icon name="music" size={26} /></div>
					<h2>No clips in this song yet</h2>
					<p class="muted">Each part of the song gets its own row of recordings, newest on the left.</p>
					<div class="empty-actions">
						<button class="btn btn-primary" onclick={() => uploadTo(null)}><Icon name="upload" size={18} /> Upload recordings</button>
						<button class="btn" onclick={() => (showEmpty = true)}>Show all parts</button>
					</div>
				</div>
			{:else}
				<div class="empty">
					<h2>No clips match</h2>
					<p class="muted">
						{#if filterCount > 0}Try fewer filters.{:else}Every clip in this song is discarded.{/if}
					</p>
					{#if filterCount > 0}
						<button class="btn" onclick={clearFilters}>Clear filters</button>
					{:else}
						<button class="btn" onclick={() => (hideDiscarded = false)}>Show discarded</button>
					{/if}
				</div>
			{/if}
		{/if}

		{#each visibleRows as { row, clips }, i (row.label_id)}
			<section class="row" class:collapsed={row.collapsed && !arranging}>
				<div class="row-head">
					{#if arranging}
						<span class="row-title">
							{row.name}
							{#if row.is_full_song}<span class="pinned">Pinned</span>{/if}
						</span>
						<div class="arrange">
							{#if !row.is_full_song}
								<button class="btn btn-sm btn-icon" aria-label="Move {row.name} up" disabled={i <= 1} onclick={() => move(row, -1)}>
									<Icon name="arrow-up" size={18} />
								</button>
								<button class="btn btn-sm btn-icon" aria-label="Move {row.name} down" disabled={i === visibleRows.length - 1} onclick={() => move(row, 1)}>
									<Icon name="arrow-down" size={18} />
								</button>
							{/if}
							{#if row.is_custom}
								<button class="btn btn-sm btn-icon btn-danger" aria-label="Delete part {row.name}" disabled={row.clips.length > 0} title={row.clips.length > 0 ? 'Move its clips first' : undefined} onclick={() => deletePart(row)}>
									<Icon name="trash" size={18} />
								</button>
							{/if}
						</div>
					{:else}
						{#if renamingRow === row.label_id}
							<form class="row-rename" onsubmit={(e) => saveRowRename(e, row)}>
								<label class="visually-hidden" for="row-name-{row.label_id}">Part name in this song</label>
								<!-- svelte-ignore a11y_autofocus -->
								<input
									id="row-name-{row.label_id}"
									class="field"
									maxlength="60"
									bind:value={rowNameDraft}
									onkeydown={(e) => e.key === 'Escape' && (renamingRow = null)}
									autofocus
								/>
								<button class="btn btn-primary btn-sm" type="submit" disabled={!rowNameDraft.trim()}>Save</button>
								<button class="btn btn-ghost btn-sm" type="button" onclick={() => (renamingRow = null)}>Cancel</button>
							</form>
						{:else}
							<button class="row-title toggle-row" aria-expanded={!row.collapsed} onclick={() => toggleCollapsed(row)}>
								<span class="chev"><Icon name="chevron-down" size={18} /></span>
								{row.name}
								<span class="count">
									{clips.length}{#if clips.length !== row.clips.length}<span class="of">&nbsp;of {row.clips.length}</span>{/if}
								</span>
							</button>
							<div class="row-actions">
								<button class="btn btn-ghost btn-sm btn-icon add" aria-label="Upload to {row.name}" title="Upload to {row.name}" onclick={() => uploadTo(row)}>
									<Icon name="plus" size={18} />
								</button>
								<div class="menu-wrap" id="row-menu-{row.label_id}">
									<button
										class="btn btn-ghost btn-sm btn-icon add"
										aria-label="More for {row.name}"
										aria-haspopup="menu"
										aria-expanded={rowMenu === row.label_id}
										onclick={() => (rowMenu = rowMenu === row.label_id ? null : row.label_id)}
									>
										<Icon name="more" size={18} />
									</button>
									{#if rowMenu === row.label_id}
										<div class="menu" role="menu">
											<a role="menuitem" href="/record?song={project.id}&part={row.label_id}">
												<Icon name="record" size={18} /> Record into {row.name}
											</a>
											{#if !row.is_full_song}
												<button role="menuitem" onclick={() => addAnother(row)}>
													<Icon name="plus" size={18} /> Add {project ? nextPartName(project.rows.map((r) => r.name), row.name) : 'another'}
												</button>
												<button role="menuitem" onclick={() => startRowRename(row)}>
													<Icon name="edit" size={18} /> Rename in this song
												</button>
											{/if}
											{#if row.is_custom && row.clips.length === 0}
												<button role="menuitem" class="danger" onclick={() => deletePart(row)}>
													<Icon name="trash" size={18} /> Delete part
												</button>
											{/if}
										</div>
									{/if}
								</div>
							</div>
						{/if}
					{/if}
				</div>
				{#if !row.collapsed && !arranging}
					{#if clips.length > 0}
						<ClipStrip {clips} onplay={play} onmenu={selecting ? undefined : openMenu} selected={selecting ? selected : undefined} />
					{:else if row.clips.length > 0}
						<div class="empty-strip">{plural(row.clips.length, 'discarded take')} hidden</div>
					{:else}
						<div class="empty-strip">No clips yet</div>
					{/if}
				{/if}
			</section>
		{/each}

		{#if !arranging && filterCount === 0}
			{#if addingPart}
				<form class="add-part" onsubmit={addPart}>
					<label class="visually-hidden" for="part-name">Part name</label>
					<!-- svelte-ignore a11y_autofocus -->
					<input
						id="part-name"
						class="field"
						placeholder="Part name, e.g. Breakdown or Verse 2"
						maxlength="60"
						bind:value={partName}
						onkeydown={(e) => e.key === 'Escape' && (addingPart = false)}
						autofocus
					/>
					<button class="btn btn-primary btn-sm" type="submit" disabled={!partName.trim()}>Add</button>
					<button class="btn btn-ghost btn-sm" type="button" onclick={() => (addingPart = false)}>Cancel</button>
				</form>
			{:else}
				<button class="add-part-button" onclick={() => (addingPart = true)}>
					<Icon name="plus" size={16} /> Add a part
				</button>
			{/if}
		{/if}
	{/if}
</div>

{#if openId !== null}
	<ClipPanel clipId={openId} onclose={() => (openId = null)} onchange={clipChanged} />
{/if}

{#if menuFor && project}
	<ClipMenu
		clip={menuFor.clip}
		at={menuFor.at}
		parts={project.rows.map((r) => ({ id: r.label_id, name: r.name }))}
		onclose={() => (menuFor = null)}
		onchange={clipChanged}
		onselect={() => menuFor && startSelect(menuFor.clip.id)}
	/>
{/if}

{#if selecting && project}
	<div class="bulk-bar" role="toolbar" aria-label="Selected clips">
		{#if bulkMenu}
			<div class="bulk-pop">
				{#if bulkMenu === 'song'}
					<p class="pop-title">Move {plural(selected.size, 'clip')} to</p>
					{#if !bulkProjects}
						<p class="muted">Loading…</p>
					{:else}
						<div class="pop-list">
							{#each bulkProjects.filter((p) => p.id !== project?.id) as p (p.id)}
								<button disabled={bulkBusy} onclick={() => bulk({ project_id: p.id })}>
									{p.name}{#if p.archived}<span class="muted"> (archived)</span>{/if}
								</button>
							{/each}
							<button class="muted-item" disabled={bulkBusy} onclick={() => bulk({ project_id: null })}>
								<Icon name="inbox" size={17} /> Uncategorized
							</button>
						</div>
					{/if}
				{:else if bulkMenu === 'parts'}
					<p class="pop-title">Add to part</p>
					<div class="chips">
						{#each project.rows as r (r.label_id)}
							<button class="chip" disabled={bulkBusy} onclick={() => bulk({ add_label_ids: [r.label_id] })}>
								<Icon name="plus" size={13} /> {r.name}
							</button>
						{/each}
					</div>
					<p class="pop-title">Role</p>
					<div class="chips">
						{#each roleOptions(allClips.map((c) => c.role)) as r (r)}
							<button class="chip" disabled={bulkBusy} onclick={() => bulk({ role: r })}>{r}</button>
						{/each}
						<button class="chip" disabled={bulkBusy} onclick={() => bulk({ role: null })}>No role</button>
					</div>
					{#if selectedParts.length > 0}
						<p class="pop-title">Take out of part</p>
						<div class="chips">
							{#each selectedParts as r (r.label_id)}
								<button class="chip" disabled={bulkBusy} onclick={() => bulk({ remove_label_ids: [r.label_id] })}>
									<Icon name="minus" size={13} /> {r.name}
								</button>
							{/each}
						</div>
					{/if}
				{:else}
					<p class="pop-title">Set status</p>
					<div class="chips">
						{#each CLIP_STATUSES.filter((s) => s.value !== 'current') as s (s.value)}
							<button class="chip" disabled={bulkBusy} onclick={() => bulk({ status: s.value })}>{s.label}</button>
						{/each}
					</div>
					<p class="pop-note muted">Current is set one take at a time, since each part has one.</p>
				{/if}
			</div>
		{/if}

		<div class="bulk-main">
			<span class="bulk-count" aria-live="polite">{selected.size} selected</span>
			<button class="btn btn-sm btn-ghost" onclick={selectAll} disabled={shownIds.size === 0}>
				{allShownSelected ? 'Select none' : 'Select all'}
			</button>
			<div class="bulk-actions">
				<button class="btn btn-sm" aria-expanded={bulkMenu === 'song'} disabled={selected.size === 0} onclick={() => openBulkMenu('song')}>
					<Icon name="folder" size={16} /> <span class="lbl">Move</span>
				</button>
				<button class="btn btn-sm" aria-expanded={bulkMenu === 'parts'} disabled={selected.size === 0} onclick={() => openBulkMenu('parts')}>
					<Icon name="tag" size={16} /> <span class="lbl">Parts</span>
				</button>
				<button class="btn btn-sm" aria-expanded={bulkMenu === 'status'} disabled={selected.size === 0} onclick={() => openBulkMenu('status')}>
					<Icon name="star" size={16} /> <span class="lbl">Status</span>
				</button>
				<button class="btn btn-sm btn-danger" aria-label="Move to Trash" title="Move to Trash" disabled={selected.size === 0 || bulkBusy} onclick={bulkTrash}>
					<Icon name="trash" size={16} />
				</button>
			</div>
			<button class="btn btn-sm btn-primary done" onclick={endSelect}>Done</button>
		</div>
	</div>
{/if}

<style>
	.page {
		max-width: 1400px;
		margin: 0 auto;
		padding: 0.9rem 0 1.5rem;
	}
	@media (min-width: 1024px) {
		.with-panel {
			margin-right: 460px;
		}
	}
	.add {
		color: var(--text-muted);
	}
	.empty-actions {
		display: flex;
		flex-wrap: wrap;
		justify-content: center;
		gap: 0.5rem;
	}
	.back {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
		margin: 0 var(--gutter) 0.6rem;
		color: var(--text-muted);
		font-size: 0.9rem;
		font-weight: 550;
	}
	.back:hover {
		color: var(--text);
	}
	.loading {
		height: 50vh;
	}
	.page > .error {
		padding: 0 var(--gutter);
	}

	.head {
		padding: 0 var(--gutter);
	}
	.title-row {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 0.75rem;
	}
	h1 {
		font-size: 1.65rem;
		overflow-wrap: anywhere;
		padding-top: 0.15rem;
	}
	.rename {
		display: flex;
		flex-wrap: wrap;
		gap: 0.4rem;
		flex: 1;
	}
	.rename .field {
		flex: 1 1 220px;
		font-size: 1.15rem;
		font-weight: 600;
	}
	.head-actions {
		display: flex;
		gap: 0.4rem;
		flex-shrink: 0;
	}
	select.status {
		min-height: 38px;
		font-weight: 600;
		font-size: 0.9rem;
	}
	.meta {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		margin: 0.3rem 0 0;
		color: var(--text-muted);
		font-size: 0.9rem;
	}

	.menu-wrap {
		position: relative;
	}
	.menu {
		position: absolute;
		right: 0;
		top: calc(100% + 6px);
		z-index: 10;
		min-width: 190px;
		padding: 0.35rem;
		border-radius: var(--radius);
		background: var(--surface-2);
		border: 1px solid var(--border);
		box-shadow: 0 16px 40px rgb(0 0 0 / 0.45);
	}
	.menu button {
		display: flex;
		align-items: center;
		gap: 0.6rem;
		width: 100%;
		padding: 0.55rem 0.65rem;
		border: 0;
		border-radius: var(--radius-sm);
		background: transparent;
		text-align: left;
		cursor: pointer;
		font-size: 0.95rem;
	}
	.menu button:hover {
		background: var(--surface-3);
	}
	.menu .danger {
		color: var(--danger);
	}
	.menu a {
		display: flex;
		align-items: center;
		gap: 0.6rem;
		padding: 0.55rem 0.65rem;
		border-radius: var(--radius-sm);
		font-size: 0.95rem;
	}
	.menu a:hover {
		background: var(--surface-3);
	}
	.row-actions {
		display: flex;
		align-items: center;
	}
	.row-actions .menu {
		min-width: 230px;
	}
	.row-rename {
		display: flex;
		flex-wrap: wrap;
		gap: 0.35rem;
		flex: 1;
		max-width: 480px;
	}
	.row-rename .field {
		flex: 1 1 180px;
		min-height: 34px;
		padding: 0.3rem 0.6rem;
		font-weight: 650;
	}
	.add-part-button {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
		margin: 0.6rem var(--gutter) 0;
		padding: 0.45rem 0.85rem;
		border: 1px dashed var(--border);
		border-radius: var(--radius-sm);
		background: transparent;
		color: var(--text-muted);
		font-size: 0.9rem;
		font-weight: 600;
		cursor: pointer;
	}
	.add-part-button:hover {
		color: var(--text);
		border-color: var(--text-faint);
	}

	.quick-links {
		display: flex;
		flex-wrap: wrap;
		gap: 0.4rem;
		margin-top: 0.75rem;
	}
	.quick-links .muted {
		font-weight: 500;
	}

	.banner {
		margin: 0.75rem var(--gutter) 0;
	}

	.toolbar {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		justify-content: space-between;
		gap: 0.6rem;
		margin: 1.1rem var(--gutter) 0.4rem;
		padding-top: 0.9rem;
		border-top: 1px solid var(--border);
	}
	.toolbar-actions {
		display: flex;
		align-items: center;
		gap: 0.4rem;
	}
	.record :global(svg) {
		color: #ff6b6b;
		fill: currentColor;
	}
	@media (max-width: 480px) {
		.record .lbl {
			display: none;
		}
	}
	.toolbar .active {
		background: var(--accent-soft);
		border-color: rgb(240 166 70 / 0.45);
		color: var(--accent-strong);
	}
	.add-part {
		display: flex;
		gap: 0.4rem;
		margin: 0.6rem var(--gutter) 0.6rem;
		max-width: 480px;
	}

	.row {
		padding: 0.55rem 0 0.4rem;
	}
	.row-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		min-height: 40px;
		padding: 0 var(--gutter);
	}
	.row-title {
		display: inline-flex;
		align-items: center;
		gap: 0.45rem;
		font-weight: 650;
		font-size: 1.02rem;
	}
	.toggle-row {
		padding: 0.3rem 0.4rem 0.3rem 0.2rem;
		margin-left: -0.2rem;
		border: 0;
		border-radius: var(--radius-sm);
		background: transparent;
		cursor: pointer;
	}
	.toggle-row:hover {
		background: var(--surface);
	}
	.chev {
		display: grid;
		color: var(--text-faint);
		transition: rotate 0.15s;
	}
	.collapsed .chev {
		rotate: -90deg;
	}
	.count {
		min-width: 1.5rem;
		padding: 0 0.4rem;
		border-radius: 999px;
		background: var(--surface-2);
		color: var(--text-muted);
		font-size: 0.75rem;
		font-weight: 650;
		text-align: center;
	}
	.unlabeled {
		color: var(--accent-strong);
	}
	.pinned {
		font-size: 0.72rem;
		font-weight: 600;
		color: var(--text-faint);
	}
	.arrange {
		display: flex;
		gap: 0.35rem;
	}
	.empty-strip {
		display: grid;
		place-items: center;
		height: 64px;
		margin: 0.15rem var(--gutter) 0.2rem;
		border: 1px dashed var(--border);
		border-radius: 10px;
		color: var(--text-faint);
		font-size: 0.875rem;
	}

	.empty {
		display: grid;
		justify-items: center;
		gap: 0.55rem;
		margin: 1rem var(--gutter);
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
		max-width: 38ch;
	}
	.empty-icon {
		display: grid;
		place-items: center;
		width: 52px;
		height: 52px;
		border-radius: 15px;
		background: var(--accent-soft);
		color: var(--accent);
	}

	.page.selecting {
		padding-bottom: 9rem;
	}
	.toggles {
		display: flex;
		flex-wrap: wrap;
		gap: 0.35rem 1.1rem;
	}
	.n {
		min-width: 1.2rem;
		padding: 0 0.3rem;
		border-radius: 999px;
		background: var(--accent);
		color: var(--accent-ink);
		font-size: 0.72rem;
		font-weight: 700;
		text-align: center;
	}

	/* --- filters --- */
	.filters {
		display: grid;
		gap: 0.75rem;
		margin: 0.4rem var(--gutter) 0.6rem;
		padding: 0.85rem 0.95rem;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--surface);
	}
	.filter-group {
		display: grid;
		grid-template-columns: 5.5rem minmax(0, 1fr);
		align-items: start;
		gap: 0.5rem;
	}
	.filter-label {
		padding-top: 0.35rem;
		font-size: 0.78rem;
		font-weight: 650;
		color: var(--text-muted);
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}
	@media (max-width: 560px) {
		.filter-group {
			grid-template-columns: minmax(0, 1fr);
			gap: 0.3rem;
		}
		.filter-label {
			padding-top: 0;
		}
	}
	.chips {
		display: flex;
		flex-wrap: wrap;
		gap: 0.35rem;
	}
	.chip {
		display: inline-flex;
		align-items: center;
		gap: 0.25rem;
		padding: 0.3rem 0.75rem;
		border-radius: 999px;
		border: 1px solid var(--border);
		background: transparent;
		color: var(--text-muted);
		font-size: 0.86rem;
		font-weight: 600;
		cursor: pointer;
	}
	.chip:hover:not(:disabled) {
		color: var(--text);
	}
	.chip:disabled {
		opacity: 0.5;
		cursor: default;
	}
	.chip[aria-pressed='true'] {
		background: var(--accent-soft);
		border-color: rgb(240 166 70 / 0.45);
		color: var(--accent-strong);
	}
	.dates {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem 0.9rem;
	}
	.dates label {
		display: flex;
		align-items: center;
		gap: 0.45rem;
		font-size: 0.88rem;
	}
	.dates .field {
		width: auto;
		min-height: 34px;
		padding: 0.25rem 0.5rem;
	}
	.filter-actions {
		display: flex;
		justify-content: flex-end;
		gap: 0.4rem;
	}
	.filter-summary {
		display: flex;
		align-items: center;
		gap: 0.45rem;
		margin: 0.25rem var(--gutter) 0.4rem;
		color: var(--accent-strong);
		font-size: 0.88rem;
		font-weight: 600;
	}
	.filter-summary span {
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.link {
		flex-shrink: 0;
		border: 0;
		padding: 0;
		background: none;
		color: var(--text-muted);
		font-size: inherit;
		text-decoration: underline;
		cursor: pointer;
	}
	.of {
		font-weight: 500;
		color: var(--text-faint);
	}
	.select-hint {
		margin: 0.25rem var(--gutter) 0.3rem;
		color: var(--accent-strong);
		font-size: 0.88rem;
	}

	/* --- bulk select bar, pinned to the bottom --- */
	.bulk-bar {
		position: fixed;
		left: 0;
		right: 0;
		bottom: 0;
		z-index: 35;
		padding: 0.6rem var(--gutter) calc(0.6rem + env(safe-area-inset-bottom));
		background: rgb(24 27 33 / 0.97);
		backdrop-filter: blur(10px);
		border-top: 1px solid var(--border);
		box-shadow: 0 -12px 30px rgb(0 0 0 / 0.35);
	}
	.bulk-main {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.4rem;
		max-width: 1400px;
		margin: 0 auto;
	}
	.bulk-count {
		min-width: 6.2rem;
		font-weight: 650;
	}
	.bulk-actions {
		display: flex;
		gap: 0.35rem;
		margin-left: auto;
	}
	.bulk-actions [aria-expanded='true'] {
		background: var(--accent-soft);
		border-color: rgb(240 166 70 / 0.45);
		color: var(--accent-strong);
	}
	.bulk-pop {
		position: absolute;
		right: var(--gutter);
		bottom: calc(100% + 8px);
		display: grid;
		gap: 0.5rem;
		width: min(420px, calc(100vw - 2 * var(--gutter)));
		max-height: 55vh;
		overflow-y: auto;
		padding: 0.75rem;
		border-radius: var(--radius);
		background: var(--surface-2);
		border: 1px solid var(--border);
		box-shadow: 0 16px 40px rgb(0 0 0 / 0.5);
	}
	.pop-title {
		margin: 0;
		font-size: 0.78rem;
		font-weight: 650;
		color: var(--text-muted);
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}
	.pop-list button {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		width: 100%;
		padding: 0.55rem 0.6rem;
		border: 0;
		border-radius: var(--radius-sm);
		background: transparent;
		text-align: left;
		cursor: pointer;
		font-size: 0.95rem;
	}
	.pop-list button:hover:not(:disabled) {
		background: var(--surface-3);
	}
	.muted-item {
		color: var(--text-muted);
	}
	.pop-note {
		margin: 0;
		font-size: 0.82rem;
	}
	@media (max-width: 560px) {
		.bulk-actions .lbl {
			display: none;
		}
		.bulk-count {
			min-width: 0;
		}
	}
</style>
