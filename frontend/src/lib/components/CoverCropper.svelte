<script lang="ts">
	import Icon from './Icon.svelte';

	// Crop an uploaded picture to the card's shape (spec 3.1): the picture behind a 16:9
	// frame; drag to move it, pinch / scroll / the slider to zoom. Zoomed all the way out the
	// whole picture shows, with dark bars. Phones show the cards 3:1, the band marked in the
	// middle. What's in the frame is drawn at 1600 x 900 and uploaded.
	let {
		file,
		onconfirm,
		oncancel
	}: {
		file: File;
		onconfirm: (image: Blob) => void;
		oncancel: () => void;
	} = $props();

	const OUT_W = 1600;
	const OUT_H = 900;
	const MAX_ZOOM = 5;
	const BACKGROUND = '#15181d';

	let bitmap = $state.raw<ImageBitmap | null>(null);
	let error = $state('');
	let busy = $state(false);
	let frame: HTMLDivElement | undefined = $state();
	let canvas: HTMLCanvasElement | undefined = $state();
	let fw = $state(0);
	const fh = $derived((fw * 9) / 16);
	/** 1 = the picture just covers the frame */
	let zoom = $state(1);
	/** The point of the picture at the frame's centre, in picture pixels */
	let cx = $state(0);
	let cy = $state(0);

	$effect(() => {
		const chosen = file;
		let alive = true;
		let made: ImageBitmap | null = null;
		// Turned the right way up from the photo's own orientation.
		createImageBitmap(chosen, { imageOrientation: 'from-image' })
			.then((b) => {
				if (!alive) return b.close();
				made = b;
				bitmap = b;
				cx = b.width / 2;
				cy = b.height / 2;
				zoom = 1;
			})
			.catch(() => (error = 'This image can’t be opened here. Try a JPEG or PNG.'));
		return () => {
			alive = false;
			made?.close();
		};
	});

	/** Screen pixels per picture pixel at zoom 1 */
	const cover = $derived(bitmap && fw ? Math.max(fw / bitmap.width, fh / bitmap.height) : 1);
	/** As far out as the whole picture showing */
	const minZoom = $derived(bitmap && fw ? Math.min(fw / bitmap.width, fh / bitmap.height) / cover : 1);
	const scale = $derived(cover * zoom);

	/** Keep the frame on the picture (centred where the picture is smaller than the frame). */
	function clamp(x: number, y: number, s: number): [number, number] {
		if (!bitmap) return [x, y];
		const hw = fw / (2 * s);
		const hh = fh / (2 * s);
		return [
			bitmap.width > 2 * hw ? Math.min(Math.max(x, hw), bitmap.width - hw) : bitmap.width / 2,
			bitmap.height > 2 * hh ? Math.min(Math.max(y, hh), bitmap.height - hh) : bitmap.height / 2
		];
	}

	/** Zoom, keeping the point under `at` (frame pixels) where it is. */
	function setZoom(next: number, at = { x: fw / 2, y: fh / 2 }) {
		const z = Math.min(MAX_ZOOM, Math.max(minZoom, next));
		const px = cx + (at.x - fw / 2) / scale;
		const py = cy + (at.y - fh / 2) / scale;
		const s = cover * z;
		zoom = z;
		[cx, cy] = clamp(px - (at.x - fw / 2) / s, py - (at.y - fh / 2) / s, s);
	}

	// The frame changed size (a phone turned): keep the picture covering it.
	$effect(() => {
		void fw;
		if (bitmap) [cx, cy] = clamp(cx, cy, scale);
	});

	$effect(() => {
		if (!canvas || !bitmap || !fw) return;
		const dpr = window.devicePixelRatio || 1;
		canvas.width = Math.round(fw * dpr);
		canvas.height = Math.round(fh * dpr);
		const g = canvas.getContext('2d');
		if (!g) return;
		g.setTransform(dpr, 0, 0, dpr, 0, 0);
		g.fillStyle = BACKGROUND;
		g.fillRect(0, 0, fw, fh);
		g.imageSmoothingQuality = 'high';
		g.drawImage(bitmap, fw / 2 - cx * scale, fh / 2 - cy * scale, bitmap.width * scale, bitmap.height * scale);
	});

	// --- moving and zooming -------------------------------------------------------------

	const pointers = new Map<number, { x: number; y: number }>();
	let pinch: { distance: number; zoom: number } | null = null;

	function local(e: PointerEvent | WheelEvent) {
		const r = frame!.getBoundingClientRect();
		return { x: e.clientX - r.left, y: e.clientY - r.top };
	}

	const spread = () => {
		const [a, b] = [...pointers.values()];
		return { distance: Math.hypot(a.x - b.x, a.y - b.y), mid: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 } };
	};

	function onpointerdown(e: PointerEvent) {
		frame?.setPointerCapture(e.pointerId);
		pointers.set(e.pointerId, local(e));
		if (pointers.size === 2) pinch = { distance: spread().distance, zoom };
	}

	function onpointermove(e: PointerEvent) {
		const before = pointers.get(e.pointerId);
		if (!before) return;
		const now = local(e);
		pointers.set(e.pointerId, now);
		if (pointers.size === 1) {
			[cx, cy] = clamp(cx - (now.x - before.x) / scale, cy - (now.y - before.y) / scale, scale);
		} else if (pointers.size === 2 && pinch) {
			const { distance, mid } = spread();
			setZoom((pinch.zoom * distance) / pinch.distance, mid);
		}
	}

	function onpointerup(e: PointerEvent) {
		pointers.delete(e.pointerId);
		if (pointers.size < 2) pinch = null;
	}

	// The mouse wheel zooms (not passive, so the page doesn't scroll instead).
	$effect(() => {
		const el = frame;
		if (!el) return;
		const onwheel = (e: WheelEvent) => {
			e.preventDefault();
			setZoom(zoom * Math.exp(-e.deltaY * 0.0015), local(e));
		};
		el.addEventListener('wheel', onwheel, { passive: false });
		return () => el.removeEventListener('wheel', onwheel);
	});

	function onkeydown(e: KeyboardEvent) {
		const step = 24 / scale;
		const moves: Record<string, [number, number]> = {
			ArrowLeft: [-step, 0],
			ArrowRight: [step, 0],
			ArrowUp: [0, -step],
			ArrowDown: [0, step]
		};
		if (e.key in moves) {
			e.preventDefault();
			[cx, cy] = clamp(cx + moves[e.key][0], cy + moves[e.key][1], scale);
		} else if (e.key === '+' || e.key === '=') setZoom(zoom * 1.15);
		else if (e.key === '-') setZoom(zoom / 1.15);
	}

	// --- done ----------------------------------------------------------------------------------

	async function use() {
		if (!bitmap || busy) return;
		busy = true;
		const out = document.createElement('canvas');
		out.width = OUT_W;
		out.height = OUT_H;
		const g = out.getContext('2d');
		if (!g) return;
		const s = (scale * OUT_W) / fw;
		g.fillStyle = BACKGROUND;
		g.fillRect(0, 0, OUT_W, OUT_H);
		g.imageSmoothingQuality = 'high';
		g.drawImage(bitmap, OUT_W / 2 - cx * s, OUT_H / 2 - cy * s, bitmap.width * s, bitmap.height * s);
		out.toBlob(
			(blob) => {
				busy = false;
				if (blob) onconfirm(blob);
				else error = 'Couldn’t make the picture. Try another image.';
			},
			'image/jpeg',
			0.9
		);
	}
</script>

<svelte:window onkeydown={(e) => e.key === 'Escape' && oncancel()} />

<div class="overlay" role="presentation">
	<div class="dialog" role="dialog" aria-modal="true" aria-labelledby="crop-title">
		<div class="bar">
			<h2 id="crop-title">Crop the picture</h2>
			<button class="btn btn-ghost btn-sm btn-icon" aria-label="Cancel" onclick={oncancel}><Icon name="x" /></button>
		</div>

		{#if error}
			<p class="error" role="alert">{error}</p>
		{:else}
			<div
				class="frame"
				bind:this={frame}
				bind:clientWidth={fw}
				role="slider"
				tabindex="0"
				aria-label="Picture position: drag to move, pinch or scroll to zoom"
				aria-valuemin={Math.round(minZoom * 100)}
				aria-valuemax={MAX_ZOOM * 100}
				aria-valuenow={Math.round(zoom * 100)}
				aria-valuetext="{Math.round(zoom * 100)}% zoom"
				{onpointerdown}
				{onpointermove}
				{onpointerup}
				onpointercancel={onpointerup}
				{onkeydown}
			>
				<canvas bind:this={canvas}></canvas>
				{#if !bitmap}<div class="loading" aria-hidden="true"></div>{/if}
				<!-- Phones show the cards 3:1: this middle band. -->
				<div class="band" aria-hidden="true"><span>Phones show this band</span></div>
			</div>

			<div class="zoom">
				<button class="btn btn-ghost btn-sm btn-icon" aria-label="Zoom out" disabled={zoom <= minZoom + 0.001} onclick={() => setZoom(zoom / 1.25)}>
					<Icon name="minus" size={18} />
				</button>
				<input
					type="range"
					min={minZoom}
					max={MAX_ZOOM}
					step="0.01"
					value={zoom}
					aria-label="Zoom"
					oninput={(e) => setZoom(Number(e.currentTarget.value))}
				/>
				<button class="btn btn-ghost btn-sm btn-icon" aria-label="Zoom in" disabled={zoom >= MAX_ZOOM} onclick={() => setZoom(zoom * 1.25)}>
					<Icon name="plus" size={18} />
				</button>
			</div>
			<p class="hint">Drag to move it. Pinch, scroll or use the slider to zoom; all the way out shows the whole picture.</p>
		{/if}

		<div class="foot">
			<button class="btn" onclick={oncancel}>Cancel</button>
			<button class="btn btn-primary" disabled={!bitmap || busy || !!error} onclick={use}>
				{busy ? 'Preparing…' : 'Use this picture'}
			</button>
		</div>
	</div>
</div>

<style>
	.overlay {
		position: fixed;
		inset: 0;
		z-index: 45;
		display: grid;
		place-items: center;
		padding: 1rem;
		background: rgb(0 0 0 / 0.75);
	}
	.dialog {
		display: grid;
		gap: 0.75rem;
		width: min(100%, 720px);
		max-height: calc(100dvh - 2rem);
		overflow-y: auto;
		padding: 0.9rem 1.1rem 1.1rem;
		border-radius: var(--radius);
		background: var(--surface);
		border: 1px solid var(--border);
	}
	.bar {
		display: flex;
		align-items: center;
		justify-content: space-between;
	}
	h2 {
		font-size: 1.15rem;
	}
	.frame {
		position: relative;
		aspect-ratio: 16 / 9;
		overflow: hidden;
		border-radius: var(--radius-sm);
		background: #15181d;
		cursor: grab;
		touch-action: none;
		user-select: none;
		-webkit-user-select: none;
	}
	.frame:active {
		cursor: grabbing;
	}
	.frame:focus-visible {
		outline-offset: 3px;
	}
	canvas {
		display: block;
		width: 100%;
		height: 100%;
	}
	.loading {
		position: absolute;
		inset: 0;
		background: linear-gradient(90deg, var(--surface), var(--surface-2), var(--surface));
		background-size: 200% 100%;
		animation: shimmer 1.4s infinite linear;
	}
	@keyframes shimmer {
		to {
			background-position: -200% 0;
		}
	}
	/* 3:1 of a 16:9 frame: the middle 59.3% of its height */
	.band {
		position: absolute;
		left: 0;
		right: 0;
		top: 20.37%;
		bottom: 20.37%;
		border-block: 2px dashed rgb(255 255 255 / 0.55);
		box-shadow: 0 0 0 100vmax rgb(0 0 0 / 0.18);
		pointer-events: none;
	}
	.band span {
		position: absolute;
		right: 0.4rem;
		bottom: 0.3rem;
		padding: 0.1rem 0.4rem;
		border-radius: 4px;
		background: rgb(0 0 0 / 0.55);
		color: #fff;
		font-size: 0.72rem;
		font-weight: 650;
	}
	.zoom {
		display: flex;
		align-items: center;
		gap: 0.4rem;
	}
	.zoom input {
		flex: 1;
		accent-color: var(--accent);
	}
	.hint {
		margin: 0;
		color: var(--text-faint);
		font-size: 0.85rem;
	}
	.foot {
		display: flex;
		justify-content: flex-end;
		gap: 0.5rem;
	}
</style>
