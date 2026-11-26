<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { auth, refreshAuth } from '#lib/auth.svelte.ts';
	import Icon from '#lib/components/Icon.svelte';
	import NoteItem from '#lib/components/NoteItem.svelte';
	import NoteMenu, { type NoteAction } from '#lib/components/NoteMenu.svelte';
	import NoteSongDialog, { type SongDialogMode } from '#lib/components/NoteSongDialog.svelte';
	import { plural } from '#lib/format.ts';
	import type { MenuAt } from '#lib/longpress.ts';
	import { notebook, type LocalNote } from '#lib/notebook.svelte.ts';
	import { foldText, searchWords, splitPasted, tagsOf } from '#lib/notebook-text.ts';
	import { deleteShare, getShare, listShares } from '#lib/share-store.ts';

	// The Notebook (spec 5.11): lyric lines, rhymes, titles and song ideas, one note each.
	// Capturing is as quick as a phone notepad; tags, stars and search help find them later.
	// Everything works offline; the notebook store keeps it on the device and syncs.

	const PAGE_SIZE = 150;
	const HIDE_USED_KEY = 'songvault.hideUsed';

	// --- the list --------------------------------------------------------------------

	let query = $state('');
	let tagFilter = $state<string | null>(null);
	let starredOnly = $state(false);
	/** Keep the notebook to fresh ideas (spec 5.11); remembered on the device */
	let hideUsed = $state(localStorage.getItem(HIDE_USED_KEY) === '1');
	$effect(() => {
		localStorage.setItem(HIDE_USED_KEY, hideUsed ? '1' : '0');
	});
	/** The song actions need the server */
	const online = $derived(!auth.offline && notebook.state !== 'offline');
	let shown = $state(PAGE_SIZE);
	/** The note being written in the Jot box: kept out of the list until it's done */
	let jotUid = $state<string | null>(null);

	const indexed = $derived(
		notebook.notes.filter((n) => !n.trash).map((n) => ({ note: n, tags: tagsOf(n.body), text: foldText(n.body) }))
	);
	const total = $derived(indexed.filter((i) => i.note.uid !== jotUid).length);
	const tagCounts = $derived.by(() => {
		const counts = new Map<string, number>();
		for (const i of indexed) for (const t of i.tags) counts.set(t, (counts.get(t) ?? 0) + 1);
		if (tagFilter && !counts.has(tagFilter)) counts.set(tagFilter, 0);
		return [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
	});
	const words = $derived(searchWords(query));
	const usedCount = $derived(indexed.filter((i) => i.note.status === 'used').length);
	const filtering = $derived(words.length > 0 || tagFilter !== null || starredOnly || (hideUsed && usedCount > 0));
	const visible = $derived(
		indexed
			.filter(
				(i) =>
					i.note.uid !== jotUid &&
					(!starredOnly || i.note.starred) &&
					(!hideUsed || i.note.status !== 'used') &&
					(!tagFilter || i.tags.includes(tagFilter)) &&
					words.every((w) => i.text.includes(w))
			)
			.map((i) => i.note)
			.sort(
				(a, b) =>
					Number(b.starred) - Number(a.starred) || b.created_at.localeCompare(a.created_at) || a.uid.localeCompare(b.uid)
			)
	);

	$effect(() => {
		void words;
		void tagFilter;
		void starredOnly;
		void hideUsed;
		shown = PAGE_SIZE;
	});

	function clearFilters() {
		query = '';
		tagFilter = null;
		starredOnly = false;
		hideUsed = false;
	}

	// Fresh changes from elsewhere when the page opens.
	$effect(() => {
		if (notebook.loaded) void notebook.sync();
	});

	// The server no longer knows this device: ask for the PIN (the layout sends it to sign-in).
	$effect(() => {
		if (notebook.state === 'signed-out' && !auth.offline) void refreshAuth();
	});

	// --- the Jot box -------------------------------------------------------------------

	let jot: HTMLDivElement | undefined = $state();
	let jotBox: HTMLTextAreaElement | undefined = $state();
	let jotText = $state('');
	let jotFocused = $state(false);
	/** Text shared from another app: saved only on Save */
	let sharing = $state<string | null>(null);
	let jotTimer: ReturnType<typeof setTimeout> | undefined;
	const jotOpen = $derived(jotFocused || jotText !== '' || sharing !== null);

	function onJotInput() {
		if (sharing) return;
		if (!jotUid) {
			// The note exists from the first character typed.
			if (jotText.trim()) jotUid = notebook.create(jotText);
			return;
		}
		clearTimeout(jotTimer);
		jotTimer = setTimeout(saveJot, 600);
	}

	function saveJot() {
		clearTimeout(jotTimer);
		if (jotUid && jotText.trim()) notebook.update(jotUid, { body: jotText });
	}

	/** Done: the note joins the list, and the box is empty for the next one. */
	function finishJot() {
		saveJot();
		if (jotUid && !jotText.trim()) notebook.discard(jotUid);
		jotUid = null;
		jotText = '';
		jotFocused = false;
		const active = document.activeElement as HTMLElement | null;
		if (active && jot?.contains(active)) active.blur();
	}

	function onJotFocusOut(e: FocusEvent) {
		const to = e.relatedTarget as Node | null;
		if (to && jot?.contains(to)) return;
		jotFocused = false;
		// Switching apps keeps the note open; tapping elsewhere on the page closes it.
		if (sharing || !document.hasFocus()) return;
		if (jotUid || jotText) finishJot();
	}

	function onJotKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape' || (e.key === 'Enter' && (e.ctrlKey || e.metaKey))) {
			e.preventDefault();
			if (sharing) saveShared();
			else finishJot();
		}
	}

	// Leaving the page mid-note keeps what was typed.
	$effect(() => () => {
		saveJot();
		if (jotUid && !jotText.trim()) notebook.discard(jotUid);
	});

	// "New note" from the home screen icon: the box, ready to type.
	$effect(() => {
		if (page.url.searchParams.has('new') && jotBox) jotBox.focus();
	});

	// --- text shared from another app (spec 5.11) ------------------------------------

	$effect(() => {
		const id = page.url.searchParams.get('share');
		if (!id) return;
		getShare(id)
			.then((share) => {
				if (!share) return;
				const parts = [share.title.trim(), share.text.trim()].filter(Boolean);
				if (parts.length === 2 && parts[1].includes(parts[0])) parts.shift();
				jotText = parts.join('\n');
				sharing = id;
			})
			.catch(() => {});
	});

	// Text shares nobody saved or cancelled (the app was closed): tidy them up after a day.
	$effect(() => {
		listShares()
			.then((all) => {
				for (const s of all) if (!s.files.length && Date.now() - s.created > 86_400_000) void deleteShare(s.id);
			})
			.catch(() => {});
	});

	async function endShare() {
		if (sharing) await deleteShare(sharing).catch(() => {});
		sharing = null;
		jotText = '';
		await goto('/notebook', { replaceState: true });
	}

	async function saveShared() {
		if (jotText.trim()) {
			notebook.create(jotText.trimEnd());
			say('Saved to the Notebook');
		}
		await endShare();
	}

	// --- menus, toasts -------------------------------------------------------------------

	let menu = $state<{ note: LocalNote; at: MenuAt } | null>(null);
	let pageMenuOpen = $state(false);
	let songDialog = $state<{ note: LocalNote; mode: SongDialogMode } | null>(null);
	let toast = $state<{ text: string; undo?: () => void; href?: string } | null>(null);
	let toastTimer: ReturnType<typeof setTimeout> | undefined;

	function say(text: string, undo?: () => void, href?: string) {
		toast = { text, undo, href };
		clearTimeout(toastTimer);
		toastTimer = setTimeout(() => (toast = null), 6000);
	}

	// --- picking notes to combine (spec 5.11) ------------------------------------------

	let selecting = $state(false);
	/** The picked notes, in the order they were tapped */
	let picked = $state<string[]>([]);

	function startSelecting(first?: string) {
		pageMenuOpen = false;
		selecting = true;
		picked = first ? [first] : [];
	}

	function stopSelecting() {
		selecting = false;
		picked = [];
	}

	function togglePick(uid: string) {
		picked = picked.includes(uid) ? picked.filter((u) => u !== uid) : [...picked, uid];
	}

	function combinePicked() {
		const count = picked.length;
		const undo = notebook.combine(picked);
		if (!undo) return;
		stopSelecting();
		say(`Combined ${count} notes`, () => {
			toast = null;
			undo();
		});
	}

	// Notes that went to Trash elsewhere drop out of the selection.
	$effect(() => {
		const there = new Set(indexed.map((i) => i.note.uid));
		if (picked.some((u) => !there.has(u))) picked = picked.filter((u) => there.has(u));
	});

	function trashNote(uid: string) {
		notebook.trash(uid);
		say('Moved to Trash', async () => {
			toast = null;
			if (!(await notebook.untrash(uid))) say("Couldn't undo while offline. It's in Trash once you're back home.");
		});
	}

	async function copyText(text: string) {
		try {
			await navigator.clipboard.writeText(text);
		} catch {
			// No clipboard access (plain HTTP): the old way.
			const area = document.createElement('textarea');
			area.value = text;
			area.style.position = 'fixed';
			area.style.opacity = '0';
			document.body.append(area);
			area.select();
			document.execCommand('copy');
			area.remove();
		}
	}

	async function act(action: NoteAction, note: LocalNote) {
		if (action === 'star') notebook.update(note.uid, { starred: !note.starred });
		else if (action === 'used') notebook.update(note.uid, { status: note.status === 'used' ? 'fresh' : 'used' });
		else if (action === 'select') startSelecting(note.uid);
		else if (action === 'use' || action === 'lyrics' || action === 'start') songDialog = { note, mode: action };
		else if (action === 'trash') trashNote(note.uid);
		else {
			await copyText(note.body);
			say('Copied');
		}
	}

	function openMenu(note: LocalNote, at: MenuAt) {
		// While picking, a long-press picks too.
		if (selecting) togglePick(note.uid);
		else menu = { note, at };
	}

	function closePageMenu(e: MouseEvent) {
		if (pageMenuOpen && !(e.target as Element).closest('.page-menu')) pageMenuOpen = false;
	}

	// --- Paste notes ---------------------------------------------------------------------

	let pasteOpen = $state(false);
	let pasteText = $state('');
	let pasteSplit = $state(true);
	const pasted = $derived(splitPasted(pasteText, pasteSplit));

	function openPaste() {
		pageMenuOpen = false;
		pasteText = '';
		pasteSplit = true;
		pasteOpen = true;
	}

	function addPasted() {
		const n = notebook.createMany(pasted);
		pasteOpen = false;
		say(`Added ${plural(n, 'note')}`);
	}
</script>

<svelte:window
	onclick={closePageMenu}
	onkeydown={(e) => {
		if (e.key !== 'Escape') return;
		if (pasteOpen) pasteOpen = false;
		else if (selecting && !menu && !songDialog) stopSelecting();
	}}
/>

<div class="page" class:selecting>
	<header class="head">
		<h1>Notebook</h1>
		{#if notebook.loaded && total > 0}<span class="count">{plural(total, 'note')}</span>{/if}
		<div class="page-menu">
			<button
				class="btn btn-ghost btn-sm btn-icon"
				aria-label="More"
				aria-haspopup="menu"
				aria-expanded={pageMenuOpen}
				onclick={() => (pageMenuOpen = !pageMenuOpen)}
			>
				<Icon name="more" />
			</button>
			{#if pageMenuOpen}
				<div class="popover" role="menu">
					<button role="menuitem" onclick={() => startSelecting()}><Icon name="select" size={18} /> Select notes (to combine)</button>
					<button role="menuitem" onclick={openPaste}><Icon name="notes" size={18} /> Paste notes…</button>
				</div>
			{/if}
		</div>
	</header>

	{#if auth.offline || notebook.state === 'offline'}
		<p class="status">
			<Icon name="cloud-off" size={16} />
			<span
				>Offline: notes are kept on this device{#if notebook.waiting > 0}&nbsp;· {plural(notebook.waiting, 'change')} waiting
					to sync{/if}</span
			>
		</p>
	{:else if notebook.state === 'error'}
		<p class="status">
			<Icon name="cloud-off" size={16} />
			<span
				>Couldn't sync just now, trying again shortly{#if notebook.waiting > 0}&nbsp;· {plural(notebook.waiting, 'change')}
					waiting{/if}</span
			>
		</p>
	{/if}

	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div class="jot" class:open={jotOpen} bind:this={jot} onfocusin={() => (jotFocused = true)} onfocusout={onJotFocusOut}>
		<textarea
			bind:this={jotBox}
			bind:value={jotText}
			oninput={onJotInput}
			onkeydown={onJotKeydown}
			placeholder="Jot something…"
			aria-label="New note"
			rows="1"
		></textarea>
		{#if jotOpen}
			<div class="jot-bar">
				{#if sharing}
					<span class="hint">Shared from another app</span>
					<button class="btn btn-sm" onclick={endShare}>Cancel</button>
					<button class="btn btn-sm btn-primary" onclick={saveShared}>Save</button>
				{:else}
					<span class="hint">{jotUid ? 'Saved as you type' : 'A line, a rhyme, an idea…'}</span>
					<button class="btn btn-sm btn-primary" onclick={finishJot}>Done</button>
				{/if}
			</div>
		{/if}
	</div>

	{#if !notebook.loaded}
		<div class="loading" aria-busy="true"></div>
	{:else if total === 0}
		<div class="empty">
			<div class="empty-icon"><Icon name="notes" size={26} /></div>
			<h2>Nothing jotted yet</h2>
			<p class="muted">
				Lyric lines, rhymes, titles, song ideas: one note each, written above. Add <span class="tag">#tags</span> to find
				them later. The Notebook works away from home too.
			</p>
			<button class="btn btn-sm" onclick={openPaste}><Icon name="notes" size={16} /> Paste notes from your phone</button>
		</div>
	{:else}
		<div class="filters">
			<label class="search">
				<Icon name="search" size={17} />
				<input type="search" bind:value={query} placeholder="Search notes" aria-label="Search notes" />
			</label>
			<div class="chips" role="group" aria-label="Filter">
				<button class="chip" aria-pressed={starredOnly} onclick={() => (starredOnly = !starredOnly)}>
					<Icon name="star" size={14} /> Starred
				</button>
				{#if usedCount > 0 || hideUsed}
					<button class="chip" aria-pressed={hideUsed} onclick={() => (hideUsed = !hideUsed)}>
						Hide used<span class="n">{usedCount}</span>
					</button>
				{/if}
				{#each tagCounts as [tag, n] (tag)}
					<button class="chip" aria-pressed={tagFilter === tag} onclick={() => (tagFilter = tagFilter === tag ? null : tag)}>
						#{tag}<span class="n">{n}</span>
					</button>
				{/each}
			</div>
		</div>

		{#if visible.length === 0}
			<div class="none">
				<p class="muted">No notes match.</p>
				<button class="btn btn-sm" onclick={clearFilters}>Clear search and filters</button>
			</div>
		{:else}
			{#if filtering}<p class="matches">{plural(visible.length, 'note')}</p>{/if}
			<ul class="notes">
				{#each visible.slice(0, shown) as note (note.uid)}
					{@const place = picked.indexOf(note.uid)}
					<NoteItem
						{note}
						onmenu={(at) => openMenu(note, at)}
						onemptied={() => trashNote(note.uid)}
						{selecting}
						number={place >= 0 ? place + 1 : null}
						ontoggle={() => togglePick(note.uid)}
					/>
				{/each}
			</ul>
			{#if visible.length > shown}
				<button class="btn show-more" onclick={() => (shown += PAGE_SIZE)}>
					Show more ({visible.length - shown} left)
				</button>
			{/if}
		{/if}
	{/if}
</div>

{#if menu}
	{@const target = menu.note}
	<NoteMenu note={target} at={menu.at} {online} onclose={() => (menu = null)} onaction={(a) => act(a, target)} />
{/if}

{#if songDialog}
	<NoteSongDialog
		note={songDialog.note}
		mode={songDialog.mode}
		onclose={() => (songDialog = null)}
		ondone={(text, href) => say(text, undefined, href)}
	/>
{/if}

{#if selecting}
	<div class="select-bar" role="toolbar" aria-label="Selected notes">
		<span class="select-count">
			{#if picked.length === 0}Tap notes in the order to combine them{:else}{plural(picked.length, 'note')} selected{/if}
		</span>
		<button class="btn btn-sm" onclick={stopSelecting}>Cancel</button>
		<button class="btn btn-sm btn-primary" disabled={picked.length < 2} onclick={combinePicked}>Combine</button>
	</div>
{/if}

{#if pasteOpen}
	<div class="overlay" role="presentation" onclick={(e) => e.target === e.currentTarget && (pasteOpen = false)}>
		<div class="dialog" role="dialog" aria-modal="true" aria-labelledby="paste-title">
			<h2 id="paste-title">Paste notes</h2>
			<p class="muted">
				Paste your whole notepad. Each block of lines between blank lines becomes its own note, in the same order.
			</p>
			<!-- svelte-ignore a11y_autofocus -->
			<textarea class="field" bind:value={pasteText} rows="10" placeholder="Paste here" autofocus></textarea>
			<label class="check">
				<input type="checkbox" bind:checked={pasteSplit} />
				Split at blank lines
			</label>
			<div class="dialog-foot">
				<span class="muted">{pasted.length ? `Makes ${plural(pasted.length, 'note')}` : ''}</span>
				<button class="btn" onclick={() => (pasteOpen = false)}>Cancel</button>
				<button class="btn btn-primary" disabled={pasted.length === 0} onclick={addPasted}>
					Add {pasted.length ? plural(pasted.length, 'note') : 'notes'}
				</button>
			</div>
		</div>
	</div>
{/if}

{#if toast}
	<div class="toast" class:raised={selecting} role="status">
		<span>{toast.text}</span>
		{#if toast.href}<a href={toast.href}>Open</a>{/if}
		{#if toast.undo}<button onclick={toast.undo}>Undo</button>{/if}
	</div>
{/if}

<style>
	.page {
		max-width: 760px;
		margin: 0 auto;
		padding: 0.9rem var(--gutter) 2rem;
	}
	.head {
		display: flex;
		align-items: baseline;
		gap: 0.6rem;
		margin-bottom: 0.7rem;
	}
	h1 {
		font-size: 1.6rem;
	}
	.count {
		color: var(--text-faint);
		font-size: 0.9rem;
	}
	.page-menu {
		position: relative;
		margin-left: auto;
		align-self: center;
	}
	.popover {
		position: absolute;
		right: 0;
		top: calc(100% + 6px);
		z-index: 25;
		min-width: 190px;
		padding: 0.35rem;
		border-radius: var(--radius);
		background: var(--surface-2);
		border: 1px solid var(--border);
		box-shadow: 0 16px 40px rgb(0 0 0 / 0.45);
	}
	.popover button {
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
	.popover button:hover {
		background: var(--surface-3);
	}
	.status {
		display: flex;
		align-items: center;
		gap: 0.45rem;
		margin: 0 0 0.7rem;
		padding: 0.45rem 0.7rem;
		border-radius: var(--radius-sm);
		background: var(--surface-2);
		color: var(--text-muted);
		font-size: 0.85rem;
	}

	.jot {
		margin-bottom: 1rem;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--surface);
		transition: border-color 0.15s;
	}
	.jot.open {
		border-color: rgb(240 166 70 / 0.55);
	}
	.jot textarea {
		display: block;
		width: 100%;
		min-height: 3rem;
		padding: 0.8rem 0.9rem;
		border: 0;
		background: transparent;
		color: var(--text);
		font: inherit;
		font-size: 1.02rem;
		line-height: 1.5;
		resize: none;
		field-sizing: content;
		outline: none;
	}
	.jot.open textarea {
		min-height: 5.5rem;
	}
	.jot-bar {
		display: flex;
		align-items: center;
		justify-content: flex-end;
		gap: 0.5rem;
		padding: 0 0.6rem 0.6rem;
	}
	.hint {
		margin-right: auto;
		color: var(--text-faint);
		font-size: 0.8rem;
	}

	.filters {
		display: grid;
		gap: 0.6rem;
		margin-bottom: 0.9rem;
	}
	.search {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		padding: 0 0.75rem;
		border: 1px solid var(--border);
		border-radius: var(--radius-sm);
		background: var(--surface);
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
		outline: none;
	}
	.search:focus-within {
		border-color: rgb(240 166 70 / 0.55);
	}
	.chips {
		display: flex;
		gap: 0.35rem;
		overflow-x: auto;
		scrollbar-width: none;
		padding-bottom: 2px;
	}
	.chip {
		display: inline-flex;
		align-items: center;
		gap: 0.3rem;
		flex-shrink: 0;
		padding: 0.3rem 0.7rem;
		border-radius: 999px;
		border: 1px solid var(--border);
		background: transparent;
		color: var(--text-muted);
		font-size: 0.85rem;
		font-weight: 600;
		white-space: nowrap;
		cursor: pointer;
	}
	.chip[aria-pressed='true'] {
		background: var(--accent-soft);
		border-color: rgb(240 166 70 / 0.45);
		color: var(--accent-strong);
	}
	.chip[aria-pressed='true'] :global(svg) {
		fill: currentColor;
	}
	.n {
		color: var(--text-faint);
		font-weight: 500;
		font-size: 0.78rem;
	}
	.matches {
		margin: 0 0 0.5rem;
		color: var(--text-faint);
		font-size: 0.85rem;
	}
	.notes {
		display: grid;
		gap: 0.6rem;
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.show-more {
		display: block;
		margin: 1rem auto 0;
	}
	.none {
		display: grid;
		justify-items: center;
		gap: 0.6rem;
		padding: 2rem 1rem;
	}
	.none p {
		margin: 0;
	}
	.loading {
		height: 30vh;
	}
	.empty {
		display: grid;
		justify-items: center;
		gap: 0.55rem;
		padding: 2.5rem 1rem;
		text-align: center;
		border: 1px dashed var(--border);
		border-radius: var(--radius);
	}
	.empty h2 {
		font-size: 1.1rem;
	}
	.empty p {
		margin: 0 0 0.3rem;
		max-width: 44ch;
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
	.tag {
		color: var(--accent-strong);
		font-weight: 600;
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
		gap: 0.7rem;
		width: min(100%, 560px);
		max-height: calc(100dvh - 2rem);
		overflow-y: auto;
		padding: 1.1rem;
		border-radius: var(--radius);
		background: var(--surface);
		border: 1px solid var(--border);
	}
	.dialog h2 {
		font-size: 1.15rem;
	}
	.dialog p {
		margin: 0;
		font-size: 0.92rem;
	}
	.dialog textarea {
		width: 100%;
		min-height: 12rem;
		resize: vertical;
		line-height: 1.45;
	}
	.check {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		font-size: 0.93rem;
	}
	.dialog-foot {
		display: flex;
		align-items: center;
		justify-content: flex-end;
		gap: 0.5rem;
	}
	.dialog-foot .muted {
		margin-right: auto;
		font-size: 0.88rem;
	}

	.toast {
		position: fixed;
		left: 50%;
		bottom: calc(1.2rem + env(safe-area-inset-bottom));
		z-index: 45;
		display: flex;
		align-items: center;
		gap: 1rem;
		max-width: calc(100vw - 2rem);
		padding: 0.65rem 0.9rem;
		border-radius: var(--radius);
		background: var(--surface-3);
		border: 1px solid var(--border);
		box-shadow: 0 12px 30px rgb(0 0 0 / 0.45);
		translate: -50% 0;
		font-size: 0.93rem;
	}
	.toast button,
	.toast a {
		border: 0;
		background: none;
		color: var(--accent-strong);
		font-weight: 700;
		cursor: pointer;
	}
	.toast.raised {
		bottom: calc(4.6rem + env(safe-area-inset-bottom));
	}
	.select-bar {
		position: fixed;
		left: 0;
		right: 0;
		bottom: 0;
		z-index: 30;
		display: flex;
		align-items: center;
		gap: 0.5rem;
		padding: 0.7rem max(var(--gutter), env(safe-area-inset-left)) calc(0.7rem + env(safe-area-inset-bottom));
		background: var(--surface-2);
		border-top: 1px solid var(--border);
		box-shadow: 0 -10px 30px rgb(0 0 0 / 0.35);
	}
	.select-count {
		flex: 1;
		min-width: 0;
		color: var(--text-muted);
		font-size: 0.9rem;
	}
	.page.selecting {
		padding-bottom: 5rem;
	}
</style>
