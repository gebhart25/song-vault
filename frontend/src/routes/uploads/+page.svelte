<script lang="ts">
	import { ApiError, api } from '#lib/api.ts';
	import ClipPanel from '#lib/components/ClipPanel.svelte';
	import { dateTime, fileSize } from '#lib/format.ts';
	import type { History, HistoryItem } from '#lib/types.ts';
	import { uploads } from '#lib/uploads.svelte.ts';

	// Upload history (spec 2.4): what is confirmed stored on the server, newest first.
	let items = $state<HistoryItem[]>([]);
	let lastBackup = $state<string | null>(null);
	let loaded = $state(false);
	let error = $state('');
	let openId = $state<number | null>(null);

	async function load() {
		try {
			const history = await api.get<History>('/clips/history');
			items = history.items;
			lastBackup = history.last_backup_at;
			error = '';
		} catch (e) {
			error = e instanceof ApiError ? e.message : 'Could not load the history.';
		} finally {
			loaded = true;
		}
	}

	$effect(() => {
		void uploads.version;
		load();
	});
</script>

<div class="page" class:with-panel={openId !== null}>
	<h1>Upload history</h1>
	<p class="muted intro">
		Every file here was uploaded and its checksum matched the copy on your phone.
	</p>
	{#if lastBackup}
		<div class="notice ok">
			<strong>Last offsite backup: {dateTime(lastBackup)}</strong>
			Files marked “Safe to delete” are on the server and in the offsite backup. Delete those from your
			phone's gallery; keep the others until the next nightly backup.
		</div>
	{:else}
		<div class="notice">
			<strong>Keep the originals on your phone for now.</strong>
			The offsite backup hasn't run yet, so the server copy is the only other copy. Once it runs, this page
			marks the files that are safe to delete.
		</div>
	{/if}

	{#if error}
		<p class="error" role="alert">{error}</p>
	{:else if loaded && items.length === 0}
		<p class="muted">Nothing uploaded yet.</p>
	{:else}
		<ul class="list">
			{#each items as item (item.id)}
				<li>
					<button class="entry" onclick={() => (openId = item.id)} disabled={item.deleted}>
						<span class="name">{item.original_filename}</span>
						<span class="meta">
							{fileSize(item.size_bytes)} · recorded {dateTime(item.recorded_at) || 'unknown'} · uploaded {dateTime(item.uploaded_at)}
						</span>
						<span class="meta">
							{item.project_name ?? 'Uncategorized'}{#if item.deleted}&nbsp;· in Trash{/if}
						</span>
					</button>
					<span class="state" class:ok={item.verified_at} class:safe={item.backed_up}>
						{item.backed_up ? 'Safe to delete ✓' : item.verified_at ? 'Stored on server ✓' : 'Not verified'}
					</span>
				</li>
			{/each}
		</ul>
	{/if}
</div>

{#if openId !== null}
	<ClipPanel clipId={openId} onclose={() => (openId = null)} onchange={load} />
{/if}

<style>
	.page {
		max-width: 900px;
		margin: 0 auto;
		padding: 1.25rem var(--gutter);
	}
	@media (min-width: 1024px) {
		.with-panel {
			margin-right: 460px;
		}
	}
	h1 {
		font-size: 1.6rem;
	}
	.intro {
		margin: 0.3rem 0 1rem;
		font-size: 0.93rem;
	}
	.notice {
		margin-bottom: 1.25rem;
		padding: 0.8rem 1rem;
		border-radius: var(--radius-sm);
		border: 1px solid rgb(240 166 70 / 0.35);
		background: var(--accent-soft);
		font-size: 0.9rem;
		color: #f3d9b4;
	}
	.notice strong {
		display: block;
		color: var(--accent-strong);
		margin-bottom: 0.15rem;
	}
	.list {
		margin: 0;
		padding: 0;
		list-style: none;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		overflow: hidden;
	}
	li {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		padding-right: 0.9rem;
		background: var(--surface);
	}
	li + li {
		border-top: 1px solid var(--border);
	}
	.entry {
		flex: 1;
		min-width: 0;
		display: grid;
		gap: 0.15rem;
		padding: 0.7rem 0.9rem;
		border: 0;
		background: transparent;
		text-align: left;
		cursor: pointer;
	}
	.entry:hover:not(:disabled) {
		background: var(--surface-2);
	}
	.entry:disabled {
		cursor: default;
		opacity: 0.6;
	}
	.name {
		font-weight: 600;
		font-size: 0.93rem;
		overflow-wrap: anywhere;
	}
	.meta {
		font-size: 0.8rem;
		color: var(--text-muted);
	}
	.state {
		flex-shrink: 0;
		font-size: 0.8rem;
		font-weight: 600;
		color: var(--text-faint);
	}
	.state.ok {
		color: #86e0ab;
	}
	.state.safe {
		padding: 0.1rem 0.5rem;
		border-radius: 999px;
		background: rgb(92 201 138 / 0.16);
	}
	.notice.ok {
		border-color: rgb(92 201 138 / 0.35);
		background: rgb(92 201 138 / 0.1);
		color: #c5ecd5;
	}
	.notice.ok strong {
		color: #86e0ab;
	}
	@media (max-width: 560px) {
		li {
			flex-direction: column;
			align-items: stretch;
			gap: 0;
			padding: 0 0 0.6rem;
		}
		.state {
			padding: 0 0.9rem;
		}
	}
</style>
