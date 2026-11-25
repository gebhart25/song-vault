<script lang="ts">
	import { isChord, joinChords, splitChords } from '#lib/chords.ts';
	import { SONG_FIELDS, fieldText, parseField, type FieldKey } from '#lib/song-fields.ts';
	import type { ProjectDetail } from '#lib/types.ts';
	import Icon from './Icon.svelte';
	import StrumPattern from './StrumPattern.svelte';

	// Song notes on the song page (spec 5.2, 5.6): key, tempo and other facts,
	// labeled chord progressions, strumming patterns, and free-form notes. Pencil to edit.
	let {
		project,
		partNames = [],
		onsave
	}: {
		project: ProjectDetail;
		/** Song parts, offered as progression labels */
		partNames?: string[];
		onsave: (changes: Partial<ProjectDetail>) => Promise<ProjectDetail | undefined>;
	} = $props();

	const FIELDS = SONG_FIELDS;
	const PROGRESSION_LABELS = ['Intro', 'Verse', 'Pre-Chorus', 'Chorus', 'Bridge', 'Turnaround', 'Chorus Turnaround', 'Solo', 'Outro'];

	interface Draft {
		shown: FieldKey[];
		values: Record<FieldKey, string>;
		progressions: { label: string; chords: string }[];
		strumming: { label: string; pattern: string }[];
		notes: string;
	}

	let editing = $state(false);
	let draft = $state<Draft | null>(null);
	let problem = $state('');
	let saving = $state(false);
	let expanded = $state(false);

	const display = (key: FieldKey): string => fieldText(key, project[key]);
	const facts = $derived(FIELDS.filter((f) => display(f.key) !== ''));
	const empty = $derived(
		facts.length === 0 && project.progressions.length === 0 && project.strumming.length === 0 && !project.notes.trim()
	);
	const longNotes = $derived(project.notes.split('\n').length > 6 || project.notes.length > 420);
	const labelOptions = $derived([...new Set([...partNames.filter((n) => n !== 'Full Song'), ...PROGRESSION_LABELS])]);

	function startEdit() {
		const values = Object.fromEntries(FIELDS.map((f) => [f.key, display(f.key)])) as Record<FieldKey, string>;
		draft = {
			shown: FIELDS.filter((f) => values[f.key] !== '').map((f) => f.key),
			values,
			progressions: project.progressions.map((p) => ({ label: p.label, chords: joinChords(p.chords) })),
			strumming: project.strumming.map((st) => ({ ...st })),
			notes: project.notes
		};
		problem = '';
		editing = true;
	}

	function addField(key: FieldKey) {
		if (!draft) return;
		draft.shown = [...draft.shown, key];
		focusSoon(`sn-${key}`);
	}

	function removeField(key: FieldKey) {
		if (!draft) return;
		draft.shown = draft.shown.filter((k) => k !== key);
		draft.values[key] = '';
	}

	function addProgression() {
		if (!draft) return;
		draft.progressions.push({ label: '', chords: '' });
		focusSoon(`sn-prog-label-${draft.progressions.length - 1}`);
	}

	function addStrum() {
		if (!draft) return;
		draft.strumming.push({ label: '', pattern: '' });
		focusSoon(`sn-strum-${draft.strumming.length - 1}`);
	}

	function moveIn<T>(list: T[], i: number, direction: -1 | 1) {
		const j = i + direction;
		if (j < 0 || j >= list.length) return;
		[list[i], list[j]] = [list[j], list[i]];
	}

	function focusSoon(id: string) {
		setTimeout(() => document.getElementById(id)?.focus(), 0);
	}

	async function save(event: SubmitEvent) {
		event.preventDefault();
		if (!draft) return;
		problem = '';

		const changes: Record<string, unknown> = { notes: draft.notes };
		for (const f of FIELDS) {
			const parsed = parseField(f, draft.shown.includes(f.key) ? draft.values[f.key] : '');
			if ('error' in parsed) {
				problem = parsed.error;
				return;
			}
			changes[f.key] = parsed.value;
		}

		const progressions = [];
		for (const p of draft.progressions) {
			const label = p.label.trim();
			const chords = splitChords(p.chords);
			if (!label && chords.length === 0) continue;
			if (!label) {
				problem = `Give the progression “${joinChords(chords)}” a label, like Verse or Chorus.`;
				return;
			}
			if (chords.length === 0) {
				problem = `Add the chords for “${label}”.`;
				return;
			}
			const bad = chords.find((c) => !isChord(c));
			if (bad) {
				problem = `“${bad}” in ${label} isn't a chord name.`;
				return;
			}
			progressions.push({ label, chords });
		}
		changes.progressions = progressions;

		const strumming = [];
		for (const st of draft.strumming) {
			const label = st.label.trim();
			const pattern = st.pattern.trim();
			if (!pattern) {
				if (label) {
					problem = `Add the strumming pattern for “${label}”.`;
					return;
				}
				continue;
			}
			if (pattern.length > 80) {
				problem = 'Keep each strumming pattern under 80 characters.';
				return;
			}
			strumming.push({ label, pattern });
		}
		changes.strumming = strumming;

		saving = true;
		const saved = await onsave(changes as Partial<ProjectDetail>);
		saving = false;
		if (saved) {
			editing = false;
			draft = null;
		}
	}
</script>

<section class="song-notes" aria-labelledby="song-notes-title">
	<div class="head">
		<h2 id="song-notes-title">Song notes</h2>
		{#if !editing}
			<button class="btn btn-ghost btn-sm btn-icon" aria-label="Edit song notes" title="Edit song notes" onclick={startEdit}>
				<Icon name="edit" size={18} />
			</button>
		{/if}
	</div>

	{#if editing && draft}
		<form class="edit" onsubmit={save}>
			{#each FIELDS.filter((f) => draft?.shown.includes(f.key)) as f (f.key)}
				<div class="edit-row">
					<label for="sn-{f.key}">{f.label}</label>
					<input
						id="sn-{f.key}"
						class="field"
						placeholder={f.placeholder}
						inputmode={f.numeric ? (f.key === 'bpm' ? 'decimal' : 'numeric') : undefined}
						list={f.options ? `sn-list-${f.key}` : undefined}
						maxlength={f.maxlength}
						bind:value={draft.values[f.key]}
					/>
					{#if f.options}
						<datalist id="sn-list-{f.key}">
							{#each f.options as o (o)}<option value={o}></option>{/each}
						</datalist>
					{/if}
					<button type="button" class="btn btn-ghost btn-sm btn-icon" aria-label="Remove {f.label}" onclick={() => removeField(f.key)}>
						<Icon name="x" size={16} />
					</button>
				</div>
			{/each}

			{#if draft.progressions.length > 0}
				<div class="group">
					<span class="group-label">Progressions</span>
					{#each draft.progressions as p, i (i)}
						<div class="prog-row">
							<label class="visually-hidden" for="sn-prog-label-{i}">Progression label</label>
							<input
								id="sn-prog-label-{i}"
								class="field prog-label"
								placeholder="Label, e.g. Verse"
								maxlength="60"
								list="sn-prog-labels"
								bind:value={p.label}
							/>
							<label class="visually-hidden" for="sn-prog-chords-{i}">Chords for {p.label || 'this progression'}</label>
							<input
								id="sn-prog-chords-{i}"
								class="field prog-chords"
								placeholder="D - Bm - G - A"
								autocapitalize="characters"
								autocomplete="off"
								spellcheck="false"
								bind:value={p.chords}
							/>
							<div class="prog-actions">
								<button type="button" class="btn btn-ghost btn-sm btn-icon" aria-label="Move up" disabled={i === 0} onclick={() => draft && moveIn(draft.progressions, i, -1)}>
									<Icon name="arrow-up" size={16} />
								</button>
								<button type="button" class="btn btn-ghost btn-sm btn-icon" aria-label="Move down" disabled={i === draft.progressions.length - 1} onclick={() => draft && moveIn(draft.progressions, i, 1)}>
									<Icon name="arrow-down" size={16} />
								</button>
								<button type="button" class="btn btn-ghost btn-sm btn-icon" aria-label="Remove progression" onclick={() => draft?.progressions.splice(i, 1)}>
									<Icon name="x" size={16} />
								</button>
							</div>
						</div>
					{/each}
					<datalist id="sn-prog-labels">
						{#each labelOptions as o (o)}<option value={o}></option>{/each}
					</datalist>
				</div>
			{/if}

			{#if draft.strumming.length > 0}
				<div class="group">
					<span class="group-label">Strumming</span>
					<p class="strum-help">
						<b>D</b> down, <b>U</b> up, <b>X</b> muted, <b>-</b> a miss; lowercase <b>d u</b> for lighter strums.
						Fill the bar (e.g. 8 for eighth notes in 4/4) to get the counts.
					</p>
					{#each draft.strumming as st, i (i)}
						<div class="prog-row strum-row">
							<label class="visually-hidden" for="sn-strum-label-{i}">Strumming label (optional)</label>
							<input
								id="sn-strum-label-{i}"
								class="field prog-label"
								placeholder="Label (optional)"
								maxlength="60"
								list="sn-prog-labels"
								bind:value={st.label}
							/>
							<label class="visually-hidden" for="sn-strum-{i}">Strumming pattern{st.label ? ` for ${st.label}` : ''}</label>
							<input
								id="sn-strum-{i}"
								class="field prog-chords"
								placeholder="D - D U - U D U"
								maxlength="80"
								autocapitalize="characters"
								autocomplete="off"
								spellcheck="false"
								bind:value={st.pattern}
							/>
							<div class="prog-actions">
								<button type="button" class="btn btn-ghost btn-sm btn-icon" aria-label="Move up" disabled={i === 0} onclick={() => draft && moveIn(draft.strumming, i, -1)}>
									<Icon name="arrow-up" size={16} />
								</button>
								<button type="button" class="btn btn-ghost btn-sm btn-icon" aria-label="Move down" disabled={i === draft.strumming.length - 1} onclick={() => draft && moveIn(draft.strumming, i, 1)}>
									<Icon name="arrow-down" size={16} />
								</button>
								<button type="button" class="btn btn-ghost btn-sm btn-icon" aria-label="Remove strumming pattern" onclick={() => draft?.strumming.splice(i, 1)}>
									<Icon name="x" size={16} />
								</button>
							</div>
							{#if st.pattern.trim()}
								<div class="strum-preview">
									<StrumPattern pattern={st.pattern.trim()} timeSignature={draft.values.time_signature || project.time_signature} />
								</div>
							{/if}
						</div>
					{/each}
				</div>
			{/if}

			{#if draft.progressions.length === 0 && draft.strumming.length > 0}
				<datalist id="sn-prog-labels">
					{#each labelOptions as o (o)}<option value={o}></option>{/each}
				</datalist>
			{/if}

			<div class="add">
				<span class="group-label">Add</span>
				<div class="add-chips">
					{#each FIELDS.filter((f) => !draft?.shown.includes(f.key)) as f (f.key)}
						<button type="button" class="chip" onclick={() => addField(f.key)}><Icon name="plus" size={14} /> {f.label}</button>
					{/each}
					<button type="button" class="chip" onclick={addProgression}><Icon name="plus" size={14} /> Progression</button>
					<button type="button" class="chip" onclick={addStrum}><Icon name="plus" size={14} /> Strumming</button>
				</div>
			</div>

			<label class="group">
				<span class="group-label">General notes</span>
				<textarea class="field" placeholder="Arrangement ideas, what to try next…" bind:value={draft.notes}></textarea>
			</label>

			{#if problem}<p class="error" role="alert">{problem}</p>{/if}
			<div class="edit-actions">
				<button class="btn btn-primary btn-sm" type="submit" disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
				<button class="btn btn-ghost btn-sm" type="button" onclick={() => ((editing = false), (draft = null))}>Cancel</button>
			</div>
		</form>
	{:else if empty}
		<button class="empty" onclick={startEdit}>Add the key, tempo, chord progressions, strumming and other notes.</button>
	{:else}
		{#if facts.length > 0}
			<dl class="facts">
				{#each facts as f (f.key)}
					<div class="fact" class:wide={f.key === 'sounds_like'}>
						<dt>{f.label}</dt>
						<dd>{display(f.key)}</dd>
					</div>
				{/each}
			</dl>
		{/if}
		{#if project.progressions.length > 0}
			<ul class="progressions">
				{#each project.progressions as p, i (i)}
					<li><span class="p-label">{p.label}:</span> <span class="p-chords">{joinChords(p.chords)}</span></li>
				{/each}
			</ul>
		{/if}
		{#if project.strumming.length > 0}
			<div class="strumming">
				<span class="strum-title">Strumming</span>
				<ul>
					{#each project.strumming as st, i (i)}
						<li>
							{#if st.label}<span class="p-label">{st.label}:</span>{/if}
							<StrumPattern pattern={st.pattern} timeSignature={project.time_signature} />
						</li>
					{/each}
				</ul>
			</div>
		{/if}
		{#if project.notes.trim()}
			<p class="general" class:clamped={longNotes && !expanded}>{project.notes}</p>
			{#if longNotes}
				<button class="more" onclick={() => (expanded = !expanded)}>{expanded ? 'Show less' : 'Show more'}</button>
			{/if}
		{/if}
	{/if}
</section>

<style>
	.song-notes {
		margin-top: 0.9rem;
		max-width: 760px;
		padding: 0.75rem 0.9rem 0.9rem;
		border-radius: var(--radius);
		background: var(--surface);
		border: 1px solid var(--border);
	}
	.head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		min-height: 32px;
		margin-bottom: 0.35rem;
	}
	h2 {
		font-size: 0.8rem;
		font-weight: 650;
		color: var(--text-muted);
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}
	.head .btn {
		color: var(--text-muted);
	}
	.empty {
		width: 100%;
		padding: 0.6rem 0;
		border: 0;
		background: none;
		color: var(--text-faint);
		text-align: left;
		cursor: pointer;
		font-size: 0.93rem;
	}
	.empty:hover {
		color: var(--text-muted);
	}

	.facts {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem 1.4rem;
		margin: 0 0 0.6rem;
	}
	.fact dt {
		font-size: 0.72rem;
		font-weight: 650;
		color: var(--text-faint);
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}
	.fact dd {
		margin: 0.05rem 0 0;
		font-weight: 650;
		font-size: 1.02rem;
	}
	.fact.wide dd {
		font-weight: 500;
		font-size: 0.95rem;
	}
	.progressions {
		display: grid;
		gap: 0.2rem;
		margin: 0 0 0.6rem;
		padding: 0;
		list-style: none;
		font-size: 1rem;
	}
	.p-label {
		color: var(--text-muted);
		font-weight: 600;
	}
	.p-chords {
		color: var(--accent-strong);
		font-weight: 650;
		word-spacing: 0.08em;
	}
	.strumming {
		margin: 0 0 0.7rem;
	}
	.strum-title {
		display: block;
		margin-bottom: 0.25rem;
		font-size: 0.72rem;
		font-weight: 650;
		color: var(--text-faint);
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}
	.strumming ul {
		display: grid;
		gap: 0.45rem;
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.strumming li {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0.2rem 0.6rem;
		font-size: 1rem;
	}
	.general {
		margin: 0;
		white-space: pre-wrap;
		overflow-wrap: anywhere;
		font-size: 0.95rem;
		color: var(--text);
	}
	.clamped {
		display: -webkit-box;
		-webkit-line-clamp: 6;
		line-clamp: 6;
		-webkit-box-orient: vertical;
		overflow: hidden;
	}
	.more {
		margin-top: 0.3rem;
		padding: 0;
		border: 0;
		background: none;
		color: var(--accent-strong);
		font-size: 0.88rem;
		font-weight: 600;
		cursor: pointer;
	}

	.edit {
		display: grid;
		gap: 0.75rem;
	}
	.edit-row {
		display: grid;
		grid-template-columns: 7.5rem minmax(0, 1fr) auto;
		align-items: center;
		gap: 0.5rem;
	}
	.edit-row label {
		font-size: 0.9rem;
		font-weight: 600;
		color: var(--text-muted);
	}
	.group {
		display: grid;
		gap: 0.4rem;
	}
	.group-label {
		font-size: 0.75rem;
		font-weight: 650;
		color: var(--text-faint);
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}
	.prog-row {
		display: grid;
		grid-template-columns: minmax(7rem, 0.8fr) minmax(0, 1.6fr) auto;
		align-items: center;
		gap: 0.4rem;
	}
	.prog-actions {
		display: flex;
	}
	.prog-chords {
		font-weight: 600;
		color: var(--accent-strong);
	}
	.strum-help {
		margin: 0;
		color: var(--text-faint);
		font-size: 0.82rem;
	}
	.strum-help b {
		color: var(--text-muted);
	}
	.strum-preview {
		grid-column: 2 / -1;
		padding: 0.1rem 0 0.25rem;
	}
	@media (max-width: 560px) {
		.edit-row {
			grid-template-columns: 6rem minmax(0, 1fr) auto;
		}
		.prog-row {
			grid-template-columns: minmax(0, 1fr) auto;
			padding-bottom: 0.4rem;
			border-bottom: 1px solid var(--border);
		}
		.prog-chords {
			grid-column: 1 / -1;
			grid-row: 2;
		}
		.strum-preview {
			grid-column: 1 / -1;
			grid-row: 3;
		}
	}
	.add {
		display: grid;
		gap: 0.4rem;
	}
	.add-chips {
		display: flex;
		flex-wrap: wrap;
		gap: 0.35rem;
	}
	.chip {
		display: inline-flex;
		align-items: center;
		gap: 0.25rem;
		padding: 0.3rem 0.7rem 0.3rem 0.55rem;
		border-radius: 999px;
		border: 1px dashed var(--border);
		background: transparent;
		color: var(--text-muted);
		font-size: 0.85rem;
		font-weight: 600;
		cursor: pointer;
	}
	.chip:hover {
		color: var(--text);
		border-color: var(--text-faint);
	}
	textarea {
		min-height: 100px;
	}
	.edit-actions {
		display: flex;
		gap: 0.4rem;
	}
</style>
