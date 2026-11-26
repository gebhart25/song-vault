<script lang="ts">
	import { beforeNavigate } from '$app/navigation';
	import { page } from '$app/state';
	import { ApiError, api } from '#lib/api.ts';
	import {
		addChordAfter,
		addChordBefore,
		chordsIn,
		chordsInKey,
		isChord,
		joinChords,
		layoutLine,
		lineSpots,
		namesAfter,
		parseLine,
		removeChord,
		setChord,
		splitChords,
		transposeChord,
		transposeChords,
		transposeKey,
		transposeLyrics
	} from '#lib/chords.ts';
	import Icon from '#lib/components/Icon.svelte';
	import NotebookDrawer from '#lib/components/NotebookDrawer.svelte';
	import StrumPattern from '#lib/components/StrumPattern.svelte';
	import { notebook, type LocalNote } from '#lib/notebook.svelte.ts';
	import type { LyricSection, Lyrics, Progression, ProjectDetail } from '#lib/types.ts';

	// The lyrics page (spec 5.6): labeled sections of lyrics with chords placed
	// over the words. Read it, write it, or tap chords into place.

	type Mode = 'read' | 'write' | 'chords';
	type Brush =
		| { kind: 'chord'; chord: string }
		| { kind: 'progression'; index: number; next: number }
		| { kind: 'erase' };

	const SCALE_KEY = 'songvault.lyricsScale';
	const COLUMNS_KEY = 'songvault.lyricsColumns';
	const STRUMMING_KEY = 'songvault.lyricsStrumming';
	const SCROLL_SPEED_KEY = 'songvault.scrollSpeed';
	/** Auto-scroll speeds in pixels per second at normal text size, slowest first */
	const SCROLL_SPEEDS = [4, 6, 8, 11, 15, 20, 26, 34, 45, 60];
	const SECTION_LABELS = ['Intro', 'Verse', 'Pre-Chorus', 'Chorus', 'Bridge', 'Outro'];
	const LABEL_OPTIONS = [...SECTION_LABELS, 'Verse 1', 'Verse 2', 'Verse 3', 'Post-Chorus', 'Instrumental', 'Solo', 'Tag', 'Breakdown'];

	const id = $derived(Number(page.params.id));

	let project = $state<ProjectDetail | null>(null);
	let sections = $state<LyricSection[]>([]);
	let revision = $state(0);
	let loadError = $state('');
	let mode = $state<Mode>('read');
	let scale = $state(Number(localStorage.getItem(SCALE_KEY)) || 1);
	/** Read the sheet (and print it) in two columns. Remembered on this device, like the text size. */
	let twoColumns = $state(localStorage.getItem(COLUMNS_KEY) === '2');
	/** Show the song notes' strumming patterns on the sheet (and so in print). Off until turned on. */
	let showStrumming = $state(localStorage.getItem(STRUMMING_KEY) === '1');

	let saveState = $state<'saved' | 'dirty' | 'saving' | 'error' | 'conflict'>('saved');
	let saveError = $state('');

	let brush = $state<Brush | null>(null);
	let customChord = $state('');
	let customError = $state('');
	let extraChords = $state<string[]>([]);
	let history = $state<string[]>([]);
	let nudge = $state(false);

	$effect(() => {
		localStorage.setItem(SCALE_KEY, String(scale));
		localStorage.setItem(COLUMNS_KEY, twoColumns ? '2' : '1');
		localStorage.setItem(STRUMMING_KEY, showStrumming ? '1' : '0');
	});

	$effect(() => {
		const projectId = id;
		project = null;
		loadError = '';
		Promise.all([api.get<ProjectDetail>(`/projects/${projectId}`), api.get<Lyrics>(`/projects/${projectId}/lyrics`)])
			.then(([p, l]) => {
				if (projectId !== id) return;
				project = p;
				sections = l.sections;
				revision = l.revision;
				saveState = 'saved';
				mode = l.sections.length > 0 ? 'read' : 'write';
			})
			.catch((e) => {
				loadError = e instanceof ApiError && e.status === 404 ? 'This project doesn’t exist.' : 'Could not load the lyrics.';
			});
	});

	// --- saving: shortly after each change, and when leaving -------------------------

	let timer: ReturnType<typeof setTimeout> | undefined;
	let inFlight = false;
	let again = false;

	function changed() {
		if (saveState === 'conflict') return;
		saveState = 'dirty';
		clearTimeout(timer);
		timer = setTimeout(save, 900);
	}

	async function save() {
		clearTimeout(timer);
		if (saveState === 'conflict' || saveState === 'saved') return;
		if (inFlight) {
			again = true;
			return;
		}
		inFlight = true;
		saveState = 'saving';
		const sent = $state.snapshot(sections);
		const sentJson = JSON.stringify(sent);
		try {
			const saved = await api.put<Lyrics>(`/projects/${id}/lyrics`, { sections: sent, revision });
			revision = saved.revision;
			if (project) project.lyric_sections = saved.sections.length;
			saveState = JSON.stringify($state.snapshot(sections)) === sentJson ? 'saved' : 'dirty';
		} catch (e) {
			again = false;
			if (e instanceof ApiError && e.status === 409) saveState = 'conflict';
			else {
				saveState = 'error';
				saveError = e instanceof ApiError ? e.message : 'Couldn’t save.';
			}
		} finally {
			inFlight = false;
		}
		if (again) {
			again = false;
			save();
		}
	}

	async function loadLatest() {
		const latest = await api.get<Lyrics>(`/projects/${id}/lyrics`);
		sections = latest.sections;
		revision = latest.revision;
		history = [];
		saveState = 'saved';
	}

	async function keepMine() {
		const latest = await api.get<Lyrics>(`/projects/${id}/lyrics`);
		revision = latest.revision;
		saveState = 'dirty';
		await save();
	}

	beforeNavigate(() => {
		if (saveState === 'dirty') save();
	});

	$effect(() => {
		const onHide = () => document.visibilityState === 'hidden' && saveState === 'dirty' && save();
		const onUnload = (e: BeforeUnloadEvent) => {
			if (saveState === 'dirty' || saveState === 'saving') e.preventDefault();
		};
		document.addEventListener('visibilitychange', onHide);
		window.addEventListener('beforeunload', onUnload);
		return () => {
			document.removeEventListener('visibilitychange', onHide);
			window.removeEventListener('beforeunload', onUnload);
		};
	});

	// --- writing --------------------------------------------------------------------

	const newId = () => Math.random().toString(36).slice(2, 10);

	function addSection(label: string) {
		let name = label;
		if (label === 'Verse') {
			const verses = sections.filter((s) => /^verse\b/i.test(s.label)).length;
			name = `Verse ${verses + 1}`;
		}
		const section: LyricSection = { id: newId(), label: name, kind: 'lyrics', text: '' };
		sections.push(section);
		changed();
		focusSoon(`text-${section.id}`);
	}

	// --- the Notebook drawer (spec 5.11, step 2) ----------------------------------------

	const DRAWER_KEY = 'songvault.lyricsNotebook';
	let drawerOpen = $state(localStorage.getItem(DRAWER_KEY) === '1');
	$effect(() => {
		localStorage.setItem(DRAWER_KEY, drawerOpen ? '1' : '0');
	});
	/** Where the cursor was last in a lyrics section, for "At cursor" */
	let caret = $state<{ id: string; at: number } | null>(null);

	function rememberCaret(id: string, el: HTMLTextAreaElement) {
		caret = { id, at: el.selectionEnd };
	}

	/** A note from the Notebook into the lyrics: at the cursor (on lines of its own) or as a
	 * new section. The note is then linked to this song and marked Used. */
	function insertNote(note: LocalNote, text: string, where: 'cursor' | 'section') {
		if (!project) return;
		const target = where === 'cursor' && caret ? sections.find((s) => s.id === caret?.id && s.kind === 'lyrics') : null;
		if (target && caret) {
			const at = Math.min(caret.at, target.text.length);
			const before = target.text.slice(0, at);
			const after = target.text.slice(at);
			const pre = before && !before.endsWith('\n') ? '\n' : '';
			const post = after && !after.startsWith('\n') ? '\n' : '';
			target.text = before + pre + text + post + after;
			const end = (before + pre + text).length;
			caret = { id: target.id, at: end };
			setTimeout(() => {
				const el = document.getElementById(`text-${target.id}`) as HTMLTextAreaElement | null;
				el?.focus();
				el?.setSelectionRange(end, end);
			}, 0);
		} else {
			const section: LyricSection = { id: newId(), label: 'Idea', kind: 'lyrics', text };
			sections.push(section);
			focusSoon(`label-${section.id}`);
		}
		changed();
		notebook.usedIn(note.uid, { id: project.id, name: project.name, in_trash: false });
	}

	function moveSection(i: number, direction: -1 | 1) {
		const j = i + direction;
		if (j < 0 || j >= sections.length) return;
		[sections[i], sections[j]] = [sections[j], sections[i]];
		changed();
	}

	function removeSection(i: number) {
		const s = sections[i];
		if (s.text.trim() && !confirm(`Delete the section “${s.label || 'Untitled'}” and its lyrics?`)) return;
		sections.splice(i, 1);
		changed();
	}

	// --- chords-only sections (an instrumental intro, a solo...) ------------------------

	const lyricSections = $derived(sections.filter((s) => s.kind !== 'chords'));

	/** "D Bm G A\n\nEm, A7" -> ["D - Bm - G - A", "Em - A7"] */
	const chordLines = (text: string) =>
		text
			.split('\n')
			.map((line) => joinChords(splitChords(line)))
			.filter(Boolean);

	/** Only offered while the section is empty, so nothing typed gets hidden. */
	function setKind(s: LyricSection, kind: LyricSection['kind']) {
		if (s.kind === kind) return;
		s.kind = kind;
		changed();
		focusSoon(`text-${s.id}`);
	}

	function tidyChords(s: LyricSection) {
		const tidy = chordLines(s.text).join('\n');
		if (tidy === s.text) return;
		s.text = tidy;
		changed();
	}

	function useProgression(s: LyricSection, p: Progression) {
		s.text = joinChords(p.chords);
		changed();
	}

	function focusSoon(id: string) {
		setTimeout(() => document.getElementById(id)?.focus(), 0);
	}

	// --- placing chords ----------------------------------------------------------------

	const progressions = $derived<Progression[]>(project?.progressions ?? []);
	const songChords = $derived(
		[
			...new Set([
				...progressions.flatMap((p) => p.chords),
				...chordsIn(lyricSections.map((s) => s.text).join('\n')),
				...sections.filter((s) => s.kind === 'chords').flatMap((s) => splitChords(s.text)),
				...extraChords
			])
		]
	);
	const keyChords = $derived(chordsInKey(project?.song_key).filter((c) => !songChords.includes(c)));
	const brushChord = $derived(
		!brush
			? null
			: brush.kind === 'chord'
				? brush.chord
				: brush.kind === 'progression'
					? (progressions[brush.index]?.chords[brush.next] ?? null)
					: null
	);

	function pick(next: Brush) {
		const same =
			brush &&
			next.kind === brush.kind &&
			(next.kind === 'erase' ||
				(next.kind === 'chord' && brush.kind === 'chord' && next.chord === brush.chord) ||
				(next.kind === 'progression' && brush.kind === 'progression' && next.index === brush.index));
		brush = same ? null : next;
	}

	function useCustom(event: SubmitEvent) {
		event.preventDefault();
		const chord = customChord.trim();
		if (!isChord(chord)) {
			customError = 'Up to 16 characters, no brackets.';
			return;
		}
		customError = '';
		if (!songChords.includes(chord)) extraChords.push(chord);
		brush = { kind: 'chord', chord };
		customChord = '';
	}

	/** What was tapped: a letter or a chord already placed (`at`), or the + at either end of a line. */
	type Target = { kind: 'at'; index: number; existing: string | null } | { kind: 'before' } | { kind: 'after' };

	function tap(sectionIndex: number, lineIndex: number, target: Target) {
		const existing = target.kind === 'at' ? target.existing : null;
		if (!brush) {
			if (existing) brush = { kind: 'chord', chord: existing };
			else {
				nudge = true;
				setTimeout(() => (nudge = false), 900);
			}
			return;
		}
		const lines = sections[sectionIndex].text.split('\n');
		const line = lines[lineIndex];
		let next: string;
		if (brush.kind === 'erase') {
			if (target.kind !== 'at' || !existing) return;
			next = removeChord(line, target.index);
		} else {
			let chord: string;
			if (brush.kind === 'chord') chord = brush.chord;
			else {
				const p = progressions[brush.index];
				if (!p) return;
				chord = p.chords[brush.next];
				brush.next = (brush.next + 1) % p.chords.length;
			}
			next =
				target.kind === 'before'
					? addChordBefore(line, chord)
					: target.kind === 'after'
						? addChordAfter(line, chord)
						: setChord(line, target.index, chord);
		}
		history.push(JSON.stringify($state.snapshot(sections)));
		if (history.length > 100) history.shift();
		lines[lineIndex] = next;
		sections[sectionIndex].text = lines.join('\n');
		changed();
	}

	function undo() {
		const previous = history.pop();
		if (!previous) return;
		sections = JSON.parse(previous);
		changed();
	}

	// --- reading: transpose, auto-scroll, print (spec 5.6) ------------------------------

	let shift = $state(0);
	let toolError = $state('');
	const spelling = $derived(namesAfter(project?.song_key, shift));
	const shownKey = $derived(shift ? transposeKey(project?.song_key, shift) : (project?.song_key ?? null));
	/** Chords as shown on the sheet: moved by `shift` while transposing. */
	const tx = (chords: string) => (shift ? transposeChords(chords, shift, spelling) : chords);
	const hasChords = $derived(sections.some((s) => (s.kind === 'chords' ? s.text.trim() !== '' : s.text.includes('['))));
	const signed = (n: number) => (n > 0 ? `+${n}` : `−${-n}`);

	function transpose(by: 1 | -1) {
		shift = Math.max(-11, Math.min(11, shift + by));
	}

	/** Rewrite the sheet and the song notes' key and progressions in the key shown. */
	async function saveInThisKey() {
		if (!project || shift === 0) return;
		const where = shownKey ? `the key of ${shownKey}` : `${signed(shift)} half steps`;
		const ok = confirm(
			`Rewrite the chords in ${where}? This changes the chords on this page and the key and progressions in the song notes. Recordings keep their own details.`
		);
		if (!ok) return;
		const by = shift;
		const names = spelling;
		toolError = '';
		for (const s of sections) {
			s.text = s.kind === 'chords' ? transposeChords(s.text, by, names) : transposeLyrics(s.text, by, names);
		}
		history = [];
		shift = 0;
		changed();
		save();
		try {
			const changes: Record<string, unknown> = {
				progressions: project.progressions.map((p) => ({
					label: p.label,
					chords: p.chords.map((c) => transposeChord(c, by, names))
				}))
			};
			const key = transposeKey(project.song_key, by);
			if (key) changes.song_key = key;
			project = await api.patch<ProjectDetail>(`/projects/${id}`, changes);
		} catch (e) {
			toolError = e instanceof ApiError ? e.message : 'The song notes weren’t updated.';
		}
	}

	let scrollBar = $state(false);
	let scrolling = $state(false);
	let speedLevel = $state(Math.min(10, Math.max(1, Number(localStorage.getItem(SCROLL_SPEED_KEY)) || 4)));

	$effect(() => {
		localStorage.setItem(SCROLL_SPEED_KEY, String(speedLevel));
	});

	const atBottom = () => window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2;

	function toggleScroll() {
		if (!scrolling && atBottom()) window.scrollTo({ top: 0 });
		scrolling = !scrolling;
	}

	function startScroll() {
		scrollBar = true;
		scrolling = false;
		toggleScroll();
	}

	function stopScroll() {
		scrollBar = false;
		scrolling = false;
	}

	function setMode(next: Mode) {
		if (next !== 'read') stopScroll();
		mode = next;
	}

	// Scroll a little every frame; whole pixels only, carrying the remainder.
	$effect(() => {
		if (!scrolling) return;
		const perSecond = SCROLL_SPEEDS[speedLevel - 1] * scale;
		let last = performance.now();
		let carry = 0;
		let frame = requestAnimationFrame(function tick(now) {
			carry += perSecond * Math.min(0.1, (now - last) / 1000);
			last = now;
			const step = Math.floor(carry);
			if (step > 0) {
				carry -= step;
				window.scrollBy(0, step);
			}
			if (atBottom()) {
				scrolling = false;
				return;
			}
			frame = requestAnimationFrame(tick);
		});
		return () => cancelAnimationFrame(frame);
	});

	// Keep the screen on while playing along.
	$effect(() => {
		if (!scrollBar || !('wakeLock' in navigator)) return;
		let lock: WakeLockSentinel | undefined;
		let stopped = false;
		const acquire = () => {
			if (document.visibilityState !== 'visible') return;
			navigator.wakeLock
				.request('screen')
				.then((l) => {
					if (stopped) l.release();
					else lock = l;
				})
				.catch(() => {});
		};
		acquire();
		document.addEventListener('visibilitychange', acquire);
		return () => {
			stopped = true;
			lock?.release();
			document.removeEventListener('visibilitychange', acquire);
		};
	});

	const facts = $derived(
		!project
			? []
			: [
					shownKey && `Key ${shownKey}`,
					project.bpm && `${Math.round(project.bpm * 100) / 100} BPM`,
					project.time_signature,
					project.capo && `Capo ${project.capo}`,
					project.tuning && project.tuning !== 'Standard' && project.tuning
				].filter(Boolean)
	);
	const hasWords = $derived(sections.some((s) => s.text.trim()));
	const hasLyrics = $derived(lyricSections.some((s) => s.text.trim()));
</script>

<svelte:window onkeydown={(e) => e.key === 'Escape' && mode === 'chords' && (brush = null)} />

<div
	class="page"
	class:chords-mode={mode === 'chords'}
	class:scroll-mode={scrollBar && mode === 'read'}
	class:with-drawer={drawerOpen && mode === 'write'}
>
	{#if loadError}
		<a href="/" class="back"><Icon name="arrow-left" size={18} /> Projects</a>
		<p class="error" role="alert">{loadError}</p>
	{:else if !project}
		<div class="loading" aria-busy="true"></div>
	{:else}
		<a href="/projects/{project.id}" class="back"><Icon name="arrow-left" size={18} /> {project.name}</a>

		<header class="head">
			<div class="title">
				<h1 class="screen-title">Lyrics &amp; chords</h1>
				<h1 class="print-title">{project.name}</h1>
				<span class="save-state" data-state={saveState} aria-live="polite">
					{#if saveState === 'saving'}Saving…{:else if saveState === 'dirty'}Editing{:else if saveState === 'saved' && revision > 0}Saved{:else if saveState === 'error'}Not saved{/if}
				</span>
			</div>
			{#if facts.length > 0}<p class="facts">{facts.join(' · ')}</p>{/if}
			{#if mode === 'read' && showStrumming && project.strumming.length > 0}
				<div class="strums">
					<span class="strums-title">Strumming</span>
					{#each project.strumming as st, i (i)}
						<span class="strum">
							{#if st.label}<span class="strum-label">{st.label}:</span>{/if}
							<StrumPattern pattern={st.pattern} timeSignature={project.time_signature} />
						</span>
					{/each}
				</div>
			{/if}

			<div class="toolbar">
				<div class="tabs" role="group" aria-label="View">
					<button aria-pressed={mode === 'read'} onclick={() => setMode('read')}>Read</button>
					<button aria-pressed={mode === 'write'} onclick={() => setMode('write')}>Write</button>
					<button aria-pressed={mode === 'chords'} onclick={() => setMode('chords')}>Chords</button>
				</div>
				{#if mode === 'write'}
					<button class="btn btn-sm" aria-pressed={drawerOpen} onclick={() => (drawerOpen = !drawerOpen)}>
						<Icon name="notes" size={16} /> Notebook
					</button>
				{/if}
				{#if mode === 'read' && hasWords}
					<div class="zoom" role="group" aria-label="Text size">
						<button class="btn btn-sm btn-icon" aria-label="Smaller text" disabled={scale <= 0.8} onclick={() => (scale = Math.round((scale - 0.1) * 10) / 10)}>A−</button>
						<button class="btn btn-sm btn-icon" aria-label="Larger text" disabled={scale >= 1.8} onclick={() => (scale = Math.round((scale + 0.1) * 10) / 10)}>A+</button>
					</div>
				{/if}
			</div>

			{#if mode === 'read' && hasWords}
				<div class="sheet-tools">
					{#if hasChords}
						<div class="transpose" role="group" aria-label="Transpose">
							<button class="btn btn-sm btn-icon" aria-label="Down a half step" disabled={shift <= -11} onclick={() => transpose(-1)}>
								<Icon name="minus" size={16} />
							</button>
							<span class="shift" aria-live="polite">
								{#if shift === 0}Transpose{:else}{signed(shift)}{#if shownKey}&nbsp;· {shownKey}{/if}{/if}
							</span>
							<button class="btn btn-sm btn-icon" aria-label="Up a half step" disabled={shift >= 11} onclick={() => transpose(1)}>
								<Icon name="plus" size={16} />
							</button>
						</div>
						{#if shift !== 0}
							<button class="btn btn-sm btn-ghost" onclick={() => (shift = 0)}>Reset</button>
							<button class="btn btn-sm" onclick={saveInThisKey}>Save in this key</button>
						{/if}
					{/if}
					<span class="spacer"></span>
					{#if project.strumming.length > 0}
						<button class="btn btn-sm" aria-pressed={showStrumming} onclick={() => (showStrumming = !showStrumming)}>
							<Icon name="strum" size={16} /> Strumming
						</button>
					{/if}
					<button class="btn btn-sm" aria-pressed={twoColumns} onclick={() => (twoColumns = !twoColumns)}>
						<Icon name="columns" size={16} /> Two columns
					</button>
					<button class="btn btn-sm" aria-pressed={scrollBar} onclick={() => (scrollBar ? stopScroll() : startScroll())}>
						<Icon name="play" size={15} /> Auto-scroll
					</button>
					<button class="btn btn-sm" onclick={() => window.print()}><Icon name="printer" size={16} /> Print</button>
				</div>
				{#if toolError}<p class="error" role="alert">{toolError}</p>{/if}
			{/if}
		</header>

		{#if saveState === 'conflict'}
			<div class="banner" role="alert">
				<p>These lyrics were changed on another device while you were editing here.</p>
				<div class="banner-actions">
					<button class="btn btn-sm" onclick={loadLatest}>Load the other version</button>
					<button class="btn btn-sm" onclick={keepMine}>Keep mine</button>
				</div>
			</div>
		{:else if saveState === 'error'}
			<div class="banner" role="alert">
				<p>{saveError}</p>
				<div class="banner-actions"><button class="btn btn-sm" onclick={() => ((saveState = 'dirty'), save())}>Try again</button></div>
			</div>
		{/if}

		{#if mode === 'read'}
			{#if !hasWords}
				<div class="empty">
					<div class="empty-icon"><Icon name="lyrics" size={26} /></div>
					<h2>No lyrics yet</h2>
					<p class="muted">Write the words in sections, then tap chords into place above them.</p>
					<button class="btn btn-primary" onclick={() => (mode = 'write')}><Icon name="edit" size={18} /> Start writing</button>
				</div>
			{:else}
				{#if twoColumns}<p class="columns-note">Two columns show on wider screens and when printing.</p>{/if}
				<!-- Tapping the sheet pauses or resumes auto-scroll (a big target while holding a guitar). -->
				<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions -->
				<article class="sheet" class:two-columns={twoColumns} style:--scale={scale} onclick={() => scrollBar && toggleScroll()}>
					{#each sections as s (s.id)}
						<section class="sheet-section">
							{#if s.label}<h2 class="sec-label">{s.label}</h2>{/if}
							{#if s.kind === 'chords'}
								{#each chordLines(s.text) as line, li (li)}<p class="chord-line">{tx(line)}</p>{/each}
							{:else}
							{#each s.text.split('\n') as line, li (li)}
								{@const words = layoutLine(parseLine(line))}
								{#if words.length === 0}
									<div class="line blank"></div>
								{:else}
									<div class="line" class:with-chords={line.includes('[')}>
										{#each words as word, wi (wi)}
											<span class="word">
												{#each word as piece, pi (pi)}
													<span class="piece">
														<span class="chord">{piece.chord ? tx(piece.chord) : ''}</span>
														<span class="lyric">{piece.text}</span>
													</span>
												{/each}
											</span>
										{/each}
									</div>
								{/if}
							{/each}
							{/if}
						</section>
					{/each}
				</article>
			{/if}
		{:else if mode === 'write'}
			<p class="hint">
				Chords show up in [brackets] here. Use <strong>Chords</strong> to place them by tapping. A chord before the first word
				(<code>[G] Headlights…</code>) is played before the line starts; one after the last word, after it ends.
			</p>
			<div class="write">
				{#each sections as s, i (s.id)}
					<div class="sec-edit">
						<div class="sec-head">
							<label class="visually-hidden" for="label-{s.id}">Section label</label>
							<input
								id="label-{s.id}"
								class="field sec-label-input"
								placeholder="Label, e.g. Verse 1"
								maxlength="60"
								list="section-labels"
								bind:value={s.label}
								oninput={changed}
							/>
							<button class="btn btn-ghost btn-sm btn-icon" aria-label="Move section up" disabled={i === 0} onclick={() => moveSection(i, -1)}>
								<Icon name="arrow-up" size={17} />
							</button>
							<button class="btn btn-ghost btn-sm btn-icon" aria-label="Move section down" disabled={i === sections.length - 1} onclick={() => moveSection(i, 1)}>
								<Icon name="arrow-down" size={17} />
							</button>
							<button class="btn btn-ghost btn-sm btn-icon btn-danger" aria-label="Delete section" onclick={() => removeSection(i)}>
								<Icon name="trash" size={17} />
							</button>
						</div>
						{#if !s.text.trim()}
							<div class="kind" role="group" aria-label="What goes in {s.label || 'this section'}">
								<button class="chip" aria-pressed={s.kind !== 'chords'} onclick={() => setKind(s, 'lyrics')}>Lyrics</button>
								<button class="chip" aria-pressed={s.kind === 'chords'} onclick={() => setKind(s, 'chords')}>Chords only</button>
							</div>
						{/if}
						{#if s.kind === 'chords'}
							<label class="visually-hidden" for="text-{s.id}">Chords for {s.label || 'this section'}</label>
							<textarea
								id="text-{s.id}"
								class="field chords-text"
								placeholder="D - Bm - G - A"
								autocomplete="off"
								autocapitalize="characters"
								spellcheck="false"
								bind:value={s.text}
								oninput={changed}
								onblur={() => tidyChords(s)}
							></textarea>
							{#if !s.text.trim() && progressions.length > 0}
								<div class="use-progression">
									<span class="add-label">Use</span>
									{#each progressions as p, pi (pi)}
										<button class="chip" onclick={() => useProgression(s, p)}>
											{p.label}: <span class="chords">{joinChords(p.chords)}</span>
										</button>
									{/each}
								</div>
							{/if}
						{:else}
							<label class="visually-hidden" for="text-{s.id}">Lyrics for {s.label || 'this section'}</label>
							<textarea
								id="text-{s.id}"
								class="field lyrics-text"
								placeholder="Lyrics…"
								bind:value={s.text}
								oninput={changed}
								onblur={(e) => rememberCaret(s.id, e.currentTarget)}
							></textarea>
						{/if}
					</div>
				{/each}
				<datalist id="section-labels">
					{#each LABEL_OPTIONS as o (o)}<option value={o}></option>{/each}
				</datalist>

				<div class="add-sections">
					<span class="add-label">Add a section</span>
					<div class="add-chips">
						{#each SECTION_LABELS as label (label)}
							<button class="chip dashed" onclick={() => addSection(label)}><Icon name="plus" size={14} /> {label}</button>
						{/each}
						<button class="chip dashed" onclick={() => addSection('')}><Icon name="plus" size={14} /> Other</button>
					</div>
				</div>
			</div>
		{:else}
			{#if !hasLyrics}
				<div class="empty">
					<h2>Write some lyrics first</h2>
					<p class="muted">Chords are placed over the words.</p>
					<button class="btn btn-primary" onclick={() => (mode = 'write')}>Write lyrics</button>
				</div>
			{:else}
				<article class="sheet placing" style:--scale={1.1}>
					{#each sections as s, si (s.id)}
						<section class="sheet-section">
							{#if s.label}<h2 class="sec-label">{s.label}</h2>{/if}
							{#if s.kind === 'chords'}
								{#each chordLines(s.text) as line, li (li)}<p class="chord-line">{line}</p>{/each}
							{:else}
							{#each s.text.split('\n') as line, li (li)}
								{@const spots = lineSpots(parseLine(line))}
								<div class="line with-chords">
									<!-- Chords played before the words start -->
									{#if spots.words.length > 0}
										<span class="word edge">
											{#each spots.before as c (c.index)}
												<button class="spot slot has" aria-label="{c.chord}, before the line" onclick={() => tap(si, li, { kind: 'at', index: c.index, existing: c.chord })}>
													<span class="chord">{c.chord}</span>
													<span class="lyric">·</span>
												</button>
											{/each}
											<button class="spot slot add" aria-label="Add a chord before this line" title="Chord before the line" onclick={() => tap(si, li, { kind: 'before' })}>
												<span class="chord"></span>
												<span class="lyric">+</span>
											</button>
										</span>
									{/if}
									{#each spots.words as word, wi (wi)}
										<span class="word">
											{#each word as spot (spot.index)}
												<button class="spot" class:has={spot.chord} onclick={() => tap(si, li, { kind: 'at', index: spot.index, existing: spot.chord })}>
													<span class="chord">{spot.chord ?? ''}</span>
													<span class="lyric">{spot.char}</span>
												</button>
											{/each}
										</span>
									{/each}
									<!-- Chords played after the last word -->
									<span class="word edge after">
										{#each spots.after as c (c.index)}
											<button class="spot slot has" aria-label="{c.chord}, after the line" onclick={() => tap(si, li, { kind: 'at', index: c.index, existing: c.chord })}>
												<span class="chord">{c.chord}</span>
												<span class="lyric">·</span>
											</button>
										{/each}
										<button class="spot slot add" aria-label="Add a chord after this line" title="Chord after the line" onclick={() => tap(si, li, { kind: 'after' })}>
											<span class="chord"></span>
											<span class="lyric">+</span>
										</button>
									</span>
								</div>
							{/each}
							{/if}
						</section>
					{/each}
				</article>
			{/if}
		{/if}
	{/if}
</div>

{#if project && drawerOpen && mode === 'write'}
	<NotebookDrawer projectId={project.id} canInsert={caret !== null} oninsert={insertNote} onclose={() => (drawerOpen = false)} />
{/if}

{#if scrollBar && mode === 'read'}
	<div class="scroll-bar" role="toolbar" aria-label="Auto-scroll">
		<button class="btn btn-primary btn-icon" aria-label={scrolling ? 'Pause' : 'Play'} onclick={toggleScroll}>
			<Icon name={scrolling ? 'pause' : 'play'} />
		</button>
		<div class="speed" role="group" aria-label="Scroll speed">
			<button class="btn btn-sm btn-icon" aria-label="Slower" disabled={speedLevel <= 1} onclick={() => speedLevel--}>
				<Icon name="minus" size={16} />
			</button>
			<span class="speed-value">Speed {speedLevel}</span>
			<button class="btn btn-sm btn-icon" aria-label="Faster" disabled={speedLevel >= SCROLL_SPEEDS.length} onclick={() => speedLevel++}>
				<Icon name="plus" size={16} />
			</button>
		</div>
		<span class="scroll-hint">Tap the lyrics to pause</span>
		<button class="btn btn-ghost btn-sm btn-icon" aria-label="Stop auto-scroll" onclick={stopScroll}><Icon name="x" /></button>
	</div>
{/if}

{#if project && mode === 'chords' && hasLyrics}
	<div class="palette" role="toolbar" aria-label="Chords">
		<div class="palette-top">
			<p class="brush-hint" class:nudge>
				{#if !brush}
					Pick a chord, then tap where it changes, or <b class="plus">+</b> at either end of a line.
				{:else if brush.kind === 'erase'}
					Tap a chord to remove it.
				{:else if brush.kind === 'progression'}
					Tap to place <b>{brushChord}</b>
					<span class="muted">· {progressions[brush.index]?.label}, chord {brush.next + 1} of {progressions[brush.index]?.chords.length}</span>
				{:else}
					Tap to place <b>{brushChord}</b>
				{/if}
			</p>
			<button class="btn btn-sm" disabled={history.length === 0} onclick={undo}><Icon name="undo" size={16} /> Undo</button>
		</div>

		{#if progressions.length > 0}
			<div class="palette-row">
				<span class="row-label">Progressions</span>
				{#each progressions as p, i (i)}
					<button
						class="chip prog"
						aria-pressed={brush?.kind === 'progression' && brush.index === i}
						onclick={() => pick({ kind: 'progression', index: i, next: 0 })}
					>
						{p.label}: <span class="chords">{joinChords(p.chords)}</span>
					</button>
				{/each}
			</div>
		{/if}
		{#if songChords.length > 0}
			<div class="palette-row">
				<span class="row-label">This song</span>
				{#each songChords as chord (chord)}
					<button class="chip chord" aria-pressed={brush?.kind === 'chord' && brush.chord === chord} onclick={() => pick({ kind: 'chord', chord })}>
						{chord}
					</button>
				{/each}
			</div>
		{/if}
		{#if keyChords.length > 0}
			<div class="palette-row">
				<span class="row-label">Key of {project.song_key}</span>
				{#each keyChords as chord (chord)}
					<button class="chip chord" aria-pressed={brush?.kind === 'chord' && brush.chord === chord} onclick={() => pick({ kind: 'chord', chord })}>
						{chord}
					</button>
				{/each}
			</div>
		{/if}
		<div class="palette-bottom">
			<form class="custom" onsubmit={useCustom}>
				<label class="visually-hidden" for="custom-chord">Another chord</label>
				<input
					id="custom-chord"
					class="field"
					placeholder="Other chord, e.g. F#m7"
					maxlength="16"
					autocomplete="off"
					autocapitalize="characters"
					spellcheck="false"
					bind:value={customChord}
				/>
				<button class="btn btn-sm" type="submit" disabled={!customChord.trim()}>Use</button>
			</form>
			<button class="chip erase" aria-pressed={brush?.kind === 'erase'} onclick={() => pick({ kind: 'erase' })}>
				<Icon name="eraser" size={15} /> Erase
			</button>
		</div>
		{#if customError}<p class="error">{customError}</p>{/if}
	</div>
{/if}

<style>
	.page {
		max-width: 900px;
		margin: 0 auto;
		padding: 0.9rem var(--gutter) 2rem;
	}
	.page.chords-mode {
		padding-bottom: 19rem;
	}
	/* Room for the Notebook drawer: under it on phones, beside it on wide screens */
	.page.with-drawer {
		padding-bottom: 64dvh;
	}
	@media (min-width: 900px) {
		.page.with-drawer {
			max-width: calc(900px + 360px);
			padding-right: calc(var(--gutter) + 360px);
			padding-bottom: 2rem;
		}
	}
	/* Room for the last lines to scroll up to eye level */
	.page.scroll-mode {
		padding-bottom: 55vh;
	}
	.loading {
		height: 50vh;
	}
	.back {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
		margin-bottom: 0.6rem;
		color: var(--text-muted);
		font-size: 0.9rem;
		font-weight: 550;
		overflow-wrap: anywhere;
	}
	.back:hover {
		color: var(--text);
	}
	.head {
		margin-bottom: 1rem;
	}
	.title {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 1rem;
	}
	h1 {
		font-size: 1.55rem;
	}
	.save-state {
		color: var(--text-faint);
		font-size: 0.85rem;
		white-space: nowrap;
	}
	.save-state[data-state='error'] {
		color: var(--danger);
	}
	.facts {
		margin: 0.3rem 0 0;
		color: var(--text-muted);
		font-size: 0.92rem;
		font-weight: 550;
	}
	/* The song notes' strumming patterns, when turned on (spec 5.6) */
	.strums {
		display: flex;
		flex-wrap: wrap;
		align-items: flex-start;
		gap: 0.35rem 1.5rem;
		margin-top: 0.55rem;
	}
	.strums-title {
		padding-top: 0.2rem;
		color: var(--text-faint);
		font-size: 0.75rem;
		font-weight: 650;
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}
	.strum {
		display: inline-flex;
		align-items: flex-start;
		gap: 0.45rem;
	}
	.strum-label {
		padding-top: 0.1rem;
		color: var(--text-muted);
		font-size: 0.92rem;
		font-weight: 600;
	}
	.toolbar {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
		margin-top: 0.85rem;
	}
	.tabs {
		display: inline-flex;
		padding: 3px;
		border-radius: 10px;
		background: var(--surface);
		border: 1px solid var(--border);
	}
	.tabs button {
		min-width: 4.6rem;
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
	.zoom {
		display: flex;
		gap: 0.3rem;
	}
	.zoom .btn {
		font-size: 0.8rem;
		font-weight: 700;
	}
	.print-title {
		display: none;
	}

	/* --- transpose, auto-scroll, print --- */
	.sheet-tools {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.4rem;
		margin-top: 0.6rem;
	}
	.transpose {
		display: inline-flex;
		align-items: center;
		gap: 0.25rem;
	}
	.shift {
		min-width: 5.2rem;
		text-align: center;
		font-size: 0.88rem;
		font-weight: 650;
		font-variant-numeric: tabular-nums;
	}
	.spacer {
		flex: 1;
	}
	.sheet-tools [aria-pressed='true'] {
		background: var(--accent-soft);
		border-color: rgb(240 166 70 / 0.45);
		color: var(--accent-strong);
	}
	.scroll-bar {
		position: fixed;
		left: 50%;
		bottom: calc(1rem + env(safe-area-inset-bottom));
		z-index: 15;
		translate: -50% 0;
		display: flex;
		align-items: center;
		gap: 0.6rem;
		width: max-content;
		max-width: calc(100vw - 1.5rem);
		padding: 0.5rem 0.6rem;
		border-radius: 16px;
		background: rgb(24 27 33 / 0.96);
		backdrop-filter: blur(10px);
		border: 1px solid var(--border);
		box-shadow: 0 12px 30px rgb(0 0 0 / 0.45);
	}
	.speed {
		display: inline-flex;
		align-items: center;
		gap: 0.3rem;
	}
	.speed-value {
		min-width: 4.6rem;
		text-align: center;
		font-size: 0.9rem;
		font-weight: 650;
		font-variant-numeric: tabular-nums;
	}
	.scroll-hint {
		color: var(--text-faint);
		font-size: 0.8rem;
	}
	@media (max-width: 480px) {
		.scroll-hint {
			display: none;
		}
	}

	.banner {
		display: grid;
		gap: 0.5rem;
		margin-bottom: 1rem;
		padding: 0.7rem 0.9rem;
		border-radius: var(--radius-sm);
		background: var(--danger-soft);
		border: 1px solid rgb(240 106 106 / 0.35);
	}
	.banner p {
		margin: 0;
		font-size: 0.93rem;
	}
	.banner-actions {
		display: flex;
		flex-wrap: wrap;
		gap: 0.4rem;
	}
	.hint {
		margin: 0 0 0.9rem;
		color: var(--text-faint);
		font-size: 0.87rem;
	}

	/* --- the chord sheet --- */
	.sheet {
		font-size: calc(1.05rem * var(--scale, 1));
		line-height: 1.35;
	}
	.sheet-section {
		margin-bottom: 1.5em;
	}
	.sec-label {
		margin-bottom: 0.35em;
		font-size: 0.78em;
		font-weight: 700;
		color: var(--text-muted);
		text-transform: uppercase;
		letter-spacing: 0.06em;
	}
	.line {
		display: flex;
		flex-wrap: wrap;
		align-items: flex-end;
		min-height: 1.35em;
	}
	.line.blank {
		height: 0.9em;
		min-height: 0;
	}
	.word {
		display: flex;
	}
	.piece {
		display: flex;
		flex-direction: column;
	}
	.chord {
		display: none;
		padding-right: 0.45em;
		color: var(--accent-strong);
		font-weight: 700;
		font-size: 0.92em;
		white-space: pre;
	}
	.with-chords .chord {
		display: block;
		min-height: 1.3em;
	}
	.lyric {
		white-space: pre;
		min-height: 1.35em; /* keeps a chord after the last word up on the chord row */
	}
	.with-chords {
		margin-top: 0.15em;
	}

	/* --- placing chords: every letter is a tap target --- */
	.placing .line {
		margin-bottom: 0.2em;
	}
	.spot {
		display: flex;
		flex-direction: column;
		padding: 0.1em 0 0.15em;
		border: 0;
		border-radius: 4px;
		background: transparent;
		color: inherit;
		font: inherit;
		cursor: pointer;
	}
	.spot:hover {
		background: var(--surface-2);
	}
	/* The chord name hangs over the following letters instead of widening its
	   own, so the words don't shift around between taps. */
	.spot .chord {
		width: 0;
		padding: 0;
		overflow: visible;
		text-align: left;
	}
	.spot .lyric {
		min-width: 0.3em;
	}
/* The + at each end of a line, and chords placed before or after the words. Their
	   chord names take their own width, since there are no letters under them to keep. */
	.edge {
		gap: 0.05em;
	}
	.edge:not(.after) {
		margin-right: 0.35em;
	}
	.edge.after {
		margin-left: 0.35em;
	}
	.spot.slot .chord {
		width: auto;
		min-width: 0.8em;
		padding-right: 0.25em;
	}
	.spot.slot .lyric {
		padding: 0 0.25em;
		color: var(--text-faint);
		font-size: 0.85em;
		text-align: center;
	}
	.spot.add .lyric {
		display: grid;
		place-items: center;
		min-width: 1.45em;
		height: 1.45em;
		margin-top: 0.05em;
		padding: 0;
		border: 1px dashed #4a5262;
		border-radius: 6px;
		color: var(--text-muted);
		font-size: 0.8em;
		font-weight: 600;
		line-height: 1;
	}
	.spot.add:hover {
		background: transparent;
	}
	.spot.add:hover .lyric {
		border-color: var(--accent);
		color: var(--accent-strong);
	}

	/* --- writing --- */
	.write {
		display: grid;
		gap: 1rem;
	}
	.sec-edit {
		display: grid;
		gap: 0.4rem;
	}
	.sec-head {
		display: flex;
		align-items: center;
		gap: 0.25rem;
	}
	.sec-label-input {
		flex: 1;
		max-width: 320px;
		font-weight: 650;
	}
	.lyrics-text {
		min-height: 7.5rem;
		field-sizing: content;
		font-size: 1rem;
		line-height: 1.55;
	}
	.chord-line {
		margin: 0 0 0.2em;
		color: var(--accent-strong);
		font-weight: 700;
		word-spacing: 0.1em;
	}
	.kind,
	.use-progression {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.35rem;
	}
	.kind .chip {
		padding: 0.2rem 0.65rem;
		font-size: 0.82rem;
	}
	.chords-text {
		min-height: 3.2rem;
		field-sizing: content;
		color: var(--accent-strong);
		font-weight: 650;
		font-size: 1rem;
		line-height: 1.55;
	}
	.use-progression .chords {
		color: var(--accent-strong);
	}
	.add-sections {
		display: grid;
		gap: 0.45rem;
		padding-top: 0.25rem;
	}
	.add-label {
		font-size: 0.78rem;
		font-weight: 650;
		color: var(--text-faint);
		text-transform: uppercase;
		letter-spacing: 0.05em;
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
		flex-shrink: 0;
		padding: 0.32rem 0.75rem;
		border-radius: 999px;
		border: 1px solid var(--border);
		background: transparent;
		color: var(--text-muted);
		font-size: 0.88rem;
		font-weight: 600;
		white-space: nowrap;
		cursor: pointer;
	}
	.chip:hover {
		color: var(--text);
	}
	.chip.dashed {
		border-style: dashed;
		padding-left: 0.55rem;
	}
	.chip[aria-pressed='true'] {
		background: var(--accent-soft);
		border-color: rgb(240 166 70 / 0.55);
		color: var(--accent-strong);
	}
	.chip.chord {
		min-width: 2.6rem;
		justify-content: center;
		color: var(--text);
		font-weight: 700;
	}
	.chip.prog .chords {
		color: var(--accent-strong);
	}

	/* --- chord palette, pinned to the bottom --- */
	.palette {
		position: fixed;
		left: 0;
		right: 0;
		bottom: 0;
		z-index: 15;
		display: grid;
		gap: 0.5rem;
		max-height: 45vh;
		overflow-y: auto;
		padding: 0.7rem max(var(--gutter), calc((100vw - 900px) / 2 + var(--gutter))) calc(0.8rem + env(safe-area-inset-bottom));
		background: rgb(24 27 33 / 0.97);
		backdrop-filter: blur(10px);
		border-top: 1px solid var(--border);
		box-shadow: 0 -12px 30px rgb(0 0 0 / 0.35);
	}
	.palette-top {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
	}
	.brush-hint {
		margin: 0;
		font-size: 0.92rem;
		transition: color 0.2s;
	}
	.brush-hint b {
		color: var(--accent-strong);
		font-size: 1.05em;
	}
	.brush-hint.nudge {
		color: var(--accent-strong);
	}
	.brush-hint .plus {
		color: var(--accent-strong);
	}
	.hint code {
		font-size: 0.95em;
		color: var(--text-muted);
	}
	.palette-row {
		display: flex;
		align-items: center;
		gap: 0.35rem;
		overflow-x: auto;
		scrollbar-width: none;
		padding-bottom: 0.1rem;
	}
	.row-label {
		flex-shrink: 0;
		min-width: 5.5rem;
		color: var(--text-faint);
		font-size: 0.75rem;
		font-weight: 650;
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}
	.palette-bottom {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}
	.custom {
		display: flex;
		flex: 1;
		gap: 0.35rem;
		max-width: 320px;
	}
	.custom .field {
		min-height: 34px;
		padding: 0.3rem 0.6rem;
	}
	@media (max-width: 560px) {
		.row-label {
			display: none;
		}
	}

	.empty {
		display: grid;
		justify-items: center;
		gap: 0.55rem;
		padding: 3rem 1rem;
		text-align: center;
		border: 1px dashed var(--border);
		border-radius: var(--radius);
	}
	.empty h2 {
		font-size: 1.1rem;
	}
	.empty p {
		margin: 0 0 0.5rem;
		max-width: 38ch;
	}
	.empty-icon {
		display: grid;
		place-items: center;
		width: 52px;
		height: 52px;
		border-radius: 15px;
		background: var(--accent-soft);
		color: var(--accent);
	}

	/* --- two columns (screens wide enough for them; print has its own rule) --- */
	.sheet.two-columns .sheet-section {
		break-inside: avoid;
	}
	@media (min-width: 640px) {
		.sheet.two-columns {
			columns: 2;
			column-gap: 2.5rem;
			column-rule: 1px solid var(--border);
		}
	}
	.columns-note {
		display: none;
		margin: 0 0 0.8rem;
		color: var(--text-faint);
		font-size: 0.85rem;
	}
	@media (max-width: 639px) {
		.columns-note {
			display: block;
		}
	}

	@media print {
		.page,
		.page.scroll-mode {
			max-width: none;
			padding: 0;
		}
		.back,
		.toolbar,
		.sheet-tools,
		.save-state,
		.banner,
		.screen-title,
		.scroll-bar,
		.columns-note,
		.error {
			display: none;
		}
		.print-title {
			display: block;
			font-size: 20pt;
		}
		.facts {
			color: #333;
		}
		.head {
			margin-bottom: 14pt;
		}
		.sheet {
			font-size: 11.5pt;
		}
		.chord,
		.chord-line {
			color: #000;
		}
		.sec-label {
			color: #555;
		}
		.sheet-section {
			break-inside: avoid;
		}
		/* Two columns carry over to paper, with a smaller size so lines wrap less. */
		.sheet.two-columns {
			columns: 2;
			column-gap: 0.45in;
			column-rule: 1px solid #bbb;
			font-size: 10.5pt;
		}
	}
</style>
