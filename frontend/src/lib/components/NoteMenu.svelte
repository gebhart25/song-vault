<script lang="ts" module>
	export type NoteAction = 'top' | 'star' | 'used' | 'select' | 'use' | 'start' | 'copy' | 'trash';
</script>

<script lang="ts">
	import type { MenuAt } from '#lib/longpress.ts';
	import type { LocalNote } from '#lib/notebook.svelte.ts';
	import Icon from './Icon.svelte';

	// The long-press / right-click / ⋯ menu on a Notebook note (spec 5.11). A popover on
	// desktop, a bottom sheet on phones, like the project cards' menu.
	let {
		note,
		at,
		online,
		onclose,
		onaction
	}: {
		note: LocalNote;
		at: MenuAt;
		/** The server can be reached: the song actions need it */
		online: boolean;
		onclose: () => void;
		onaction: (action: NoteAction) => void;
	} = $props();

	let menu: HTMLDivElement | undefined = $state();
	let width = $state(0);
	let height = $state(0);

	const left = $derived(Math.max(8, Math.min(at.x, window.innerWidth - width - 8)));
	const top = $derived(at.y + height > window.innerHeight - 8 ? Math.max(8, at.y - height) : at.y);
	const title = $derived(note.body.split('\n').find((l) => l.trim()) ?? 'Note');

	$effect(() => {
		menu?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
	});

	/** Act first: closing clears the parent's state, and `note` with it. */
	function act(action: NoteAction) {
		onaction(action);
		onclose();
	}

	function onkeydown(e: KeyboardEvent) {
		if (e.key !== 'Escape') return;
		e.stopPropagation();
		onclose();
	}

	// The finger that long-pressed the note is still down when the sheet opens under it;
	// lifting it must not tap whatever is there now. Clicks count once a new press starts.
	let armed = false;
	const arm = () => (armed = true);
	function guard(e: MouseEvent) {
		if (armed || e.detail === 0 || !at.sheet) return;
		e.preventDefault();
		e.stopPropagation();
	}
</script>

<svelte:window {onkeydown} />

<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
<div
	class="backdrop"
	class:dim={at.sheet}
	onpointerdown={arm}
	onclick={() => (armed || !at.sheet) && onclose()}
	oncontextmenu={(e) => (e.preventDefault(), (armed || !at.sheet) && onclose())}
></div>

<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
<div
	class="menu"
	class:sheet={at.sheet}
	role="dialog"
	tabindex="-1"
	aria-label="Note: actions"
	bind:this={menu}
	bind:clientWidth={width}
	bind:clientHeight={height}
	style:left={at.sheet ? undefined : `${left}px`}
	style:top={at.sheet ? undefined : `${top}px`}
	onpointerdowncapture={arm}
	onclickcapture={guard}
	oncontextmenu={(e) => e.preventDefault()}
>
	<p class="menu-title">{title}</p>
	<div role="menu" aria-label="Note">
		<button role="menuitem" onclick={() => act('top')}><Icon name="arrow-up" size={18} /> Move to top</button>
		<button role="menuitem" class:on={note.starred} onclick={() => act('star')}>
			<Icon name="star" size={18} />
			{note.starred ? 'Unstar' : 'Star'}
		</button>
		<button role="menuitem" onclick={() => act('used')}>
			<Icon name="check" size={18} />
			{note.status === 'used' ? 'Mark as fresh' : 'Mark as used'}
		</button>
		<button role="menuitem" onclick={() => act('select')}><Icon name="select" size={18} /> Select (to combine)</button>
		<hr />
		<button role="menuitem" disabled={!online} onclick={() => act('use')}><Icon name="music" size={18} /> Use in a song…</button>
		<button role="menuitem" disabled={!online} onclick={() => act('start')}><Icon name="plus" size={18} /> Start a song from this</button>
		{#if !online}<p class="offline-note">Songs need the home network.</p>{/if}
		<hr />
		<button role="menuitem" onclick={() => act('copy')}><Icon name="copy" size={18} /> Copy text</button>
		<button role="menuitem" onclick={() => act('trash')}><Icon name="trash" size={18} /> Move to Trash</button>
	</div>
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
		width: 240px;
		max-height: calc(100dvh - 16px);
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
		max-height: 85dvh;
		padding: 0.5rem 0.6rem calc(0.8rem + env(safe-area-inset-bottom));
		border-radius: 16px 16px 0 0;
		border-bottom: 0;
	}
	.menu-title {
		margin: 0;
		padding: 0.35rem 0.65rem 0.2rem;
		color: var(--text-muted);
		font-size: 0.85rem;
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
		padding: 0.5rem 0.65rem;
		border: 0;
		border-radius: var(--radius-sm);
		background: transparent;
		text-align: left;
		cursor: pointer;
		font-size: 0.95rem;
	}
	.sheet [role='menu'] button {
		padding: 0.7rem 0.65rem;
		font-size: 1rem;
	}
	[role='menu'] button:hover:not(:disabled),
	[role='menu'] button:focus-visible {
		background: var(--surface-3);
	}
	[role='menu'] button:disabled {
		opacity: 0.45;
		cursor: default;
	}
	.offline-note {
		margin: 0;
		padding: 0.1rem 0.65rem 0.3rem;
		color: var(--text-faint);
		font-size: 0.8rem;
	}
	.on :global(svg) {
		color: var(--accent);
		fill: currentColor;
	}
	hr {
		margin: 0.3rem 0.4rem;
		border: 0;
		border-top: 1px solid var(--border);
	}
</style>
