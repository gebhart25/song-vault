<script lang="ts">
	import { goto } from '$app/navigation';
	import { ApiError, api } from '#lib/api.ts';
	import { shortDate } from '#lib/format.ts';
	import { roleOptions, sameRole } from '#lib/roles.ts';
	import type { ClipDetail, ClipSummary, ProjectSummary } from '#lib/types.ts';
	import type { MenuAt } from './ClipTile.svelte';
	import Icon from './Icon.svelte';

	// The long-press / right-click menu on a song page tile (spec 4.3): a popover at
	// the pointer on desktop, a bottom sheet on phones.
	let {
		clip,
		at,
		parts,
		onclose,
		onchange,
		onselect
	}: {
		clip: ClipSummary;
		at: MenuAt;
		/** The song's parts, in song-page order */
		parts: { id: number; name: string }[];
		onclose: () => void;
		/** Something about the clip changed: reload the page */
		onchange: () => void;
		/** Start picking clips for a bulk change, with this one picked */
		onselect?: () => void;
	} = $props();

	type View = 'main' | 'parts' | 'role' | 'song' | 'notes';
	const TITLES: Record<Exclude<View, 'main'>, string> = {
		parts: 'Parts',
		role: 'Role',
		song: 'Move to song',
		notes: 'Notes'
	};

	let view = $state<View>('main');
	let detail = $state<ClipDetail | null>(null);
	let projects = $state<ProjectSummary[] | null>(null);
	let usedRoles = $state<string[] | null>(null);
	let roleDraft = $state('');
	let notesDraft = $state('');
	let error = $state('');
	let busy = $state(false);

	let menu: HTMLDivElement | undefined = $state();
	let width = $state(0);
	let height = $state(0);

	const name = $derived(clip.title || shortDate(clip.recorded_at ?? clip.uploaded_at));
	// Keep the popover on screen: open to the left / above the pointer when there's no room.
	const left = $derived(Math.max(8, Math.min(at.x, window.innerWidth - width - 8)));
	const top = $derived(at.y + height > window.innerHeight - 8 ? Math.max(8, at.y - height) : at.y);

	$effect(() => {
		const id = clip.id;
		api
			.get<ClipDetail>(`/clips/${id}`)
			.then((d) => {
				detail = d;
				notesDraft = d.notes;
			})
			.catch(() => (error = 'Could not load this clip.'));
	});

	// Move focus into the menu (and to each view's first control) for keyboard users.
	$effect(() => {
		void view;
		if (!menu) return;
		const target = menu.querySelector<HTMLElement>('[data-autofocus]') ?? menu.querySelector<HTMLElement>('button:not(:disabled)');
		target?.focus();
	});

	function go(next: View) {
		error = '';
		view = next;
		if (next === 'role' && !usedRoles) {
			api.get<string[]>('/clips/roles')
				.then((r) => (usedRoles = r))
				.catch(() => (usedRoles = []));
		}
		if (next === 'song' && !projects) {
			api.get<ProjectSummary[]>('/projects?include_archived=true')
				.then((p) => (projects = p))
				.catch(() => (error = 'Could not load your songs.'));
		}
	}

	async function act(action: () => Promise<unknown>, close = true) {
		if (busy) return;
		busy = true;
		error = '';
		try {
			await action();
			onchange();
			if (close) onclose();
		} catch (e) {
			error = e instanceof ApiError ? e.message : 'Something went wrong.';
		} finally {
			busy = false;
		}
	}

	const patch = (changes: Record<string, unknown>) => api.patch<ClipDetail>(`/clips/${clip.id}`, changes);

	function toggleCurrent() {
		act(() => patch({ status: clip.status === 'current' ? 'idea' : 'current' }));
	}

	function togglePart(id: number) {
		if (!detail) return;
		const d = detail;
		const ids = d.label_ids.includes(id) ? d.label_ids.filter((x) => x !== id) : [...d.label_ids, id];
		act(async () => (detail = await patch({ label_ids: ids })), false);
	}

	/** One Current take per part and role (spec 5.1). */
	function setRole(role: string | null) {
		act(() => patch({ role }));
	}

	function useOtherRole(event: SubmitEvent) {
		event.preventDefault();
		if (roleDraft.trim()) setRole(roleDraft.trim());
	}

	function moveTo(projectId: number | null) {
		act(() => patch({ project_id: projectId }));
	}

	function saveNotes(event: SubmitEvent) {
		event.preventDefault();
		act(() => patch({ notes: notesDraft }));
	}

	function trash() {
		if (!confirm('Move this clip to Trash? You can restore it from Trash for 30 days.')) return;
		act(() => api.del(`/clips/${clip.id}`));
	}

	function onkeydown(e: KeyboardEvent) {
		if (e.key !== 'Escape') return;
		e.stopPropagation();
		if (view === 'main') onclose();
		else go('main');
	}

	// The finger that long-pressed the tile is still down when the sheet opens under it,
	// and lifting it sends a click to whatever is there now (a menu item, or the backdrop).
	// So clicks only count once a new press has started on the menu or the backdrop.
	// Keyboard clicks (detail 0) always count.
	let armed = false;
	const arm = () => (armed = true);
	function guard(e: MouseEvent) {
		if (armed || e.detail === 0) return;
		e.preventDefault();
		e.stopPropagation();
	}

	/** Read before closing: closing clears the parent's state, and `clip` with it. */
	function openEditor() {
		const href = `/clips/${clip.id}/edit`;
		onclose();
		void goto(href);
	}
</script>

<svelte:window {onkeydown} />

<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
<div
	class="backdrop"
	class:dim={at.sheet}
	onpointerdown={arm}
	onclick={() => armed && onclose()}
	oncontextmenu={(e) => (e.preventDefault(), armed && onclose())}
></div>

<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
<div
	class="menu"
	class:sheet={at.sheet}
	role="dialog"
	tabindex="-1"
	aria-label="Clip actions"
	bind:this={menu}
	bind:clientWidth={width}
	bind:clientHeight={height}
	style:left={at.sheet ? undefined : `${left}px`}
	style:top={at.sheet ? undefined : `${top}px`}
	onpointerdowncapture={arm}
	onclickcapture={guard}
	oncontextmenu={(e) => e.preventDefault()}
>
	{#if view === 'main'}
		<p class="menu-title">{name}</p>
		<div role="menu" aria-label="Clip actions">
			<button role="menuitem" onclick={toggleCurrent} disabled={busy}>
				<Icon name="star" size={18} />
				{clip.status === 'current' ? 'Not current' : 'Set as current'}
			</button>
			<button role="menuitem" onclick={() => go('parts')} disabled={!detail}>
				<Icon name="tag" size={18} /> Edit parts <span class="more"><Icon name="chevron-right" size={16} /></span>
			</button>
			<button role="menuitem" onclick={openEditor}>
				<Icon name="scissors" size={18} /> Trim &amp; split
			</button>
			<button role="menuitem" onclick={() => go('role')}>
				<Icon name="user" size={18} /> Role
				{#if clip.role}<span class="current-role">{clip.role}</span>{/if}
				<span class="more"><Icon name="chevron-right" size={16} /></span>
			</button>
			<button role="menuitem" onclick={() => go('song')}>
				<Icon name="folder" size={18} /> Move to song <span class="more"><Icon name="chevron-right" size={16} /></span>
			</button>
			<button role="menuitem" onclick={() => go('notes')} disabled={!detail}>
				<Icon name="notes" size={18} /> Notes
				{#if detail?.notes}<span class="has-notes" aria-label="(has notes)">•</span>{/if}
				<span class="more"><Icon name="chevron-right" size={16} /></span>
			</button>
			{#if onselect}
				<button role="menuitem" onclick={() => (onselect?.(), onclose())}>
					<Icon name="select" size={18} /> Select more…
				</button>
			{/if}
			<button role="menuitem" class="danger" onclick={trash} disabled={busy}>
				<Icon name="trash" size={18} /> Move to Trash
			</button>
		</div>
	{:else}
		<div class="sub-head">
			<button class="btn btn-ghost btn-sm btn-icon" aria-label="Back" onclick={() => go('main')}>
				<Icon name="chevron-left" size={18} />
			</button>
			<span>{TITLES[view]}</span>
		</div>

		{#if view === 'parts' && detail}
			<div class="chips" role="group" aria-label="Parts">
				{#each parts as p (p.id)}
					<button class="chip" aria-pressed={detail.label_ids.includes(p.id)} disabled={busy} onclick={() => togglePart(p.id)}>
						{p.name}
					</button>
				{/each}
			</div>
			<button class="btn btn-sm done" onclick={onclose}>Done</button>
		{:else if view === 'role'}
			<div class="chips" role="group" aria-label="Role">
				{#each roleOptions([...(usedRoles ?? []), clip.role]) as r (r)}
					<button class="chip" aria-pressed={sameRole(clip.role, r)} disabled={busy} onclick={() => setRole(r)}>{r}</button>
				{/each}
				{#if clip.role}
					<button class="chip" disabled={busy} onclick={() => setRole(null)}>No role</button>
				{/if}
			</div>
			<form class="other-role" onsubmit={useOtherRole}>
				<label class="visually-hidden" for="menu-role">Another role</label>
				<input id="menu-role" class="field" placeholder="Another role" maxlength="40" bind:value={roleDraft} />
				<button class="btn btn-sm" type="submit" disabled={busy || !roleDraft.trim()}>Set</button>
			</form>
		{:else if view === 'song'}
			{#if !projects}
				<p class="muted pad">Loading…</p>
			{:else}
				<div class="list" role="menu" aria-label="Move to song">
					{#each projects.filter((p) => p.id !== detail?.project_id) as p (p.id)}
						<button role="menuitem" onclick={() => moveTo(p.id)} disabled={busy}>
							{p.name}{#if p.archived}<span class="muted"> (archived)</span>{/if}
						</button>
					{/each}
					<button role="menuitem" class="muted-item" onclick={() => moveTo(null)} disabled={busy}>
						<Icon name="inbox" size={18} /> Uncategorized
					</button>
				</div>
			{/if}
		{:else if view === 'notes'}
			<form class="notes" onsubmit={saveNotes}>
				<label class="visually-hidden" for="menu-notes">Notes for this clip</label>
				<textarea id="menu-notes" class="field" data-autofocus placeholder="Try this with a palm-muted verse…" bind:value={notesDraft}></textarea>
				<div class="notes-actions">
					<button class="btn btn-sm btn-ghost" type="button" onclick={() => go('main')}>Cancel</button>
					<button class="btn btn-sm btn-primary" type="submit" disabled={busy || notesDraft === detail?.notes}>Save</button>
				</div>
			</form>
		{/if}
	{/if}
	{#if error}<p class="error pad" role="alert">{error}</p>{/if}
</div>

<style>
	.backdrop {
		position: fixed;
		inset: 0;
		z-index: 40;
	}
	.backdrop.dim {
		background: rgb(0 0 0 / 0.5);
	}
	.menu {
		position: fixed;
		z-index: 41;
		width: 250px;
		max-height: min(480px, calc(100dvh - 16px));
		overflow-y: auto;
		padding: 0.35rem;
		border-radius: var(--radius);
		background: var(--surface-2);
		border: 1px solid var(--border);
		box-shadow: 0 16px 40px rgb(0 0 0 / 0.5);
	}
	.menu.sheet {
		inset: auto 0 0 0;
		width: auto;
		max-height: 75dvh;
		padding: 0.5rem 0.6rem calc(0.8rem + env(safe-area-inset-bottom));
		border-radius: 16px 16px 0 0;
		border-bottom: 0;
	}
	.menu-title {
		margin: 0;
		padding: 0.35rem 0.65rem 0.4rem;
		color: var(--text-faint);
		font-size: 0.8rem;
		font-weight: 650;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	[role='menu'] button {
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
	.sheet [role='menu'] button {
		padding: 0.75rem 0.65rem;
		font-size: 1rem;
	}
	[role='menu'] button:hover:not(:disabled),
	[role='menu'] button:focus-visible {
		background: var(--surface-3);
	}
	[role='menu'] button:disabled {
		opacity: 0.5;
		cursor: default;
	}
	.more {
		display: grid;
		margin-left: auto;
		color: var(--text-faint);
	}
	.has-notes {
		color: var(--accent);
	}
	.danger {
		color: var(--danger);
	}
	.muted-item {
		color: var(--text-muted);
	}
	.sub-head {
		display: flex;
		align-items: center;
		gap: 0.3rem;
		padding: 0.1rem 0.2rem 0.4rem;
		font-weight: 650;
	}
	.chips {
		display: flex;
		flex-wrap: wrap;
		gap: 0.35rem;
		padding: 0.2rem 0.35rem 0.5rem;
	}
	.chip {
		padding: 0.3rem 0.75rem;
		border-radius: 999px;
		border: 1px solid var(--border);
		background: transparent;
		color: var(--text-muted);
		font-size: 0.87rem;
		font-weight: 600;
		cursor: pointer;
	}
	.chip[aria-pressed='true'] {
		background: var(--accent-soft);
		border-color: rgb(240 166 70 / 0.45);
		color: var(--accent-strong);
	}
	.current-role {
		margin-left: auto;
		color: var(--text-faint);
		font-size: 0.85rem;
	}
	.current-role + .more {
		margin-left: 0.2rem;
	}
	.other-role {
		display: flex;
		gap: 0.35rem;
		padding: 0 0.35rem 0.35rem;
	}
	.other-role .field {
		min-height: 34px;
		padding: 0.3rem 0.6rem;
	}
	.done {
		margin: 0 0.35rem 0.3rem auto;
		display: flex;
	}
	.list {
		max-height: 300px;
		overflow-y: auto;
	}
	.notes {
		display: grid;
		gap: 0.45rem;
		padding: 0 0.3rem 0.3rem;
	}
	.notes textarea {
		min-height: 110px;
	}
	.notes-actions {
		display: flex;
		justify-content: flex-end;
		gap: 0.35rem;
	}
	.pad {
		margin: 0;
		padding: 0.4rem 0.65rem;
		font-size: 0.9rem;
	}
</style>
