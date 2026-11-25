<script lang="ts">
	import { fileSize } from '#lib/format.ts';
	import { uploads, type UploadItem } from '#lib/uploads.svelte.ts';
	import Icon from './Icon.svelte';

	const counts = $derived({
		total: uploads.items.length,
		finished: uploads.items.filter((i) => i.status === 'done' || i.status === 'duplicate').length,
		failed: uploads.items.filter((i) => i.status === 'error').length
	});

	const title = $derived(
		uploads.active
			? `Uploading ${Math.min(counts.finished + counts.failed + 1, counts.total)} of ${counts.total}`
			: counts.failed
				? `${counts.failed} upload${counts.failed === 1 ? '' : 's'} need attention`
				: `${counts.finished} upload${counts.finished === 1 ? '' : 's'} stored`
	);

	function percent(item: UploadItem): number {
		return item.size ? Math.floor((item.sent / item.size) * 100) : 0;
	}

	function statusText(item: UploadItem): string {
		switch (item.status) {
			case 'queued':
				return 'Waiting';
			case 'uploading':
				return `${percent(item)}% · ${fileSize(item.sent)} of ${fileSize(item.size)}`;
			case 'verifying':
				return item.hashed < item.size ? 'Checking the file…' : 'Verifying on the server…';
			case 'done':
				return 'Stored on server ✓';
			case 'duplicate':
				return 'Already in Song Vault ✓';
			case 'error':
				return item.error;
		}
	}
</script>

{#if uploads.items.length > 0}
	<aside class="panel no-print" class:collapsed={!uploads.expanded} aria-label="Uploads">
		<header>
			<button class="head" onclick={() => (uploads.expanded = !uploads.expanded)} aria-expanded={uploads.expanded}>
				<span class="icon" class:spin={uploads.active}><Icon name="upload" size={18} /></span>
				<span class="title">{title}</span>
				<span class="chev"><Icon name="chevron-down" size={18} /></span>
			</button>
			{#if !uploads.active && counts.finished > 0}
				<button class="btn btn-ghost btn-sm" onclick={() => uploads.clearFinished()}>Clear</button>
			{/if}
		</header>
		{#if uploads.expanded}
			<ul>
				{#each uploads.items as item (item.key)}
					<li data-status={item.status}>
						<div class="row">
							<div class="names">
								<span class="name" title={item.name}>{item.name}</span>
								<span class="target">→ {item.target.label}</span>
							</div>
							{#if item.status === 'error'}
								<button class="btn btn-sm" onclick={() => uploads.retry(item.key)}>Retry</button>
							{/if}
							{#if item.status !== 'done' && item.status !== 'duplicate' && item.status !== 'verifying'}
								<button class="btn btn-ghost btn-sm btn-icon" aria-label="Cancel upload of {item.name}" onclick={() => uploads.cancel(item.key)}>
									<Icon name="x" size={16} />
								</button>
							{/if}
						</div>
						{#if item.status === 'uploading' || item.status === 'verifying'}
							<div class="bar" aria-hidden="true">
								<span style:width="{item.status === 'verifying' ? 100 : percent(item)}%"></span>
							</div>
						{/if}
						<p class="status" role={item.status === 'error' ? 'alert' : undefined}>{statusText(item)}</p>
					</li>
				{/each}
			</ul>
		{/if}
	</aside>
{/if}

<style>
	.panel {
		position: fixed;
		right: 1rem;
		bottom: calc(1rem + env(safe-area-inset-bottom));
		z-index: 40;
		width: min(380px, calc(100vw - 2rem));
		max-height: min(60vh, 520px);
		display: flex;
		flex-direction: column;
		border-radius: var(--radius);
		background: var(--surface-2);
		border: 1px solid var(--border);
		box-shadow: 0 18px 50px rgb(0 0 0 / 0.5);
		overflow: hidden;
	}
	header {
		display: flex;
		align-items: center;
		gap: 0.25rem;
		padding: 0.35rem 0.5rem 0.35rem 0.35rem;
		border-bottom: 1px solid var(--border);
	}
	.collapsed header {
		border-bottom: 0;
	}
	.head {
		flex: 1;
		display: flex;
		align-items: center;
		gap: 0.55rem;
		padding: 0.4rem;
		border: 0;
		background: transparent;
		cursor: pointer;
		text-align: left;
		font-weight: 650;
		font-size: 0.92rem;
	}
	.icon {
		display: grid;
		place-items: center;
		width: 30px;
		height: 30px;
		border-radius: 8px;
		background: var(--accent-soft);
		color: var(--accent);
	}
	.icon.spin :global(svg) {
		animation: bob 1.2s ease-in-out infinite;
	}
	@keyframes bob {
		50% {
			translate: 0 -2px;
		}
	}
	.title {
		flex: 1;
	}
	.chev {
		display: grid;
		color: var(--text-faint);
		transition: rotate 0.15s;
	}
	.collapsed .chev {
		rotate: 180deg;
	}
	ul {
		margin: 0;
		padding: 0.25rem 0;
		list-style: none;
		overflow-y: auto;
	}
	li {
		padding: 0.55rem 0.85rem;
	}
	li + li {
		border-top: 1px solid rgb(43 48 58 / 0.6);
	}
	.row {
		display: flex;
		align-items: center;
		gap: 0.4rem;
	}
	.names {
		flex: 1;
		min-width: 0;
		display: grid;
	}
	.name {
		font-size: 0.9rem;
		font-weight: 600;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.target {
		font-size: 0.78rem;
		color: var(--text-faint);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.bar {
		height: 4px;
		margin-top: 0.45rem;
		border-radius: 999px;
		background: var(--surface-3);
		overflow: hidden;
	}
	.bar span {
		display: block;
		height: 100%;
		background: var(--accent);
		transition: width 0.3s;
	}
	[data-status='verifying'] .bar span {
		animation: pulse 1s ease-in-out infinite alternate;
	}
	@keyframes pulse {
		to {
			opacity: 0.45;
		}
	}
	.status {
		margin: 0.3rem 0 0;
		font-size: 0.8rem;
		color: var(--text-muted);
		font-variant-numeric: tabular-nums;
	}
	[data-status='done'] .status,
	[data-status='duplicate'] .status {
		color: #86e0ab;
	}
	[data-status='error'] .status {
		color: var(--danger);
	}
	@media (max-width: 560px) {
		.panel {
			right: 0.5rem;
			left: 0.5rem;
			width: auto;
			bottom: calc(0.5rem + env(safe-area-inset-bottom));
		}
	}
</style>
