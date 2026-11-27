<script lang="ts">
	import { goto } from '$app/navigation';
	import { ApiError, api } from '#lib/api.ts';
	import type { MenuAt } from '#lib/longpress.ts';
	import {
		PROJECT_KINDS,
		PROJECT_STATUSES,
		type ProjectDetail,
		type ProjectKind,
		type ProjectStatus,
		type ProjectSummary
	} from '#lib/types.ts';
	import Icon from './Icon.svelte';

	// The long-press / right-click menu on a Projects page card (spec 3.1): set the song's
	// status in one tap, or change its picture. A popover on desktop, a bottom sheet on phones.
	let {
		project,
		at,
		onclose,
		onchange
	}: {
		project: ProjectSummary;
		at: MenuAt;
		onclose: () => void;
		/** The song changed (its new details) */
		onchange: (project: ProjectDetail) => void;
	} = $props();

	let busy = $state(false);
	let error = $state('');
	let menu: HTMLDivElement | undefined = $state();
	let width = $state(0);
	let height = $state(0);

	// Keep the popover on screen: open to the left / above the pointer when there's no room.
	const left = $derived(Math.max(8, Math.min(at.x, window.innerWidth - width - 8)));
	const top = $derived(at.y + height > window.innerHeight - 8 ? Math.max(8, at.y - height) : at.y);

	// Focus the current status, for keyboard users.
	$effect(() => {
		menu?.querySelector<HTMLElement>('[aria-checked="true"]')?.focus();
	});

	async function save(change: { status: ProjectStatus } | { kind: ProjectKind }) {
		if (busy) return;
		if ('status' in change ? change.status === project.status : change.kind === project.kind) {
			onclose();
			return;
		}
		busy = true;
		error = '';
		try {
			onchange(await api.patch<ProjectDetail>(`/projects/${project.id}`, change));
			onclose();
		} catch (e) {
			error = e instanceof ApiError ? e.message : 'Could not change it.';
		} finally {
			busy = false;
		}
	}

	function onkeydown(e: KeyboardEvent) {
		if (e.key !== 'Escape') return;
		e.stopPropagation();
		onclose();
	}

	// The finger that long-pressed the card is still down when the sheet opens under it,
	// and lifting it sends a click to whatever is there now (a status, or the backdrop).
	// So clicks only count once a new press has started on the menu or the backdrop.
	// Keyboard clicks (detail 0) always count.
	let armed = false;
	const arm = () => (armed = true);
	function guard(e: MouseEvent) {
		if (armed || e.detail === 0) return;
		e.preventDefault();
		e.stopPropagation();
	}

	/** Read before closing: closing clears the parent's state, and `project` with it. */
	function openThumbnail() {
		const href = `/projects/${project.id}/thumbnail`;
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
	aria-label="{project.name}: actions"
	bind:this={menu}
	bind:clientWidth={width}
	bind:clientHeight={height}
	style:left={at.sheet ? undefined : `${left}px`}
	style:top={at.sheet ? undefined : `${top}px`}
	onpointerdowncapture={arm}
	onclickcapture={guard}
	oncontextmenu={(e) => e.preventDefault()}
>
	<p class="menu-title">{project.name}</p>
	<div role="menu" aria-label="Status">
		<p class="group">Status</p>
		{#each PROJECT_STATUSES as s (s.value)}
			<button role="menuitemradio" aria-checked={project.status === s.value} disabled={busy} onclick={() => save({ status: s.value })}>
				<span class="dot" data-status={s.value}></span>
				{s.label}
				{#if project.status === s.value}<span class="check"><Icon name="check" size={16} stroke={2.5} /></span>{/if}
			</button>
		{/each}
		<hr />
		<p class="group">Type</p>
		{#each PROJECT_KINDS as k (k.value)}
			<button
				role="menuitemradio"
				aria-checked={project.kind === k.value}
				disabled={busy}
				title={k.hint}
				onclick={() => save({ kind: k.value })}
			>
				<span class="dot kind-dot"></span>
				{k.label}
				{#if project.kind === k.value}<span class="check"><Icon name="check" size={16} stroke={2.5} /></span>{/if}
			</button>
		{/each}
		<hr />
		<button role="menuitem" onclick={openThumbnail}>
			<Icon name="image" size={18} /> Change thumbnail…
		</button>
	</div>
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
		width: 240px;
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
		max-height: 80dvh;
		padding: 0.5rem 0.6rem calc(0.8rem + env(safe-area-inset-bottom));
		border-radius: 16px 16px 0 0;
		border-bottom: 0;
	}
	.menu-title {
		margin: 0;
		padding: 0.35rem 0.65rem 0.2rem;
		color: var(--text);
		font-size: 0.88rem;
		font-weight: 700;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.group {
		margin: 0;
		padding: 0.3rem 0.65rem 0.2rem;
		color: var(--text-faint);
		font-size: 0.72rem;
		font-weight: 700;
		letter-spacing: 0.05em;
		text-transform: uppercase;
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
		opacity: 0.5;
		cursor: default;
	}
	[aria-checked='true'] {
		font-weight: 650;
	}
	.check {
		display: grid;
		margin-left: auto;
		color: var(--accent);
	}
	/* The status pill colours (app.css), as a dot */
	.dot {
		width: 10px;
		height: 10px;
		flex-shrink: 0;
		margin: 0 4px;
		border-radius: 50%;
		background: #c3c9d4;
	}
	.dot[data-status='writing'] {
		background: #9cc2ff;
	}
	.dot[data-status='arranging'] {
		background: #d0b2ff;
	}
	.dot[data-status='recorded'] {
		background: #ffc47a;
	}
	.dot[data-status='done'] {
		background: #86e0ab;
	}
	.dot[data-status='shelved'] {
		background: #8e96a6;
	}
	.kind-dot {
		background: transparent;
		border: 2px solid var(--text-faint);
	}
	hr {
		margin: 0.3rem 0.4rem;
		border: 0;
		border-top: 1px solid var(--border);
	}
	.pad {
		margin: 0;
		padding: 0.4rem 0.65rem;
		font-size: 0.9rem;
	}
</style>
