<script lang="ts">
	import { contextMenu, type MenuAt } from '#lib/longpress.ts';
	import { notebook, type LocalNote } from '#lib/notebook.svelte.ts';
	import { noteDate, segmentsOf } from '#lib/notebook-text.ts';
	import Icon from './Icon.svelte';

	// One note in the Notebook (spec 5.11): its text with the tags picked out, cut to about
	// eight lines with More. Tap to edit in place; it saves as you type.
	let {
		note,
		onmenu,
		onemptied
	}: {
		note: LocalNote;
		onmenu: (at: MenuAt) => void;
		/** Edited down to nothing: the page moves it to Trash (with Undo). */
		onemptied: () => void;
	} = $props();

	let editing = $state(false);
	let text = $state('');
	let box: HTMLTextAreaElement | undefined = $state();
	let expanded = $state(false);
	let overflowing = $state(false);
	let saveTimer: ReturnType<typeof setTimeout> | undefined;

	const segments = $derived(segmentsOf(note.body));

	function edit() {
		text = note.body;
		editing = true;
	}

	$effect(() => {
		if (!editing || !box) return;
		box.focus();
		box.setSelectionRange(box.value.length, box.value.length);
	});

	function save() {
		clearTimeout(saveTimer);
		if (text !== note.body && text.trim()) notebook.update(note.uid, { body: text });
	}

	function finish() {
		if (!editing) return;
		save();
		editing = false;
		if (!text.trim()) onemptied();
	}

	function oninput() {
		clearTimeout(saveTimer);
		saveTimer = setTimeout(save, 600);
	}

	function onfocusout(e: FocusEvent) {
		if (!document.hasFocus()) return; // the app went to the background: carry on later
		const to = e.relatedTarget as Node | null;
		if (to && (e.currentTarget as Element).contains(to)) return;
		finish();
	}

	function onkeydown(e: KeyboardEvent) {
		if (e.key === 'Escape' || (e.key === 'Enter' && (e.ctrlKey || e.metaKey))) {
			e.preventDefault();
			finish();
		}
	}

	// Leaving the page mid-edit keeps what was typed.
	$effect(() => () => {
		if (editing) save();
	});

	/** Whether the text is cut short (so More is worth showing). */
	function measure(node: HTMLElement, _body: string) {
		const check = () => (overflowing = node.scrollHeight > node.clientHeight + 2);
		const observer = new ResizeObserver(check);
		observer.observe(node);
		check();
		return {
			update: check,
			destroy: () => observer.disconnect()
		};
	}

	function openMenu(e: MouseEvent) {
		const r = (e.currentTarget as Element).getBoundingClientRect();
		onmenu({ x: r.right - 8, y: r.bottom + 4, sheet: matchMedia('(pointer: coarse)').matches });
	}
</script>

<li class="note" class:editing>
	{#if editing}
		<div class="editor" {onfocusout}>
			<textarea bind:this={box} bind:value={text} {oninput} {onkeydown} aria-label="Edit note"></textarea>
			<div class="editor-bar">
				<span class="hint">Saved as you type</span>
				<button class="btn btn-sm btn-primary" onclick={finish}>Done</button>
			</div>
		</div>
	{:else}
		<div class="view" use:contextMenu={onmenu}>
			<div
				class="text"
				class:clamped={!expanded}
				role="button"
				tabindex="0"
				onclick={edit}
				onkeydown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), edit())}
				use:measure={note.body}
			>{#each segments as s, i (i)}{#if s.tag}<span class="tag">{s.text}</span>{:else}{s.text}{/if}{/each}</div>
			{#if overflowing || expanded}
				<button class="more" onclick={() => (expanded = !expanded)}>{expanded ? 'Less' : 'More'}</button>
			{/if}
			{#if note.copy_of}
				<div class="two-devices">
					<Icon name="copy" size={15} />
					<span>Edited on two devices: this is one version, the other is beside it.</span>
					<button onclick={() => notebook.update(note.uid, { copy_of: null })}>Got it</button>
				</div>
			{/if}
			<div class="foot">
				<span class="date">{noteDate(note.created_at)}</span>
				{#if note.status === 'used'}<span class="used">Used</span>{/if}
				{#if note.starred}
					<button class="icon star" aria-label="Unstar" title="Starred (tap to unstar)" onclick={() => notebook.update(note.uid, { starred: false })}>
						<Icon name="star" size={16} />
					</button>
				{/if}
				<button class="icon" aria-label="Note actions" aria-haspopup="menu" onclick={openMenu}>
					<Icon name="more" size={18} />
				</button>
			</div>
		</div>
	{/if}
</li>

<style>
	.note {
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--surface);
	}
	.note.editing {
		border-color: rgb(240 166 70 / 0.55);
	}
	.view {
		padding: 0.7rem 0.85rem 0.35rem;
		-webkit-touch-callout: none;
	}
	.text {
		white-space: pre-wrap;
		overflow-wrap: anywhere;
		line-height: 1.5;
		font-size: 1rem;
		cursor: text;
		border-radius: 4px;
	}
	.text:focus-visible {
		outline-offset: 4px;
	}
	.clamped {
		display: -webkit-box;
		-webkit-box-orient: vertical;
		-webkit-line-clamp: 8;
		line-clamp: 8;
		overflow: hidden;
	}
	.tag {
		color: var(--accent-strong);
		font-weight: 600;
	}
	.more {
		margin-top: 0.2rem;
		padding: 0.15rem 0;
		border: 0;
		background: none;
		color: var(--accent-strong);
		font-size: 0.85rem;
		font-weight: 650;
		cursor: pointer;
	}
	.two-devices {
		display: flex;
		align-items: center;
		gap: 0.45rem;
		margin-top: 0.5rem;
		padding: 0.4rem 0.6rem;
		border-radius: var(--radius-sm);
		background: var(--accent-soft);
		color: #f3d9b4;
		font-size: 0.83rem;
	}
	.two-devices span {
		flex: 1;
	}
	.two-devices button {
		flex-shrink: 0;
		border: 0;
		background: none;
		color: var(--accent-strong);
		font-weight: 650;
		cursor: pointer;
	}
	.foot {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		min-height: 32px;
		margin-top: 0.25rem;
		color: var(--text-faint);
		font-size: 0.8rem;
	}
	.date {
		margin-right: auto;
	}
	.used {
		padding: 0.05rem 0.45rem;
		border-radius: 999px;
		background: rgb(92 201 138 / 0.14);
		color: #86e0ab;
		font-weight: 650;
		font-size: 0.75rem;
	}
	.icon {
		display: grid;
		place-items: center;
		width: 32px;
		height: 32px;
		padding: 0;
		border: 0;
		border-radius: 50%;
		background: transparent;
		color: var(--text-muted);
		cursor: pointer;
	}
	.icon:hover {
		background: var(--surface-2);
		color: var(--text);
	}
	.star {
		color: var(--accent);
	}
	.star :global(svg) {
		fill: currentColor;
	}
	.editor {
		display: grid;
		gap: 0.4rem;
		padding: 0.55rem 0.6rem 0.55rem;
	}
	textarea {
		width: 100%;
		min-height: 4.5rem;
		padding: 0.15rem 0.25rem;
		border: 0;
		background: transparent;
		color: var(--text);
		font: inherit;
		font-size: 1rem;
		line-height: 1.5;
		resize: none;
		field-sizing: content;
		outline: none;
	}
	.editor-bar {
		display: flex;
		align-items: center;
		justify-content: flex-end;
		gap: 0.6rem;
	}
	.hint {
		color: var(--text-faint);
		font-size: 0.8rem;
	}
</style>
