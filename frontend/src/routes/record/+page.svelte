<script lang="ts">
	import { untrack } from 'svelte';
	import { beforeNavigate, goto } from '$app/navigation';
	import { page } from '$app/state';
	import { api } from '#lib/api.ts';
	import Icon from '#lib/components/Icon.svelte';
	import { countdown } from '#lib/countdown.ts';
	import { duration as clock } from '#lib/format.ts';
	import type { Label, ProjectSummary } from '#lib/types.ts';
	import { uploads } from '#lib/uploads.svelte.ts';

	// Record in the app (spec 5.7): straight from the phone's camera and mic into a song
	// and part, without going through the gallery. A saved take uploads like any other file.

	type Mode = 'video' | 'audio';
	type Phase = 'starting' | 'ready' | 'countdown' | 'recording' | 'review' | 'error';

	const MODE_KEY = 'songvault.recordMode';
	const COUNTDOWN_KEY = 'songvault.recordCountdown';
	// Best first: MP4 needs no conversion on the server; WebM is what older Chrome records.
	const VIDEO_TYPES = ['video/mp4;codecs=avc1,mp4a', 'video/mp4', 'video/webm;codecs=vp9,opus', 'video/webm;codecs=vp8,opus', 'video/webm'];
	const AUDIO_TYPES = ['audio/mp4', 'audio/webm;codecs=opus', 'audio/webm'];

	const numberParam = (name: string) => {
		const n = Number(page.url.searchParams.get(name));
		return Number.isInteger(n) && n > 0 ? n : null;
	};

	let projects = $state<ProjectSummary[]>([]);
	let labels = $state<Label[]>([]);
	let songId = $state<number | null>(numberParam('song'));
	let partId = $state<number | null>(numberParam('part'));
	let mode = $state<Mode>(localStorage.getItem(MODE_KEY) === 'audio' ? 'audio' : 'video');
	let facing = $state<'user' | 'environment'>('environment');
	let phase = $state<Phase>('starting');
	let problem = $state('');
	let attempt = $state(0);
	let elapsed = $state(0);
	let level = $state(0);
	let take = $state<{ url: string; type: string; startedAt: Date; seconds: number } | null>(null);
	let savedTo = $state('');
	/** Count 5 seconds before recording starts, to get your hands in place (off unless turned on; remembered on the device) */
	let countIn = $state(localStorage.getItem(COUNTDOWN_KEY) === '1');
	let countLeft = $state(0);
	let cancelCount: (() => void) | null = null;
	/** Settings can't change from pressing record until the take is done */
	const busy = $derived(phase === 'countdown' || phase === 'recording');
	/** Save & trim (spec 5.10): waiting for the take to upload, then open the editor */
	let trimPending = $state(false);
	/** A take saved with Save & trim is up, but the editor couldn't open by itself (another take was started) */
	let trimReady = $state<number | null>(null);
	let alive = true;
	$effect(() => () => {
		alive = false;
		cancelCount?.();
	});

	let live = $state.raw<MediaStream | null>(null);
	let takeBlob: Blob | null = null;
	let recorder: MediaRecorder | null = null;

	const song = $derived(projects.find((p) => p.id === songId) ?? null);
	const part = $derived(labels.find((l) => l.id === partId) ?? null);
	const destination = $derived([song?.name ?? 'Uncategorized', part?.name].filter(Boolean).join(' · '));
	const backHref = $derived(songId ? `/projects/${songId}` : '/');

	$effect(() => {
		api.get<ProjectSummary[]>('/projects').then((p) => (projects = p)).catch(() => {});
	});

	$effect(() => {
		const pid = songId;
		api.get<Label[]>(pid === null ? '/labels' : `/labels?project_id=${pid}`)
			.then((l) => {
				labels = l;
				if (untrack(() => partId) !== null && !l.some((x) => x.id === untrack(() => partId))) partId = null;
			})
			.catch(() => {});
	});

	$effect(() => {
		localStorage.setItem(COUNTDOWN_KEY, countIn ? '1' : '0');
	});

	$effect(() => {
		localStorage.setItem(MODE_KEY, mode);
	});

	// --- the camera and mic: on while there's no take to review -----------------------------

	const wantLive = $derived(phase !== 'review');

	function constraints(m: Mode, f: 'user' | 'environment'): MediaStreamConstraints {
		// Music, not a phone call: no echo cancelling, noise removal or volume levelling.
		const audio = { echoCancellation: false, noiseSuppression: false, autoGainControl: false };
		if (m === 'audio') return { audio, video: false };
		return { audio, video: { facingMode: f, width: { ideal: 1920 }, height: { ideal: 1080 } } };
	}

	function describe(e: unknown): string {
		const name = e instanceof DOMException ? e.name : '';
		if (name === 'NotAllowedError') return 'Song Vault isn’t allowed to use the camera or microphone. Allow it in the browser’s site settings, then try again.';
		if (name === 'NotFoundError' || name === 'OverconstrainedError') return 'No camera or microphone was found.';
		if (name === 'NotReadableError') return 'The camera or microphone is busy in another app.';
		return e instanceof Error ? e.message : 'Couldn’t start the camera or microphone.';
	}

	$effect(() => {
		void attempt;
		if (!wantLive) return;
		const m = mode;
		const f = facing;
		let stopped = false;
		let got: MediaStream | null = null;
		untrack(() => {
			phase = 'starting';
			problem = '';
		});
		if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
			phase = 'error';
			problem = 'Recording only works when Song Vault is opened over HTTPS.';
			return;
		}
		navigator.mediaDevices
			.getUserMedia(constraints(m, f))
			.then((s) => {
				if (stopped) {
					s.getTracks().forEach((t) => t.stop());
					return;
				}
				got = s;
				live = s;
				phase = 'ready';
			})
			.catch((e) => {
				if (stopped) return;
				phase = 'error';
				problem = describe(e);
			});
		return () => {
			stopped = true;
			got?.getTracks().forEach((t) => t.stop());
			if (live === got) live = null;
		};
	});

	/** Shows the camera in the preview box. */
	function showStream(video: HTMLVideoElement, stream: MediaStream | null) {
		video.srcObject = stream;
		return {
			update(next: MediaStream | null) {
				video.srcObject = next;
			}
		};
	}

	// A level meter, so it's obvious the mic hears the guitar.
	$effect(() => {
		const s = live;
		if (!s || s.getAudioTracks().length === 0) return;
		const ctx = new AudioContext();
		ctx.resume().catch(() => {});
		const analyser = ctx.createAnalyser();
		analyser.fftSize = 1024;
		ctx.createMediaStreamSource(s).connect(analyser);
		const buffer = new Uint8Array(analyser.fftSize);
		let frame = requestAnimationFrame(function tick() {
			analyser.getByteTimeDomainData(buffer);
			let peak = 0;
			for (const v of buffer) peak = Math.max(peak, Math.abs(v - 128));
			level = Math.max(peak / 128, level * 0.9); // falls back slowly, like a meter
			frame = requestAnimationFrame(tick);
		});
		return () => {
			cancelAnimationFrame(frame);
			level = 0;
			ctx.close().catch(() => {});
		};
	});

	// Keep the screen on while recording.
	$effect(() => {
		if (phase !== 'recording' || !('wakeLock' in navigator)) return;
		let lock: WakeLockSentinel | undefined;
		let done = false;
		navigator.wakeLock
			.request('screen')
			.then((l) => {
				if (done) l.release();
				else lock = l;
			})
			.catch(() => {});
		return () => {
			done = true;
			lock?.release();
		};
	});

	// --- recording -------------------------------------------------------------------------

	function pickType(types: string[]): string {
		return types.find((t) => MediaRecorder.isTypeSupported(t)) ?? '';
	}

	/** The record button: straight away, or after the countdown. */
	function start() {
		if (!live || phase !== 'ready') return;
		savedTo = '';
		if (!countIn) {
			begin();
			return;
		}
		phase = 'countdown';
		cancelCount = countdown(
			(left) => (countLeft = left),
			() => {
				cancelCount = null;
				if (phase === 'countdown') begin();
			}
		);
	}

	function cancelCountdown() {
		cancelCount?.();
		cancelCount = null;
		if (phase === 'countdown') phase = 'ready';
	}

	function begin() {
		if (!live) return;
		const wanted = pickType(mode === 'video' ? VIDEO_TYPES : AUDIO_TYPES);
		const chunks: Blob[] = [];
		const startedAt = new Date();
		const options: MediaRecorderOptions = { audioBitsPerSecond: 192_000 };
		if (wanted) options.mimeType = wanted;
		if (mode === 'video') options.videoBitsPerSecond = 8_000_000;
		let rec: MediaRecorder;
		try {
			rec = new MediaRecorder(live, options);
		} catch (e) {
			phase = 'error';
			problem = describe(e);
			return;
		}
		recorder = rec;
		rec.ondataavailable = (e) => e.data.size > 0 && chunks.push(e.data);
		rec.onstop = () => {
			const type = rec.mimeType || wanted || (mode === 'video' ? 'video/webm' : 'audio/webm');
			takeBlob = new Blob(chunks, { type });
			take = {
				url: URL.createObjectURL(takeBlob),
				type,
				startedAt,
				seconds: (Date.now() - startedAt.getTime()) / 1000
			};
			recorder = null;
			phase = 'review';
		};
		rec.start(1000); // a chunk a second, so a crash mid-take loses little
		elapsed = 0;
		phase = 'recording';
	}

	$effect(() => {
		if (phase !== 'recording') return;
		const from = Date.now();
		const timer = setInterval(() => (elapsed = (Date.now() - from) / 1000), 250);
		return () => clearInterval(timer);
	});

	function stop() {
		if (recorder?.state === 'recording') recorder.stop();
	}

	function discard() {
		if (take) URL.revokeObjectURL(take.url);
		take = null;
		takeBlob = null;
		phase = 'starting';
	}

	/** 20261124_101500: the date the server reads from the file name, in local time. */
	function stamp(d: Date): string {
		const p = (n: number) => String(n).padStart(2, '0');
		return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}_${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
	}

	function save(trim = false) {
		if (!take || !takeBlob) return;
		const mp4 = take.type.includes('mp4');
		const ext = mp4 ? (mode === 'audio' ? 'm4a' : 'mp4') : 'webm';
		const file = new File([takeBlob], `REC_${stamp(take.startedAt)}.${ext}`, {
			type: take.type.split(';')[0],
			lastModified: take.startedAt.getTime()
		});
		const target = { projectId: songId, labelIds: partId ? [partId] : [], label: destination };
		const [key] = uploads.add([file], target, trim ? () => openEditor(key) : undefined);
		savedTo = destination;
		trimPending = trim;
		trimReady = null;
		discard();
	}

	/** Save & trim: the take is on the server, so open it in the editor, unless another
	 * take has been started here meanwhile (then offer a link instead). */
	function openEditor(key: number) {
		const clipId = uploads.items.find((i) => i.key === key)?.clipId;
		trimPending = false;
		if (!clipId || !alive) return;
		if (page.url.pathname === '/record' && !busy && !take) void goto(`/clips/${clipId}/edit`);
		else trimReady = clipId;
	}

	beforeNavigate((nav) => {
		const unsaved = phase === 'recording' || take !== null;
		if (unsaved && !confirm('Leave without saving this recording?')) {
			nav.cancel();
			return;
		}
		cancelCountdown();
		stop();
	});

	$effect(() => {
		const warn = (e: BeforeUnloadEvent) => {
			if (phase === 'recording' || take) e.preventDefault();
		};
		window.addEventListener('beforeunload', warn);
		return () => window.removeEventListener('beforeunload', warn);
	});
</script>

<div class="page">
	<a href={backHref} class="back"><Icon name="arrow-left" size={18} /> {song?.name ?? 'Projects'}</a>
	<h1>Record</h1>

	<div class="target">
		<label>
			<span>Song</span>
			<select class="field" bind:value={songId} disabled={busy}>
				<option value={null}>Uncategorized</option>
				{#each projects as p (p.id)}<option value={p.id}>{p.name}</option>{/each}
			</select>
		</label>
		<label>
			<span>Part</span>
			<select class="field" bind:value={partId} disabled={busy}>
				<option value={null}>No part yet</option>
				{#each labels as l (l.id)}<option value={l.id}>{l.name}</option>{/each}
			</select>
		</label>
	</div>

	<div class="modes">
		<div class="tabs" role="group" aria-label="What to record">
			<button aria-pressed={mode === 'video'} disabled={busy || phase === 'review'} onclick={() => (mode = 'video')}>
				<Icon name="video" size={16} /> Video
			</button>
			<button aria-pressed={mode === 'audio'} disabled={busy || phase === 'review'} onclick={() => (mode = 'audio')}>
				<Icon name="mic" size={16} /> Audio only
			</button>
		</div>
		<div class="mode-tools">
			{#if phase !== 'review'}
				<button
					class="btn btn-sm count-toggle"
					aria-pressed={countIn}
					disabled={busy}
					title="Count 5 seconds after you press record, to get your hands in place"
					onclick={() => (countIn = !countIn)}
				>
					<Icon name="timer" size={16} /> 5 s<span class="lbl">&nbsp;countdown</span>
				</button>
			{/if}
			{#if mode === 'video' && phase !== 'review'}
				<button
					class="btn btn-sm flip"
					disabled={busy}
					aria-label={facing === 'user' ? 'Use the back camera' : 'Use the front camera'}
					onclick={() => (facing = facing === 'user' ? 'environment' : 'user')}
				>
					<Icon name="flip" size={16} /> <span class="lbl">{facing === 'user' ? 'Back camera' : 'Front camera'}</span>
				</button>
			{/if}
		</div>
	</div>

	{#if savedTo}
		<p class="saved" role="status">
			<Icon name="check" size={16} /> Saved. It's uploading to {savedTo}{#if trimPending}; the editor opens once it's up{/if}.
			<a href={backHref}>Back to {song ? 'the song' : 'Projects'}</a>
		</p>
	{/if}
	{#if trimReady}
		<p class="saved" role="status">
			<Icon name="scissors" size={16} /> Your earlier take is up. <a href="/clips/{trimReady}/edit">Trim &amp; split it</a>
		</p>
	{/if}

	<div class="stage" class:audio={mode === 'audio'}>
		{#if phase === 'review' && take}
			{#if mode === 'video'}
				<!-- svelte-ignore a11y_media_has_caption -->
				<video src={take.url} controls playsinline></video>
			{:else}
				<div class="mic-art"><Icon name="mic" size={44} stroke={1.5} /></div>
				<audio src={take.url} controls></audio>
			{/if}
		{:else if phase === 'error'}
			<div class="message">
				<p>{problem}</p>
				<button class="btn btn-sm" onclick={() => attempt++}>Try again</button>
			</div>
		{:else}
			{#if mode === 'video'}
				<video class:mirror={facing === 'user'} use:showStream={live} autoplay muted playsinline></video>
			{:else}
				<div class="mic-art" class:on={phase === 'recording'}><Icon name="mic" size={44} stroke={1.5} /></div>
			{/if}
			{#if phase === 'starting'}<p class="starting">Starting the {mode === 'video' ? 'camera' : 'microphone'}…</p>{/if}
			{#if phase === 'recording'}<span class="rec-badge"><span class="rec-dot"></span>{clock(elapsed)}</span>{/if}
			{#if phase === 'countdown'}
				<span class="count" aria-live="assertive"><span class="count-n">{countLeft}</span><span class="count-note">Recording starts in {countLeft} s</span></span>
			{/if}
			{#if live}
				<span class="meter" aria-hidden="true"><span style:width="{Math.min(100, level * 140)}%"></span></span>
			{/if}
		{/if}
	</div>

	<div class="controls">
		{#if phase === 'review' && take}
			<button class="btn" onclick={() => confirm('Delete this take?') && discard()}>Retake</button>
			<button class="btn" onclick={() => save(true)}><Icon name="scissors" size={18} /> Save &amp; trim</button>
			<button class="btn btn-primary" onclick={() => save()}><Icon name="upload" size={18} /> Save to {destination}</button>
		{:else if phase === 'recording'}
			<button class="shutter recording" aria-label="Stop recording" onclick={stop}><span></span></button>
		{:else if phase === 'countdown'}
			<button class="btn" onclick={cancelCountdown}><Icon name="x" size={18} /> Cancel countdown</button>
		{:else}
			<button class="shutter" aria-label="Start recording" disabled={phase !== 'ready'} onclick={start}><span></span></button>
		{/if}
	</div>
	{#if phase === 'review' && take}
		<p class="muted note">{clock(take.seconds)} · saves into {destination}, dated now.</p>
	{/if}
</div>

<style>
	.page {
		max-width: 720px;
		margin: 0 auto;
		padding: 0.9rem var(--gutter) 6rem;
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
		margin-bottom: 0.8rem;
	}
	.target {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 0.6rem;
	}
	.target label {
		display: grid;
		gap: 0.3rem;
	}
	.target span {
		font-size: 0.78rem;
		font-weight: 650;
		color: var(--text-muted);
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}
	.modes {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		justify-content: space-between;
		gap: 0.6rem;
		margin: 0.9rem 0 0.7rem;
	}
	.tabs {
		display: inline-flex;
		padding: 3px;
		border-radius: 10px;
		background: var(--surface);
		border: 1px solid var(--border);
	}
	.tabs button {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
		padding: 0.4rem 0.8rem;
		border: 0;
		border-radius: 7px;
		background: transparent;
		color: var(--text-muted);
		font-weight: 600;
		font-size: 0.9rem;
		cursor: pointer;
	}
	.tabs button[aria-pressed='true'] {
		background: var(--surface-3);
		color: var(--text);
	}
	.tabs button:disabled {
		cursor: default;
	}
	.saved {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.35rem;
		margin: 0 0 0.7rem;
		color: #86e0ab;
		font-size: 0.92rem;
	}
	.saved a {
		color: var(--accent-strong);
		text-decoration: underline;
	}
	.stage {
		position: relative;
		display: grid;
		place-items: center;
		min-height: 240px;
		border-radius: var(--radius);
		overflow: hidden;
		background: #000;
	}
	.stage.audio {
		min-height: 220px;
		gap: 1rem;
		padding: 1.5rem 1rem;
		background: linear-gradient(160deg, #2f281d, #16140f);
	}
	/* Leave room for the record button on a phone held upright */
	.stage video {
		display: block;
		width: 100%;
		max-height: min(62vh, max(220px, calc(100dvh - 22rem)));
		background: #000;
	}
	.stage audio {
		width: 100%;
	}
	.mirror {
		transform: scaleX(-1);
	}
	.mic-art {
		display: grid;
		place-items: center;
		width: 96px;
		height: 96px;
		border-radius: 24px;
		background: rgb(240 166 70 / 0.16);
		color: var(--accent);
		transition: background 0.2s;
	}
	.mic-art.on {
		background: rgb(240 106 106 / 0.25);
		color: #ff8a8a;
	}
	.starting {
		position: absolute;
		color: var(--text-muted);
		font-size: 0.92rem;
	}
	.message {
		display: grid;
		justify-items: center;
		gap: 0.7rem;
		max-width: 40ch;
		padding: 2rem 1rem;
		text-align: center;
		color: var(--text-muted);
	}
	.message p {
		margin: 0;
	}
	.rec-badge {
		position: absolute;
		top: 0.7rem;
		left: 0.7rem;
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
		padding: 0.15rem 0.6rem;
		border-radius: 999px;
		background: rgb(0 0 0 / 0.6);
		color: #fff;
		font-weight: 700;
		font-variant-numeric: tabular-nums;
	}
	.rec-dot {
		width: 9px;
		height: 9px;
		border-radius: 50%;
		background: #ff4d4d;
		animation: blink 1s ease-in-out infinite alternate;
	}
	@keyframes blink {
		to {
			opacity: 0.3;
		}
	}
	.meter {
		position: absolute;
		left: 0.7rem;
		right: 0.7rem;
		bottom: 0.6rem;
		height: 5px;
		border-radius: 3px;
		background: rgb(255 255 255 / 0.15);
		overflow: hidden;
	}
	.meter span {
		display: block;
		height: 100%;
		background: #6fd38f;
		transition: width 0.05s linear;
	}
	.controls {
		position: sticky;
		bottom: 0;
		z-index: 2;
		display: flex;
		justify-content: center;
		align-items: center;
		flex-wrap: wrap;
		gap: 0.6rem;
		margin-top: 0.4rem;
		padding: 0.6rem 0 calc(0.6rem + env(safe-area-inset-bottom));
		background: linear-gradient(transparent, var(--bg) 35%);
	}
	@media (max-width: 480px) {
		.flip .lbl,
		.count-toggle .lbl {
			display: none;
		}
	}
	.mode-tools {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		margin-left: auto;
	}
	.count-toggle[aria-pressed='true'] {
		background: var(--accent-soft);
		border-color: rgb(240 166 70 / 0.45);
		color: var(--accent-strong);
	}
	/* The countdown, big over the camera */
	.count {
		position: absolute;
		inset: 0;
		display: grid;
		place-content: center;
		justify-items: center;
		gap: 0.3rem;
		background: rgb(0 0 0 / 0.45);
		color: #fff;
		text-shadow: 0 2px 10px rgb(0 0 0 / 0.6);
	}
	.count-n {
		font-size: 6rem;
		font-weight: 800;
		line-height: 1;
		font-variant-numeric: tabular-nums;
	}
	.count-note {
		font-size: 0.95rem;
		font-weight: 600;
	}
	.shutter {
		display: grid;
		place-items: center;
		width: 72px;
		height: 72px;
		border-radius: 50%;
		border: 4px solid #fff;
		background: transparent;
		cursor: pointer;
	}
	.shutter span {
		width: 52px;
		height: 52px;
		border-radius: 50%;
		background: #ff4d4d;
		transition:
			width 0.15s,
			height 0.15s,
			border-radius 0.15s;
	}
	.shutter.recording span {
		width: 28px;
		height: 28px;
		border-radius: 6px;
	}
	.shutter:disabled {
		opacity: 0.4;
		cursor: default;
	}
	.note {
		margin: 0.6rem 0 0;
		text-align: center;
		font-size: 0.88rem;
	}
</style>
