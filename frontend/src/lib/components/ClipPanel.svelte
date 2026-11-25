<script lang="ts">
	import { untrack } from 'svelte';
	import { ApiError, api } from '#lib/api.ts';
	import { isChord, joinChords, splitChords } from '#lib/chords.ts';
	import { COUNTDOWN_SECONDS } from '#lib/countdown.ts';
	import { dateTime, duration, fileSize, shortDate, toLocalInput } from '#lib/format.ts';
	import { CLIP_FIELDS, fieldText, parseField, type FieldDef, type FieldKey } from '#lib/song-fields.ts';
	import { roleOptions, sameRole } from '#lib/roles.ts';
	import { CLIP_STATUSES, type ClipDetail, type ClipStatus, type Label, type ProjectSummary } from '#lib/types.ts';
	import ClipPlayer from './ClipPlayer.svelte';
	import Icon from './Icon.svelte';
	import Waveform from './Waveform.svelte';

	type ClipFieldKey = Exclude<FieldKey, 'sounds_like'>;

	// Player and details for one clip (spec 3.5): a full-screen sheet on phones,
	// a side panel on desktop.
	let {
		clipId,
		onclose,
		onchange
	}: { clipId: number; onclose: () => void; onchange?: () => void } = $props();

	const SPEED_KEY = 'songvault.playbackSpeed';
	const AUDIO_ONLY_KEY = 'songvault.audioOnly';
	const COUNTDOWN_KEY = 'songvault.playCountdown';
	const SPEEDS = [0.5, 0.75, 1];

	let clip = $state<ClipDetail | null>(null);
	let loadError = $state('');
	let actionError = $state('');
	let projects = $state<ProjectSummary[]>([]);
	let labels = $state<Label[]>([]);
	let usedRoles = $state<string[]>([]);
	let roleDraft = $state('');
	let speed = $state(Number(localStorage.getItem(SPEED_KEY)) || 1);
	let player: HTMLMediaElement | undefined = $state();
	/** Where the player is, in seconds into the clip, and the clip's length (spec 5.10) */
	let time = $state(0);
	let length = $state(0);
	let paused = $state(true);
	/** Play videos as sound only (spec 5.4): it keeps going with the screen off and uses less data. */
	let audioOnlyPref = $state(localStorage.getItem(AUDIO_ONLY_KEY) === '1');
	/** Count 5 seconds before playing, to get your hands in place and play along (off
	 * unless turned on; remembered on the device) */
	let countIn = $state(localStorage.getItem(COUNTDOWN_KEY) === '1');
	$effect(() => {
		localStorage.setItem(COUNTDOWN_KEY, countIn ? '1' : '0');
	});
	let titleDraft = $state('');
	let notesDraft = $state('');
	let dateDraft = $state('');

	const SOURCES: Record<string, string> = {
		metadata: "from the file's metadata",
		filename: 'from the file name',
		file_date: "from the file's modified date",
		manual: 'set by hand'
	};

	// This take's details (spec 5.2): fields are shown once they have a value or were just added.
	let detailDrafts = $state<Record<string, string>>({});
	let addedFields = $state<ClipFieldKey[]>([]);
	let progDrafts = $state<string[]>([]);

	const shownFields = $derived(
		!clip ? [] : CLIP_FIELDS.filter((f) => fieldText(f.key, clip?.[f.key as ClipFieldKey]) !== '' || addedFields.includes(f.key as ClipFieldKey))
	);
	const hiddenFields = $derived(CLIP_FIELDS.filter((f) => !shownFields.includes(f)));
	const partNames = $derived(clip ? labels.filter((l) => clip?.label_ids.includes(l.id)).map((l) => l.name) : []);

	/** Refresh the form from the server, except the field being typed in. */
	function setClip(c: ClipDetail) {
		clip = c;
		const active = document.activeElement?.id ?? '';
		if (active !== 'clip-title') titleDraft = c.title;
		if (active !== 'clip-notes') notesDraft = c.notes;
		dateDraft = toLocalInput(c.recorded_at ?? c.uploaded_at);
		for (const f of CLIP_FIELDS) {
			if (active !== `cd-${f.key}`) detailDrafts[f.key] = fieldText(f.key, c[f.key as ClipFieldKey]);
		}
		if (!active.startsWith('cd-prog-')) progDrafts = c.progressions.map(joinChords);
	}

	function focusSoon(id: string) {
		setTimeout(() => document.getElementById(id)?.focus(), 0);
	}

	function addField(f: FieldDef) {
		addedFields = [...addedFields, f.key as ClipFieldKey];
		focusSoon(`cd-${f.key}`);
	}

	async function saveField(f: FieldDef) {
		if (!clip) return;
		const key = f.key as ClipFieldKey;
		const parsed = parseField(f, detailDrafts[key] ?? '');
		if ('error' in parsed) {
			actionError = parsed.error;
			return;
		}
		if (parsed.value === null) addedFields = addedFields.filter((k) => k !== key);
		if (fieldText(key, parsed.value) === fieldText(key, clip[key])) return;
		await patch({ [key]: parsed.value });
	}

	async function removeField(f: FieldDef) {
		const key = f.key as ClipFieldKey;
		addedFields = addedFields.filter((k) => k !== key);
		detailDrafts[key] = '';
		if (clip && clip[key] !== null) await patch({ [key]: null });
	}

	function addProgression() {
		progDrafts = [...progDrafts, ''];
		focusSoon(`cd-prog-${progDrafts.length - 1}`);
	}

	async function saveProgressions() {
		if (!clip) return;
		const lists = progDrafts.map(splitChords).filter((chords) => chords.length > 0);
		const bad = lists.flat().find((c) => !isChord(c));
		if (bad) {
			actionError = `“${bad}” isn't a chord name.`;
			return;
		}
		if (JSON.stringify(lists) === JSON.stringify(clip.progressions)) {
			progDrafts = progDrafts.filter((p) => p.trim()); // drop rows left empty
			return;
		}
		await patch({ progressions: lists });
	}

	async function removeProgression(i: number) {
		progDrafts = progDrafts.filter((_, j) => j !== i);
		await saveProgressions();
	}

	function setStatus(status: ClipStatus) {
		if (!clip || clip.status === status) return;
		patch({ status });
	}

	// --- A/B loop (spec 5.3): play one stretch of the take over and over ---------------
	// Times are seconds into the clip; the player does the looping.

	const NUDGE = 0.1;
	const MIN_LOOP = 0.2;
	let loopA = $state<number | null>(null);
	let loopB = $state<number | null>(null);
	let looping = $state(false);

	/** 72.35 -> "1:12.4" */
	const clock = (t: number) => `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, '0')}`;
	const pct = (t: number) => `${Math.min(100, Math.max(0, (t / (length || 1)) * 100))}%`;
	const loop = $derived<[number, number] | null>(looping && loopA !== null && loopB !== null ? [loopA, loopB] : null);

	function seekTo(t: number) {
		if (player && clip) player.currentTime = clip.start_seconds + t;
	}

	function setA() {
		loopA = time;
		if (loopB !== null && loopB < time + MIN_LOOP) {
			loopB = null;
			looping = false;
		}
	}

	function setB() {
		let a = loopA ?? 0;
		let b = time;
		if (b < a) [a, b] = [b, a]; // tapped B before A: swap them
		if (b - a < MIN_LOOP) return;
		loopA = a;
		loopB = b;
		looping = true;
		seekTo(a);
	}

	function nudge(which: 'a' | 'b', delta: number) {
		const end = length || Infinity;
		if (which === 'a' && loopA !== null) {
			loopA = Math.max(0, Math.min(loopA + delta, (loopB ?? end) - MIN_LOOP));
		} else if (which === 'b' && loopB !== null) {
			loopB = Math.min(end, Math.max(loopB + delta, (loopA ?? 0) + MIN_LOOP));
		}
	}

	function toggleLoop() {
		looping = !looping;
		if (looping && loopA !== null && loopB !== null && (time < loopA || time >= loopB)) seekTo(loopA);
	}

	function clearLoop() {
		loopA = loopB = null;
		looping = false;
	}

	// --- Use this frame (spec 5.10): the clip's thumbnail -----------------------------

	let frameSaved = $state(false);

	async function useThisFrame() {
		if (!clip) return;
		await patch({ thumbnail_at: Math.round(time * 100) / 100 });
		frameSaved = true;
		setTimeout(() => (frameSaved = false), 2500);
	}

	// Load the clip; keep polling while its playable copy is being made.
	$effect(() => {
		const id = clipId;
		let timer: ReturnType<typeof setTimeout> | undefined;
		let stopped = false;
		clip = null;
		loadError = '';
		addedFields = [];
		untrack(clearLoop);
		time = length = 0;
		const load = async () => {
			try {
				const c = await api.get<ClipDetail>(`/clips/${id}`);
				if (stopped) return;
				const wasPending = clip?.processing === 'pending';
				setClip(c);
				if (c.processing === 'pending') timer = setTimeout(load, 4000);
				else if (wasPending) onchange?.();
			} catch (e) {
				if (!stopped) loadError = e instanceof ApiError ? e.message : 'Could not load the clip.';
			}
		};
		load();
		return () => {
			stopped = true;
			clearTimeout(timer);
		};
	});

	$effect(() => {
		api.get<ProjectSummary[]>('/projects?include_archived=true').then((p) => (projects = p)).catch(() => {});
	});

	const projectId = $derived(clip?.project_id ?? null);
	$effect(() => {
		const pid = projectId;
		api.get<Label[]>(pid === null ? '/labels' : `/labels?project_id=${pid}`).then((l) => (labels = l)).catch(() => {});
	});

	$effect(() => {
		localStorage.setItem(SPEED_KEY, String(speed));
	});

	// --- audio only (spec 5.4) ----------------------------------------------------------

	const audioOnly = $derived(!!clip && clip.media_type === 'video' && audioOnlyPref && !!clip.audio_url);
	/** Where to pick up after switching between the video and its sound */
	let resumeAt = $state<{ time: number; play: boolean } | null>(null);

	function setAudioOnly(on: boolean) {
		resumeAt = { time, play: !paused };
		audioOnlyPref = on;
		localStorage.setItem(AUDIO_ONLY_KEY, on ? '1' : '0');
	}

	// The lock screen and headphone controls say what's playing.
	$effect(() => {
		if (!clip || !('mediaSession' in navigator)) return;
		const c = clip;
		navigator.mediaSession.metadata = new MediaMetadata({
			title: c.title || `${c.media_type === 'audio' ? 'Audio' : 'Video'} clip, ${shortDate(c.recorded_at ?? c.uploaded_at)}`,
			artist: c.project_name ?? 'Uncategorized',
			album: 'Song Vault',
			artwork: c.thumbnail_url ? [{ src: c.thumbnail_url }] : []
		});
	});

	const JOB_NAMES: Record<string, string> = {
		thumbnail: 'thumbnail',
		playback: 'playable copy',
		audio: 'audio-only copy',
		preview: 'preview strip',
		waveform: 'waveform'
	};

	async function patch(changes: Record<string, unknown>) {
		actionError = '';
		try {
			setClip(await api.patch<ClipDetail>(`/clips/${clipId}`, changes));
			onchange?.();
		} catch (e) {
			actionError = e instanceof ApiError ? e.message : 'Could not save.';
		}
	}

	// --- role (spec 5.1): one Current take per part and role -------------------------------

	$effect(() => {
		api.get<string[]>('/clips/roles').then((r) => (usedRoles = r)).catch(() => {});
	});

	const roles = $derived(roleOptions([...usedRoles, clip?.role]));

	function setRole(role: string | null) {
		if (!clip || sameRole(clip.role, role)) return;
		patch({ role });
	}

	function useOtherRole(event: SubmitEvent) {
		event.preventDefault();
		const role = roleDraft.trim();
		if (!role) return;
		setRole(role);
		roleDraft = '';
	}

	function toggleLabel(id: number) {
		if (!clip) return;
		const ids = clip.label_ids.includes(id) ? clip.label_ids.filter((x) => x !== id) : [...clip.label_ids, id];
		patch({ label_ids: ids });
	}

	function setProject(value: string) {
		patch({ project_id: value === '' ? null : Number(value) });
	}

	function saveDate() {
		if (!dateDraft) return;
		const d = new Date(dateDraft);
		if (!Number.isNaN(d.getTime())) patch({ recorded_at: d.toISOString() });
	}

	async function retry() {
		actionError = '';
		try {
			setClip(await api.post<ClipDetail>(`/clips/${clipId}/reprocess`));
		} catch (e) {
			actionError = e instanceof ApiError ? e.message : 'Could not retry.';
		}
	}

	async function trash() {
		if (!confirm('Move this clip to Trash? You can restore it from Trash for 30 days.')) return;
		try {
			await api.del(`/clips/${clipId}`);
			onchange?.();
			onclose();
		} catch (e) {
			actionError = e instanceof ApiError ? e.message : 'Could not delete.';
		}
	}

	const failedJob = $derived(clip?.jobs.find((j) => j.state === 'failed'));
</script>

<svelte:window onkeydown={(e) => e.key === 'Escape' && onclose()} />

<div class="panel" role="dialog" aria-modal="false" aria-label="Clip">
	<div class="bar">
		<button class="btn btn-ghost btn-icon" onclick={onclose} aria-label="Close">
			<Icon name="x" />
		</button>
		<span class="bar-title">{clip?.title || clip?.original_filename || ''}</span>
	</div>

	{#if loadError}
		<p class="error pad">{loadError}</p>
	{:else if clip}
		<div class="stage" class:audio={clip.media_type === 'audio' || audioOnly}>
			{#if clip.media_url}
				{#key `${clip.id}-${audioOnly}`}
					<ClipPlayer
						src={(audioOnly ? clip.audio_url : clip.media_url) ?? ''}
						kind={clip.media_type === 'video' && !audioOnly ? 'video' : 'audio'}
						poster={clip.thumbnail_url}
						start={clip.start_seconds}
						end={clip.end_seconds}
						{speed}
						{loop}
						countIn={countIn ? COUNTDOWN_SECONDS : 0}
						resume={resumeAt}
						bind:element={player}
						bind:time
						bind:length
						bind:paused
					>
						{#snippet art()}
							{#if clip?.media_type === 'audio' && clip.waveform}
								<div class="stage-wave">
									<Waveform
										peaks={clip.waveform}
										progress={length ? time / length : 0}
										loop={loopA !== null && loopB !== null && length ? [loopA / length, loopB / length] : null}
										onseek={(f) => seekTo(f * length)}
										label="Seek in the recording"
									/>
								</div>
							{:else if audioOnly && clip?.thumbnail_url}
								<img class="audio-poster" src={clip.thumbnail_url} alt="" />
							{:else}
								<div class="audio-art"><Icon name="mic" size={40} stroke={1.5} /></div>
							{/if}
						{/snippet}
					</ClipPlayer>
				{/key}
			{:else if clip.processing === 'pending'}
				<div class="waiting">
					<span class="spinner" aria-hidden="true"></span>
					<p>Making a playable copy…</p>
					<p class="muted small">Phone videos are converted so every browser can play them.</p>
				</div>
			{:else}
				<div class="waiting">
					<p>Couldn't make a playable copy.</p>
					<button class="btn btn-sm" onclick={retry}>Try again</button>
				</div>
			{/if}
		</div>

		<div class="play-options">
			<div class="speeds" role="group" aria-label="Playback speed">
				{#each SPEEDS as s (s)}
					<button class="chip" aria-pressed={speed === s} onclick={() => (speed = s)}>{s}×</button>
				{/each}
			</div>
			{#if clip.media_type === 'video' && clip.audio_url}
				<button
					class="chip listen"
					aria-pressed={audioOnly}
					title="Play just the sound. It keeps playing with the screen off."
					onclick={() => setAudioOnly(!audioOnly)}
				>
					<Icon name="headphones" size={15} /> Audio only
				</button>
			{/if}
			<button
				class="chip listen"
				aria-pressed={countIn}
				title="Count 5 seconds before playing, to get your hands in place"
				onclick={() => (countIn = !countIn)}
			>
				<Icon name="timer" size={15} /> 5 s countdown
			</button>
		</div>

		{#if clip.media_url}
			<div class="loop" role="group" aria-label="A/B loop">
				<span class="point">
					<button class="chip" aria-pressed={loopA !== null} title="Loop from here" onclick={setA}>
						A{#if loopA !== null}&nbsp;<span class="time">{clock(loopA)}</span>{/if}
					</button>
					{#if loopA !== null}
						<button class="nudge" aria-label="Move A back {NUDGE} s" onclick={() => nudge('a', -NUDGE)}><Icon name="chevron-left" size={14} /></button>
						<button class="nudge" aria-label="Move A forward {NUDGE} s" onclick={() => nudge('a', NUDGE)}><Icon name="chevron-right" size={14} /></button>
					{/if}
				</span>
				<span class="point">
					<button class="chip" aria-pressed={loopB !== null} title="Loop up to here" onclick={setB}>
						B{#if loopB !== null}&nbsp;<span class="time">{clock(loopB)}</span>{/if}
					</button>
					{#if loopB !== null}
						<button class="nudge" aria-label="Move B back {NUDGE} s" onclick={() => nudge('b', -NUDGE)}><Icon name="chevron-left" size={14} /></button>
						<button class="nudge" aria-label="Move B forward {NUDGE} s" onclick={() => nudge('b', NUDGE)}><Icon name="chevron-right" size={14} /></button>
					{/if}
				</span>
				<button class="chip" aria-pressed={looping} disabled={loopA === null || loopB === null} onclick={toggleLoop}>
					<Icon name="repeat" size={15} /> Loop
				</button>
				{#if loopA !== null || loopB !== null}
					<button class="btn btn-ghost btn-sm btn-icon" aria-label="Clear the loop" onclick={clearLoop}><Icon name="x" size={16} /></button>
				{/if}
			</div>
			{#if loopA !== null && length > 0}
				<div class="loop-bar" class:off={!looping} aria-hidden="true">
					<span class="region" style:left={pct(loopA)} style:width={pct((loopB ?? loopA) - loopA)}></span>
					<span class="head" style:left={pct(time)}></span>
				</div>
			{:else if loopA === null && loopB === null}
				<p class="loop-hint">To repeat a bit: tap <b>A</b> where it starts and <b>B</b> where it ends.</p>
			{/if}
		{/if}

		<div class="edit-row">
			<a class="btn btn-sm" href="/clips/{clip.id}/edit"><Icon name="scissors" size={16} /> Trim &amp; split</a>
			{#if clip.media_type === 'video' && !audioOnly && clip.media_url}
				<button class="btn btn-sm" onclick={useThisFrame} title="Use the frame showing now as this clip's thumbnail">
					<Icon name={frameSaved ? 'check' : 'image'} size={16} /> {frameSaved ? 'Thumbnail set' : 'Use this frame'}
				</button>
			{/if}
		</div>
		{#if clip.trimmed || clip.pieces > 1}
			<p class="stretch-note">
				{#if clip.trimmed}
					{clock(clip.start_seconds)}–{clock(clip.end_seconds ?? clip.recording_duration ?? 0)} of a {duration(clip.recording_duration)} recording{/if}{#if clip.trimmed && clip.pieces > 1}&nbsp;·{/if}
				{#if clip.pieces > 1}{clip.pieces} clips were made from it{/if}
			</p>
		{/if}

		<div class="pad body">
			{#if actionError}<p class="error" role="alert">{actionError}</p>{/if}
			{#if failedJob && clip.media_url}
				<p class="warn">
					The {JOB_NAMES[failedJob.type] ?? 'processing'} failed.
					<button class="link" onclick={retry}>Try again</button>
				</p>
			{/if}

			<div class="field-label">
				<span id="status-label">Status</span>
				<div class="statuses" role="group" aria-labelledby="status-label">
					{#each CLIP_STATUSES as s (s.value)}
						<button
							class="chip status"
							data-status={s.value}
							aria-pressed={clip.status === s.value}
							onclick={() => setStatus(s.value)}
						>
							{#if s.value === 'current'}<Icon name="star" size={15} />{/if}
							{s.label}
						</button>
					{/each}
				</div>
				<small class="muted">
					{#if clip.status === 'current'}
						The {clip.role ? `${clip.role} take` : 'take'} you're going with{#if partNames.length && clip.project_id !== null}&nbsp;for {partNames.join(', ')}{/if}. Marking another {clip.role ? `${clip.role} take` : 'take without a role'} in the same part replaces it.
					{:else}
						Mark the take you're going with as Current: one per part, or one per role in a part (Rhythm, Lead…).
					{/if}
				</small>
			</div>

			<label class="field-label">
				<span>Title</span>
				<input
					id="clip-title"
					class="field"
					placeholder="Untitled"
					maxlength="200"
					bind:value={titleDraft}
					onblur={() => titleDraft.trim() !== clip?.title && patch({ title: titleDraft })}
					onkeydown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
				/>
			</label>

			<label class="field-label">
				<span>Song</span>
				<select class="field" value={clip.project_id ?? ''} onchange={(e) => setProject(e.currentTarget.value)}>
					<option value="">Uncategorized</option>
					{#each projects as p (p.id)}
						<option value={p.id}>{p.name}{p.archived ? ' (archived)' : ''}</option>
					{/each}
				</select>
			</label>

			<div class="field-label">
				<span>Parts</span>
				<div class="parts">
					{#each labels as l (l.id)}
						<button class="chip" aria-pressed={clip.label_ids.includes(l.id)} onclick={() => toggleLabel(l.id)}>
							{l.name}
						</button>
					{/each}
				</div>
			</div>

			<div class="field-label">
				<span id="role-label">Role</span>
				<div class="parts" role="group" aria-labelledby="role-label">
					{#each roles as r (r)}
						<button class="chip" aria-pressed={sameRole(clip.role, r)} onclick={() => setRole(sameRole(clip?.role, r) ? null : r)}>
							{r}
						</button>
					{/each}
				</div>
				<form class="role-other" onsubmit={useOtherRole}>
					<label class="visually-hidden" for="clip-role">Another role</label>
					<input id="clip-role" class="field" placeholder="Another role, e.g. Slide guitar" maxlength="40" bind:value={roleDraft} />
					<button class="btn btn-sm" type="submit" disabled={!roleDraft.trim()}>Set</button>
				</form>
				<small class="muted">Each role can have its own Current take in a part, so the rhythm and lead takes of a solo can both be Current.</small>
			</div>

			<div class="field-label">
				<span>Details</span>
				{#each progDrafts as _, i (i)}
					<div class="detail-row">
						<label for="cd-prog-{i}">Chords</label>
						<input
							id="cd-prog-{i}"
							class="field chords"
							placeholder="D - Bm - G - A"
							autocomplete="off"
							autocapitalize="characters"
							spellcheck="false"
							bind:value={progDrafts[i]}
							onblur={saveProgressions}
							onkeydown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
						/>
						<button class="btn btn-ghost btn-sm btn-icon" aria-label="Remove these chords" onclick={() => removeProgression(i)}>
							<Icon name="x" size={16} />
						</button>
					</div>
				{/each}
				{#each shownFields as f (f.key)}
					<div class="detail-row">
						<label for="cd-{f.key}">{f.label}</label>
						<input
							id="cd-{f.key}"
							class="field"
							placeholder={f.placeholder}
							maxlength={f.maxlength}
							inputmode={f.numeric ? (f.numeric.integer ? 'numeric' : 'decimal') : undefined}
							list={f.options ? `cd-list-${f.key}` : undefined}
							bind:value={detailDrafts[f.key]}
							onblur={() => saveField(f)}
							onkeydown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
						/>
						{#if f.options}
							<datalist id="cd-list-{f.key}">
								{#each f.options as o (o)}<option value={o}></option>{/each}
							</datalist>
						{/if}
						<button class="btn btn-ghost btn-sm btn-icon" aria-label="Remove {f.label}" onclick={() => removeField(f)}>
							<Icon name="x" size={16} />
						</button>
					</div>
				{/each}
				<div class="add-details">
					<button class="chip add" onclick={addProgression}><Icon name="plus" size={14} /> Chords</button>
					{#each hiddenFields as f (f.key)}
						<button class="chip add" onclick={() => addField(f)}><Icon name="plus" size={14} /> {f.label}</button>
					{/each}
				</div>
			</div>

			<label class="field-label">
				<span>Recorded</span>
				<input class="field" type="datetime-local" bind:value={dateDraft} onchange={saveDate} />
				<small class="muted">
					{clip.recorded_at_source ? SOURCES[clip.recorded_at_source] : 'no date found in the file; showing the upload time'}
				</small>
			</label>

			<label class="field-label">
				<span>Notes</span>
				<textarea
					id="clip-notes"
					class="field"
					placeholder="Try this with a palm-muted verse…"
					bind:value={notesDraft}
					onblur={() => notesDraft !== clip?.notes && patch({ notes: notesDraft })}
				></textarea>
			</label>

			<dl class="facts">
				<dt>File</dt>
				<dd>{clip.original_filename}</dd>
				<dt>Size</dt>
				<dd>
					{fileSize(clip.size_bytes)}{#if clip.duration_seconds}&nbsp;· {duration(clip.duration_seconds)}{/if}
					{#if clip.width && clip.height}&nbsp;· {clip.width}×{clip.height}{/if}
					{#if clip.video_codec}&nbsp;· {clip.video_codec.toUpperCase()}{/if}
				</dd>
				<dt>Uploaded</dt>
				<dd>{dateTime(clip.uploaded_at)}</dd>
				<dt>Server copy</dt>
				<dd class:ok={clip.verified_at}>{clip.verified_at ? 'Stored on server ✓ (checksum matched)' : 'Not verified'}</dd>
			</dl>

			<div class="actions">
				{#if clip.cut_url}
					<a class="btn btn-sm" href={clip.cut_url} download title="Just this stretch, cut from the original without re-encoding. A video can start up to a second early.">
						Download clip
					</a>
				{/if}
				<a class="btn btn-sm" href={clip.original_url} download>{clip.trimmed ? 'Download whole recording' : 'Download original'}</a>
				{#if clip.media_type === 'video' && clip.audio_url}
					<a class="btn btn-sm" href={clip.audio_url} download>Download audio</a>
				{/if}
				<button class="btn btn-sm btn-danger" onclick={trash}><Icon name="trash" size={16} /> Move to Trash</button>
			</div>
		</div>
	{:else}
		<div class="waiting"><span class="spinner" aria-hidden="true"></span></div>
	{/if}
</div>

<style>
	.panel {
		position: fixed;
		inset: 0;
		z-index: 30;
		display: flex;
		flex-direction: column;
		overflow-y: auto;
		overscroll-behavior: contain;
		background: var(--bg);
	}
	/* Scroll the panel instead of squeezing its sections (a tall portrait video
	   would otherwise overflow its shrunken box and cover the fields below). */
	.panel > :global(*) {
		flex-shrink: 0;
	}
	@media (min-width: 1024px) {
		.panel {
			inset: var(--header-h) 0 0 auto;
			width: var(--panel-w, 460px);
			border-left: 1px solid var(--border);
			background: var(--surface);
			box-shadow: -18px 0 40px rgb(0 0 0 / 0.35);
		}
	}
	.bar {
		position: sticky;
		top: 0;
		z-index: 2;
		display: flex;
		align-items: center;
		gap: 0.4rem;
		min-height: 52px;
		padding: 0 0.5rem;
		background: inherit;
		border-bottom: 1px solid var(--border);
	}
	.bar-title {
		font-weight: 650;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.pad {
		padding: 1rem;
	}
	.stage {
		display: grid;
		place-items: center;
		background: #000;
		min-height: 200px;
	}
	/* The player brings its own look; this only covers the waiting states. */
	.stage.audio {
		background: linear-gradient(160deg, #2f281d, #16140f);
	}
	.audio-art {
		display: grid;
		place-items: center;
		width: 88px;
		height: 88px;
		border-radius: 22px;
		background: rgb(240 166 70 / 0.16);
		color: var(--accent);
	}
	.waiting {
		display: grid;
		justify-items: center;
		gap: 0.5rem;
		padding: 2.5rem 1rem;
		text-align: center;
		color: var(--text-muted);
	}
	.waiting p {
		margin: 0;
	}
	.small {
		font-size: 0.85rem;
	}
	.spinner {
		width: 28px;
		height: 28px;
		border-radius: 50%;
		border: 3px solid var(--surface-3);
		border-top-color: var(--accent);
		animation: spin 0.8s linear infinite;
	}
	@keyframes spin {
		to {
			rotate: 360deg;
		}
	}
	.play-options {
		display: flex;
		flex-wrap: wrap;
		justify-content: center;
		align-items: center;
		gap: 0.4rem 0.9rem;
		padding: 0.7rem 1rem 0;
	}
	.speeds {
		display: flex;
		gap: 0.4rem;
	}
	.chip.listen {
		display: inline-flex;
		align-items: center;
		gap: 0.3rem;
	}
	.stage-wave {
		width: 100%;
		height: 96px;
	}
	.audio-poster {
		width: 112px;
		height: 112px;
		object-fit: cover;
		border-radius: 18px;
		opacity: 0.85;
	}
	.loop {
		display: flex;
		flex-wrap: wrap;
		justify-content: center;
		align-items: center;
		gap: 0.4rem 0.5rem;
		padding: 0.55rem 1rem 0;
	}
	.loop .chip {
		display: inline-flex;
		align-items: center;
		gap: 0.3rem;
	}
	.loop .chip:disabled {
		opacity: 0.45;
		cursor: default;
	}
	.point {
		display: inline-flex;
		align-items: center;
		gap: 0.1rem;
	}
	.time {
		font-variant-numeric: tabular-nums;
	}
	.nudge {
		display: grid;
		place-items: center;
		width: 26px;
		height: 30px;
		padding: 0;
		border: 0;
		border-radius: 6px;
		background: transparent;
		color: var(--text-muted);
		cursor: pointer;
	}
	.nudge:hover {
		background: var(--surface-2);
		color: var(--text);
	}
	.loop-bar {
		position: relative;
		height: 6px;
		margin: 0.55rem 1rem 0;
		border-radius: 3px;
		background: var(--surface-3);
	}
	.loop-bar .region {
		position: absolute;
		top: 0;
		bottom: 0;
		min-width: 3px;
		border-radius: 3px;
		background: var(--accent);
	}
	.loop-bar.off .region {
		opacity: 0.4;
	}
	.loop-bar .head {
		position: absolute;
		top: -3px;
		width: 2px;
		height: 12px;
		margin-left: -1px;
		border-radius: 1px;
		background: var(--text);
	}
	.edit-row {
		display: flex;
		flex-wrap: wrap;
		justify-content: center;
		gap: 0.4rem;
		padding: 0.7rem 1rem 0;
	}
	.stretch-note {
		margin: 0.45rem 1rem 0;
		text-align: center;
		color: var(--text-faint);
		font-size: 0.82rem;
	}
	.loop-hint {
		margin: 0.4rem 1rem 0;
		text-align: center;
		color: var(--text-faint);
		font-size: 0.8rem;
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
	.chip:hover {
		color: var(--text);
	}
	.chip[aria-pressed='true'] {
		background: var(--accent-soft);
		border-color: rgb(240 166 70 / 0.45);
		color: var(--accent-strong);
	}
	.body {
		display: grid;
		gap: 1rem;
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
	.field-label textarea {
		min-height: 90px;
	}
	.parts,
	.statuses,
	.add-details {
		display: flex;
		flex-wrap: wrap;
		gap: 0.35rem;
	}
	.chip.status {
		display: inline-flex;
		align-items: center;
		gap: 0.3rem;
	}
	.chip.status[data-status='current'][aria-pressed='true'] {
		background: var(--accent);
		border-color: var(--accent);
		color: var(--accent-ink);
	}
	.chip.status[data-status='current'][aria-pressed='true'] :global(svg) {
		fill: currentColor;
	}
	.chip.status[data-status='discarded'][aria-pressed='true'] {
		background: var(--danger-soft);
		border-color: rgb(240 106 106 / 0.45);
		color: var(--danger);
	}
	.field-label small {
		font-size: 0.82rem;
	}
	.role-other {
		display: flex;
		gap: 0.35rem;
		max-width: 340px;
	}
	.role-other .field {
		min-height: 34px;
		padding: 0.3rem 0.6rem;
	}
	.detail-row {
		display: grid;
		grid-template-columns: 6.5rem minmax(0, 1fr) auto;
		align-items: center;
		gap: 0.4rem;
	}
	.detail-row label {
		font-size: 0.88rem;
		font-weight: 600;
		color: var(--text-muted);
	}
	.detail-row .chords {
		font-weight: 650;
		color: var(--accent-strong);
	}
	.chip.add {
		display: inline-flex;
		align-items: center;
		gap: 0.25rem;
		padding-left: 0.55rem;
		border-style: dashed;
	}
	.facts {
		display: grid;
		grid-template-columns: auto 1fr;
		gap: 0.35rem 1rem;
		margin: 0.25rem 0 0;
		font-size: 0.87rem;
	}
	.facts dt {
		color: var(--text-faint);
	}
	.facts dd {
		margin: 0;
		overflow-wrap: anywhere;
	}
	.facts .ok {
		color: #86e0ab;
	}
	.actions {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
		padding-bottom: calc(5rem + env(safe-area-inset-bottom));
	}
	.warn {
		margin: 0;
		font-size: 0.9rem;
		color: #ffc47a;
	}
	.link {
		border: 0;
		padding: 0;
		background: none;
		color: var(--accent-strong);
		text-decoration: underline;
		cursor: pointer;
	}
</style>
