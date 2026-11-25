<script lang="ts">
	import { page } from '$app/state';
	import { api } from '#lib/api.ts';
	import Icon from '#lib/components/Icon.svelte';
	import { fileSize, plural } from '#lib/format.ts';
	import { deleteShare, getShare, listShares, type SharedBatch } from '#lib/share-store.ts';
	import type { Label, ProjectSummary } from '#lib/types.ts';
	import { uploads } from '#lib/uploads.svelte.ts';

	// Landing page for Android's share sheet (spec 2.1): the service worker has
	// saved the shared files; pick where they go and upload them.
	const LAST_KEY = 'songvault.shareDestination';

	let batches = $state<SharedBatch[]>([]);
	let loaded = $state(false);
	let projects = $state<ProjectSummary[]>([]);
	let labels = $state<Label[]>([]);
	let projectId = $state<number | null>(null);
	let labelIds = $state<number[]>([]);
	let started = $state(false);
	let finished = $state(false);

	const missed = $derived(page.url.searchParams.has('missed'));
	const empty = $derived(page.url.searchParams.has('empty'));
	const files = $derived(batches.flatMap((b) => b.files));
	const total = $derived(files.reduce((n, f) => n + f.size, 0));
	const project = $derived(projects.find((p) => p.id === projectId) ?? null);

	$effect(() => {
		const id = page.url.searchParams.get('id');
		(async () => {
			try {
				const one = id ? await getShare(id) : undefined;
				// Also pick up shares that were never uploaded (e.g. the app was closed).
				const all = await listShares();
				batches = one ? [one, ...all.filter((b) => b.id !== one.id)] : all;
			} finally {
				loaded = true;
			}
		})();
	});

	$effect(() => {
		api.get<ProjectSummary[]>('/projects').then((p) => {
			projects = p;
			const last = Number(localStorage.getItem(LAST_KEY));
			if (last && p.some((x) => x.id === last)) projectId = last;
		}).catch(() => {});
	});

	$effect(() => {
		const pid = projectId;
		labelIds = [];
		if (pid === null) {
			labels = [];
			return;
		}
		api.get<Label[]>(`/labels?project_id=${pid}`).then((l) => (labels = l)).catch(() => {});
	});

	function toggle(id: number) {
		labelIds = labelIds.includes(id) ? labelIds.filter((x) => x !== id) : [...labelIds, id];
	}

	function upload() {
		if (!files.length) return;
		started = true;
		localStorage.setItem(LAST_KEY, projectId === null ? '' : String(projectId));
		const partNames = labels.filter((l) => labelIds.includes(l.id)).map((l) => l.name);
		const label = project ? [project.name, ...partNames].join(' · ') : 'Uncategorized';
		for (const batch of batches) {
			// Each saved share is removed from the phone's app storage once it's safely stored.
			uploads.add(batch.files, { projectId, labelIds: [...labelIds], label }, () => {
				deleteShare(batch.id).finally(() => {
					batches = batches.filter((b) => b.id !== batch.id);
					if (batches.length === 0) finished = true;
				});
			});
		}
	}
</script>

<div class="page">
	<h1>Shared to Song Vault</h1>

	{#if missed}
		<div class="note">
			Song Vault wasn't ready to receive that share. Open the app once (so it can finish installing),
			then share the videos again.
		</div>
	{:else if !loaded}
		<p class="muted">Loading…</p>
	{:else if finished}
		<div class="done">
			<div class="done-icon"><Icon name="check" size={28} /></div>
			<h2>All stored on the server</h2>
			<p class="muted">
				{#if project}They're in {project.name}.{:else}They're waiting in Uncategorized.{/if}
			</p>
			<a class="btn btn-primary" href={project ? `/projects/${project.id}` : '/uncategorized'}>
				Open {project ? project.name : 'Uncategorized'}
			</a>
		</div>
	{:else if files.length === 0}
		<p class="muted">{empty ? 'Nothing that could be uploaded was shared.' : 'Nothing waiting to upload.'}</p>
		<a class="btn" href="/">Go to Projects</a>
	{:else}
		<p class="muted summary">{plural(files.length, 'file')} · {fileSize(total)}</p>
		<ul class="files">
			{#each files as file, i (i)}
				<li>
					<Icon name={file.type.startsWith('audio') ? 'mic' : 'video'} size={18} />
					<span class="name">{file.name}</span>
					<span class="size">{fileSize(file.size)}</span>
				</li>
			{/each}
		</ul>

		{#if !started}
			<label class="field-label">
				<span>Song</span>
				<select
					class="field"
					value={projectId ?? ''}
					onchange={(e) => (projectId = e.currentTarget.value === '' ? null : Number(e.currentTarget.value))}
				>
					<option value="">Uncategorized (sort later)</option>
					{#each projects as p (p.id)}
						<option value={p.id}>{p.name}</option>
					{/each}
				</select>
			</label>

			{#if projectId !== null}
				<div class="field-label">
					<span>Parts (optional)</span>
					<div class="parts">
						{#each labels as l (l.id)}
							<button class="chip" aria-pressed={labelIds.includes(l.id)} onclick={() => toggle(l.id)}>{l.name}</button>
						{/each}
					</div>
				</div>
			{/if}

			<button class="btn btn-primary go" onclick={upload}>
				<Icon name="upload" size={18} /> Upload {plural(files.length, 'file')}
			</button>
		{:else}
			<p class="muted">Uploading… progress is in the panel below. You can keep using the app.</p>
		{/if}
	{/if}
</div>

<style>
	.page {
		max-width: 560px;
		margin: 0 auto;
		padding: 1.25rem var(--gutter);
		display: grid;
		gap: 1rem;
	}
	h1 {
		font-size: 1.5rem;
	}
	.summary {
		margin: -0.5rem 0 0;
	}
	.note {
		padding: 0.8rem 1rem;
		border-radius: var(--radius-sm);
		border: 1px solid rgb(240 166 70 / 0.35);
		background: var(--accent-soft);
		color: #f3d9b4;
	}
	.files {
		margin: 0;
		padding: 0;
		list-style: none;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		overflow: hidden;
	}
	.files li {
		display: flex;
		align-items: center;
		gap: 0.6rem;
		padding: 0.6rem 0.8rem;
		background: var(--surface);
		color: var(--text-muted);
	}
	.files li + li {
		border-top: 1px solid var(--border);
	}
	.name {
		flex: 1;
		min-width: 0;
		color: var(--text);
		font-size: 0.92rem;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.size {
		font-size: 0.8rem;
		font-variant-numeric: tabular-nums;
	}
	.field-label {
		display: grid;
		gap: 0.35rem;
	}
	.field-label > span {
		font-size: 0.8rem;
		font-weight: 650;
		color: var(--text-muted);
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}
	.field-label select.field {
		width: 100%;
	}
	.parts {
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
	.chip[aria-pressed='true'] {
		background: var(--accent-soft);
		border-color: rgb(240 166 70 / 0.45);
		color: var(--accent-strong);
	}
	.go {
		min-height: 46px;
		font-size: 1rem;
	}
	.done {
		display: grid;
		justify-items: center;
		gap: 0.6rem;
		padding: 2.5rem 1rem;
		text-align: center;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--surface);
	}
	.done p {
		margin: 0;
	}
	.done-icon {
		display: grid;
		place-items: center;
		width: 56px;
		height: 56px;
		border-radius: 16px;
		background: rgb(92 201 138 / 0.14);
		color: #86e0ab;
	}
</style>
