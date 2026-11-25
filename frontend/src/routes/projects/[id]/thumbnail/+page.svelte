<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { ApiError, api } from '#lib/api.ts';
	import Icon from '#lib/components/Icon.svelte';
	import { STOCK_COVERS, shrinkImage, stockUrl } from '#lib/covers.ts';
	import { shortDate } from '#lib/format.ts';
	import type { ClipSummary, Cover, ProjectDetail } from '#lib/types.ts';

	// A song's picture on the Projects page (spec 3.1): one of its clip thumbnails, stock
	// artwork, or an image of the user's own. Each choice applies straight away.

	const id = $derived(Number(page.params.id));

	let project = $state<ProjectDetail | null>(null);
	let cover = $state<Cover | null>(null);
	let loadError = $state('');
	let error = $state('');
	/** Which choice is being saved: 'auto', 'upload', 'stock:<id>' or 'clip:<id>' */
	let saving = $state<string | null>(null);
	let fileInput: HTMLInputElement | undefined = $state();

	$effect(() => {
		const pid = id;
		api.get<ProjectDetail>(`/projects/${pid}`)
			.then((p) => {
				project = p;
				cover = p.cover;
			})
			.catch((e) => (loadError = e instanceof ApiError ? e.message : 'Could not load this song.'));
	});

	/** The song's clips that have a thumbnail, newest first, each once. */
	const clips = $derived.by(() => {
		if (!project) return [];
		const seen = new Map<number, ClipSummary>();
		for (const c of [...project.rows.flatMap((r) => r.clips), ...project.unlabeled]) {
			if (c.thumbnail_url && !seen.has(c.id)) seen.set(c.id, c);
		}
		const when = (c: ClipSummary) => c.recorded_at ?? c.uploaded_at;
		return [...seen.values()].sort((a, b) => when(b).localeCompare(when(a)));
	});

	const caption = $derived.by(() => {
		if (!cover) return '';
		if (cover.kind === 'auto') return cover.url ? 'Automatic: the newest clip’s thumbnail' : 'Automatic: no thumbnails yet';
		if (cover.kind === 'stock') return `Artwork: ${STOCK_COVERS.find((s) => s.id === cover?.stock)?.name ?? cover.stock}`;
		return cover.clip_id ? 'From one of your clips' : 'Your own image';
	});

	async function choose(key: string, request: () => Promise<Cover>) {
		if (saving) return;
		saving = key;
		error = '';
		try {
			cover = await request();
		} catch (e) {
			error = e instanceof ApiError ? e.message : 'Could not change the picture.';
		} finally {
			saving = null;
		}
	}

	const useAuto = () => choose('auto', () => api.put<Cover>(`/projects/${id}/cover`, { kind: 'auto' }));
	const useStock = (stock: string) =>
		choose(`stock:${stock}`, () => api.put<Cover>(`/projects/${id}/cover`, { kind: 'stock', stock }));
	const useClip = (clipId: number) =>
		choose(`clip:${clipId}`, () => api.put<Cover>(`/projects/${id}/cover`, { kind: 'clip', clip_id: clipId }));

	async function upload() {
		const file = fileInput?.files?.[0];
		if (fileInput) fileInput.value = '';
		if (!file) return;
		await choose('upload', async () => {
			let image: Blob;
			try {
				image = await shrinkImage(file);
			} catch {
				throw new ApiError(0, 'This image can’t be opened here. Try a JPEG or PNG.');
			}
			return api.put<Cover>(`/projects/${id}/cover/image`, image);
		});
	}

	// A stable accent for a song without a picture, as on the Projects page.
	function hue(name: string): number {
		let h = 0;
		for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) % 360;
		return h;
	}
</script>

<div class="page">
	<a href="/" class="back"><Icon name="arrow-left" size={18} /> Projects</a>
	<h1>Thumbnail</h1>

	{#if loadError}
		<p class="error" role="alert">{loadError}</p>
	{:else if !project || !cover}
		<div class="loading" aria-busy="true"></div>
	{:else}
		<div class="current">
			<div class="preview" style:--h={hue(project.name)}>
				{#if cover.url}
					<img src={cover.url} alt="" />
				{:else}
					<Icon name="music" size={34} stroke={1.6} />
				{/if}
			</div>
			<div class="current-text">
				<p class="song">{project.name}</p>
				<p class="muted">{caption}</p>
				<div class="current-actions">
					{#if cover.kind !== 'auto'}
						<button class="btn btn-sm" disabled={!!saving} onclick={useAuto}>{saving === 'auto' ? 'Saving…' : 'Use automatic'}</button>
					{/if}
					<button class="btn btn-sm btn-primary" onclick={() => goto('/')}>Done</button>
				</div>
			</div>
		</div>
		{#if error}<p class="error" role="alert">{error}</p>{/if}

		<section>
			<h2>From your clips</h2>
			{#if clips.length}
				<div class="grid">
					{#each clips as c (c.id)}
						<button
							class="choice"
							aria-pressed={cover.kind === 'image' && cover.clip_id === c.id}
							aria-label="Use the thumbnail of {c.title || shortDate(c.recorded_at ?? c.uploaded_at)}"
							disabled={!!saving}
							onclick={() => useClip(c.id)}
						>
							<img src={c.thumbnail_url} alt="" loading="lazy" />
							<span class="label">{c.title || shortDate(c.recorded_at ?? c.uploaded_at)}</span>
							{#if saving === `clip:${c.id}`}<span class="busy" aria-hidden="true"></span>{/if}
						</button>
					{/each}
				</div>
			{:else}
				<p class="muted note">No clip thumbnails yet. Videos get one once they’ve been processed.</p>
			{/if}
		</section>

		<section>
			<h2>Artwork</h2>
			<div class="grid">
				{#each STOCK_COVERS as s (s.id)}
					<button
						class="choice"
						aria-pressed={cover.kind === 'stock' && cover.stock === s.id}
						aria-label="Use the {s.name} artwork"
						disabled={!!saving}
						onclick={() => useStock(s.id)}
					>
						<img src={stockUrl(s.id)} alt="" loading="lazy" />
						<span class="label">{s.name}</span>
						{#if saving === `stock:${s.id}`}<span class="busy" aria-hidden="true"></span>{/if}
					</button>
				{/each}
			</div>
		</section>

		<section>
			<h2>Your own image</h2>
			<p class="muted note">Album art, a photo, a sketch: JPEG, PNG or WebP. It’s resized to fit 1600 px.</p>
			<!-- Chrome on Android opens its photo picker when every accepted type is an image or
			     video. One type that matches no file (a known workaround) gets the "Choose an action"
			     sheet instead, as Upload clip does: Camera, and Files, where Gallery is a source.
			     The Files list still only shows images. -->
			<input bind:this={fileInput} class="visually-hidden" type="file" accept="image/*,android/allowCamera" tabindex="-1" onchange={upload} />
			<div class="upload-row">
				<button class="btn" disabled={!!saving} onclick={() => fileInput?.click()}>
					<Icon name="upload" size={18} />
					{saving === 'upload' ? 'Uploading…' : 'Upload an image'}
				</button>
				{#if cover.kind === 'image' && !cover.clip_id}
					<span class="in-use"><Icon name="check" size={16} stroke={2.5} /> Your image is in use</span>
				{/if}
			</div>
		</section>
	{/if}
</div>

<style>
	.page {
		max-width: 1000px;
		margin: 0 auto;
		padding: 0.9rem var(--gutter) 5rem;
	}
	.back {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
		margin-bottom: 0.6rem;
		color: var(--text-muted);
		font-size: 0.9rem;
		font-weight: 550;
	}
	h1 {
		font-size: 1.55rem;
		margin-bottom: 0.9rem;
	}
	h2 {
		font-size: 1.05rem;
		margin-bottom: 0.6rem;
	}
	.loading {
		height: 40vh;
	}
	.current {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 1rem 1.25rem;
		padding: 0.9rem;
		border-radius: var(--radius);
		background: var(--surface);
		border: 1px solid var(--border);
	}
	.preview {
		display: grid;
		place-items: center;
		flex: 0 0 auto;
		width: min(320px, 100%);
		aspect-ratio: 16 / 9;
		overflow: hidden;
		border-radius: var(--radius-sm);
		color: hsl(var(--h) 55% 72% / 0.75);
		background:
			radial-gradient(120% 120% at 0% 0%, hsl(var(--h) 45% 32% / 0.65), transparent 60%),
			linear-gradient(135deg, hsl(var(--h) 30% 16%), #15181d);
	}
	.preview img {
		width: 100%;
		height: 100%;
		object-fit: cover;
	}
	.current-text {
		flex: 1 1 220px;
		min-width: 0;
	}
	.song {
		margin: 0 0 0.2rem;
		font-size: 1.1rem;
		font-weight: 700;
		overflow-wrap: anywhere;
	}
	.current-text .muted {
		margin: 0;
		font-size: 0.9rem;
	}
	.current-actions {
		display: flex;
		flex-wrap: wrap;
		gap: 0.45rem;
		margin-top: 0.75rem;
	}
	section {
		margin-top: 1.6rem;
	}
	.note {
		margin: 0 0 0.6rem;
		font-size: 0.9rem;
	}
	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
		gap: 0.65rem;
	}
	.choice {
		position: relative;
		display: block;
		aspect-ratio: 16 / 9;
		padding: 0;
		overflow: hidden;
		border: 2px solid transparent;
		border-radius: var(--radius-sm);
		background: var(--surface-2);
		cursor: pointer;
	}
	.choice:hover:not(:disabled) {
		border-color: #4a5262;
	}
	.choice[aria-pressed='true'] {
		border-color: var(--accent);
		box-shadow: 0 0 0 2px rgb(240 166 70 / 0.35);
	}
	.choice[aria-pressed='true']::after {
		content: '✓';
		position: absolute;
		top: 0.35rem;
		right: 0.35rem;
		display: grid;
		place-items: center;
		width: 22px;
		height: 22px;
		border-radius: 50%;
		background: var(--accent);
		color: var(--accent-ink);
		font-size: 0.8rem;
		font-weight: 800;
	}
	.choice:disabled {
		cursor: default;
	}
	.choice img {
		display: block;
		width: 100%;
		height: 100%;
		object-fit: cover;
	}
	.label {
		position: absolute;
		inset: auto 0 0 0;
		padding: 1rem 0.5rem 0.3rem;
		color: #fff;
		font-size: 0.78rem;
		font-weight: 650;
		text-align: left;
		text-shadow: 0 1px 2px rgb(0 0 0 / 0.6);
		background: linear-gradient(transparent, rgb(0 0 0 / 0.7));
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.busy {
		position: absolute;
		inset: 0;
		background: rgb(0 0 0 / 0.45);
	}
	.busy::after {
		content: '';
		position: absolute;
		top: calc(50% - 12px);
		left: calc(50% - 12px);
		width: 24px;
		height: 24px;
		border-radius: 50%;
		border: 3px solid rgb(255 255 255 / 0.3);
		border-top-color: #fff;
		animation: spin 0.8s linear infinite;
	}
	@keyframes spin {
		to {
			rotate: 360deg;
		}
	}
	.upload-row {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.75rem;
	}
	.in-use {
		display: inline-flex;
		align-items: center;
		gap: 0.3rem;
		color: var(--accent-strong);
		font-size: 0.9rem;
		font-weight: 600;
	}
</style>
