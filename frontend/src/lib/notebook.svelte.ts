/**
 * The Notebook on this device (spec 5.11).
 *
 * The whole notebook is kept in IndexedDB, so it reads, searches and takes new notes
 * with no connection to the server (the app is home-network only). Changes made here
 * wait on the device and are sent when the server can be reached; then the device
 * asks for what changed elsewhere since its last sync (by change number, see notes.py).
 *
 * Each note remembers the revision and text the server last had, and which fields were
 * changed here. A change is sent with that starting point. If the text changed elsewhere
 * in between, the server refuses it, and this device keeps its version as a copy next
 * to the other one, marked "Edited on two devices".
 */
import { fetchWithTimeout } from '#lib/net.ts';
import { newUid } from '#lib/notebook-text.ts';

export type NoteStatus = 'fresh' | 'used';
type Field = 'body' | 'starred' | 'status' | 'copy_of';

/** A note as the server sends it */
export interface ServerNote {
	uid: string;
	body: string;
	starred: boolean;
	status: NoteStatus;
	revision: number;
	copy_of: string | null;
	created_at: string;
	updated_at: string;
	deleted_at: string | null;
}

/** A note as this device keeps it */
export interface LocalNote {
	uid: string;
	body: string;
	starred: boolean;
	status: NoteStatus;
	/** A copy made because the note was edited on two devices: the other version's uid */
	copy_of: string | null;
	created_at: string;
	/** The server's revision and text this device last saw (0 = never sent) */
	revision: number;
	server_body: string;
	/** Fields changed here and not yet sent */
	pending: Field[];
	/** Moved to Trash here, not yet sent */
	trash: boolean;
	/** Up by one with every change made here, to spot edits made while a sync was out */
	version: number;
}

export type SyncState = 'idle' | 'offline' | 'signed-out' | 'error';

const SYNC_EVERY_MS = 30_000;
const SEND_AFTER_MS = 1500;
/** A note moved to Trash waits this long before it's sent, so Undo stays local. */
const TRASH_HOLD_MS = 7000;
const CHUNK = 200;

// --- IndexedDB ----------------------------------------------------------------------

const DB_NAME = 'songvault-notebook';
let dbPromise: Promise<IDBDatabase> | null = null;

function openDb(): Promise<IDBDatabase> {
	dbPromise ??= new Promise((resolve, reject) => {
		const req = indexedDB.open(DB_NAME, 1);
		req.onupgradeneeded = () => {
			req.result.createObjectStore('notes', { keyPath: 'uid' });
			req.result.createObjectStore('meta');
		};
		req.onsuccess = () => {
			const db = req.result;
			db.onversionchange = () => {
				db.close();
				dbPromise = null;
			};
			resolve(db);
		};
		req.onerror = () => {
			dbPromise = null;
			reject(req.error);
		};
	});
	return dbPromise;
}

function done(tx: IDBTransaction): Promise<void> {
	return new Promise((resolve, reject) => {
		tx.oncomplete = () => resolve();
		tx.onerror = () => reject(tx.error);
		tx.onabort = () => reject(tx.error);
	});
}

async function readAll(): Promise<{ notes: LocalNote[]; cursor: number | null }> {
	const db = await openDb();
	const tx = db.transaction(['notes', 'meta'], 'readonly');
	const notesReq = tx.objectStore('notes').getAll();
	const cursorReq = tx.objectStore('meta').get('cursor');
	await done(tx);
	return { notes: notesReq.result as LocalNote[], cursor: (cursorReq.result as number | undefined) ?? null };
}

// Writes are gathered and saved together, one transaction per moment.
const toPut = new Map<string, LocalNote>();
const toDelete = new Set<string>();
let cursorToSave: number | null = null;
let flushQueued = false;

function queueFlush() {
	if (flushQueued) return;
	flushQueued = true;
	queueMicrotask(async () => {
		flushQueued = false;
		const puts = [...toPut.values()];
		const deletes = [...toDelete];
		const cursor = cursorToSave;
		toPut.clear();
		toDelete.clear();
		cursorToSave = null;
		if (!puts.length && !deletes.length && cursor === null) return;
		try {
			const db = await openDb();
			const tx = db.transaction(['notes', 'meta'], 'readwrite');
			const notes = tx.objectStore('notes');
			for (const uid of deletes) notes.delete(uid);
			for (const note of puts) notes.put($state.snapshot(note));
			if (cursor !== null) tx.objectStore('meta').put(cursor, 'cursor');
			await done(tx);
		} catch (e) {
			console.error('Notebook: could not save on this device', e);
		}
	});
}

// --- state --------------------------------------------------------------------------

const byUid = new Map<string, LocalNote>();
let notes = $state.raw<LocalNote[]>([]);
let loaded = $state(false);
let syncState = $state<SyncState>('idle');
let publishQueued = false;

/** Hand the page a fresh list once the current changes are done. */
function publish() {
	if (publishQueued) return;
	publishQueued = true;
	queueMicrotask(() => {
		publishQueued = false;
		notes = [...byUid.values()];
	});
}

function keep(note: LocalNote) {
	byUid.set(note.uid, note);
	toDelete.delete(note.uid);
	toPut.set(note.uid, note);
	queueFlush();
	publish();
}

function drop(uid: string) {
	byUid.delete(uid);
	toPut.delete(uid);
	toDelete.add(uid);
	queueFlush();
	publish();
}

const hasChanges = (n: LocalNote) => n.revision === 0 || n.pending.length > 0 || n.trash;

function fromServer(s: ServerNote, version = 1): LocalNote {
	return {
		uid: s.uid,
		body: s.body,
		starred: s.starred,
		status: s.status,
		copy_of: s.copy_of,
		created_at: s.created_at,
		revision: s.revision,
		server_body: s.body,
		pending: [],
		trash: false,
		version
	};
}

// --- talking to the server ---------------------------------------------------------

class SyncError extends Error {
	constructor(public state: Exclude<SyncState, 'idle'>) {
		super(state);
	}
}

async function call<T>(method: string, path: string, body?: unknown, ms = 5000): Promise<T> {
	let res: Response;
	try {
		res = await fetchWithTimeout(
			`/api${path}`,
			{
				method,
				headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
				body: body === undefined ? undefined : JSON.stringify(body)
			},
			ms
		);
	} catch {
		throw new SyncError('offline');
	}
	if (res.status === 401) throw new SyncError('signed-out');
	// A proxy in front of a stopped server answers 502–504: as good as offline.
	if (res.status >= 502 && res.status <= 504) throw new SyncError('offline');
	if (!res.ok) throw new SyncError('error');
	return (await res.json()) as T;
}

interface ChangeResult {
	uid: string;
	note: ServerNote | null;
	conflict: boolean;
}

/** Notes being sent right now (a note never sent can't just be dropped while it's out). */
const inFlight = new Set<string>();
const trashedAt = new Map<string, number>();

function toChange(n: LocalNote) {
	const isNew = n.revision === 0;
	const has = (f: Field) => isNew || n.pending.includes(f);
	return {
		uid: n.uid,
		created_at: n.created_at,
		base_revision: n.revision,
		base_body: isNew ? null : n.server_body,
		body: has('body') ? n.body : null,
		starred: has('starred') ? n.starred : null,
		status: has('status') ? n.status : null,
		copy_of: isNew ? n.copy_of : n.pending.includes('copy_of') ? (n.copy_of ?? '') : null,
		trash: n.trash
	};
}

function applyResult(sentVersion: number, result: ChangeResult) {
	const local = byUid.get(result.uid);
	if (!local) return;
	const server = result.note;

	if (result.conflict && server) {
		// The text changed elsewhere too: keep this device's version as a copy beside it.
		if (!local.trash) {
			keep({ ...local, uid: newUid(), copy_of: server.uid, revision: 0, server_body: '', pending: [], trash: false, version: 1 });
			again = true; // send the copy straight away
		}
		if (server.deleted_at) drop(local.uid);
		else keep(fromServer(server, local.version + 1));
		return;
	}
	if (!server || server.deleted_at) {
		drop(local.uid);
		trashedAt.delete(local.uid);
		return;
	}
	if (local.version === sentVersion) keep(fromServer(server, local.version + 1));
	// Changed again while this was out: keep the newer changes, on top of what the server has
	// now, and send them next.
	else {
		keep({ ...local, revision: server.revision, server_body: server.body });
		again = true;
	}
}

async function push(): Promise<number | null> {
	const now = Date.now();
	let holdUntil: number | null = null;
	const waiting = [...byUid.values()].filter((n) => {
		if (!hasChanges(n)) return false;
		const held = n.trash ? (trashedAt.get(n.uid) ?? 0) + TRASH_HOLD_MS : 0;
		if (held > now) {
			holdUntil = Math.min(holdUntil ?? held, held);
			return false;
		}
		if (n.trash && n.revision === 0) {
			// Never sent, so there's nothing to tell the server.
			drop(n.uid);
			trashedAt.delete(n.uid);
			return false;
		}
		return true;
	});
	for (let i = 0; i < waiting.length; i += CHUNK) {
		const batch = waiting.slice(i, i + CHUNK).map((n) => ({ uid: n.uid, version: n.version, change: toChange(n) }));
		batch.forEach((b) => inFlight.add(b.uid));
		try {
			const { results } = await call<{ results: ChangeResult[] }>(
				'POST',
				'/notes/changes',
				{ changes: batch.map((b) => b.change) },
				20_000
			);
			results.forEach((r, j) => applyResult(batch[j].version, r));
		} finally {
			batch.forEach((b) => inFlight.delete(b.uid));
		}
	}
	return holdUntil;
}

let cursor: number | null = null;

async function pull() {
	const page = await call<{ notes: ServerNote[]; cursor: number; full: boolean }>(
		'GET',
		cursor === null ? '/notes' : `/notes?since=${cursor}`,
		undefined,
		15_000
	);
	if (page.full) {
		// The whole notebook: anything else this device has (and hasn't changed) is gone.
		const there = new Set(page.notes.map((n) => n.uid));
		for (const local of [...byUid.values()]) if (!there.has(local.uid) && !hasChanges(local)) drop(local.uid);
	}
	for (const s of page.notes) {
		const local = byUid.get(s.uid);
		if (local && hasChanges(local)) continue; // its own changes go first; the server sorts it out
		if (s.deleted_at) {
			if (local) drop(s.uid);
		} else keep(fromServer(s, (local?.version ?? 0) + 1));
	}
	cursor = page.cursor;
	cursorToSave = page.cursor;
	queueFlush();
}

let running: Promise<void> | null = null;
let again = false;
let sendTimer: ReturnType<typeof setTimeout> | undefined;

/** Send what's waiting and fetch what changed elsewhere. One at a time; a call during a
 * sync runs another once it's done. */
function sync(): Promise<void> {
	if (!loaded) return Promise.resolve();
	if (running) {
		again = true;
		return running;
	}
	running = (async () => {
		try {
			do {
				again = false;
				try {
					const holdUntil = await push();
					await pull();
					syncState = 'idle';
					if (holdUntil !== null) later(holdUntil - Date.now() + 50);
				} catch (e) {
					syncState = e instanceof SyncError ? e.state : 'error';
				}
			} while (again);
		} finally {
			running = null;
		}
	})();
	return running;
}

function later(ms = SEND_AFTER_MS) {
	clearTimeout(sendTimer);
	sendTimer = setTimeout(() => void sync(), Math.max(0, ms));
}

// --- starting and stopping ----------------------------------------------------------

let started = false;
let timer: ReturnType<typeof setInterval> | undefined;
const onOnline = () => void sync();
const onVisible = () => document.visibilityState === 'visible' && void sync();

async function start(): Promise<void> {
	if (started) return;
	started = true;
	try {
		const saved = await readAll();
		for (const n of saved.notes) byUid.set(n.uid, n);
		cursor = saved.cursor;
	} catch (e) {
		console.error('Notebook: this device can’t keep notes (IndexedDB unavailable)', e);
	}
	notes = [...byUid.values()];
	loaded = true;
	window.addEventListener('online', onOnline);
	document.addEventListener('visibilitychange', onVisible);
	timer = setInterval(() => document.visibilityState === 'visible' && void sync(), SYNC_EVERY_MS);
	void sync();
}

/** Forget the notebook on this device (signing out). */
async function clear(): Promise<void> {
	started = false;
	clearInterval(timer);
	clearTimeout(sendTimer);
	window.removeEventListener('online', onOnline);
	document.removeEventListener('visibilitychange', onVisible);
	await running?.catch(() => {});
	byUid.clear();
	toPut.clear();
	toDelete.clear();
	trashedAt.clear();
	cursor = null;
	notes = [];
	loaded = false;
	syncState = 'idle';
	const db = await dbPromise?.catch(() => null);
	db?.close();
	dbPromise = null;
	await new Promise<void>((resolve) => {
		const req = indexedDB.deleteDatabase(DB_NAME);
		req.onsuccess = req.onerror = req.onblocked = () => resolve();
	});
}

// --- changes made here ------------------------------------------------------------

function create(body: string, createdAt = new Date().toISOString()): string {
	const uid = newUid();
	keep({
		uid,
		body,
		starred: false,
		status: 'fresh',
		copy_of: null,
		created_at: createdAt,
		revision: 0,
		server_body: '',
		pending: [],
		trash: false,
		version: 1
	});
	later();
	return uid;
}

/** Several notes at once, in order: the first one on top (newest). */
function createMany(bodies: string[]): number {
	const now = Date.now();
	bodies.forEach((body, i) => create(body, new Date(now - i).toISOString()));
	return bodies.length;
}

function update(uid: string, fields: Partial<Pick<LocalNote, Field>>): void {
	const note = byUid.get(uid);
	if (!note) return;
	const next = { ...note, pending: [...note.pending] };
	let changed = false;
	for (const [key, value] of Object.entries(fields) as [Field, never][]) {
		if (next[key] === value) continue;
		(next as Record<Field, unknown>)[key] = value;
		if (!next.pending.includes(key)) next.pending.push(key);
		changed = true;
	}
	if (!changed) return;
	next.version += 1;
	keep(next);
	later();
}

/** Move a note to Trash. It's held here for a few seconds first, so Undo works even for a
 * note the server never saw (which is then just dropped). */
function trash(uid: string): void {
	const note = byUid.get(uid);
	if (!note) return;
	trashedAt.set(uid, Date.now());
	keep({ ...note, trash: true, version: note.version + 1 });
	later(TRASH_HOLD_MS + 50);
}

/** A note emptied before it was ever kept (the Jot box): gone, no Trash. */
function discard(uid: string): void {
	const note = byUid.get(uid);
	if (!note) return;
	if (note.revision === 0 && !inFlight.has(uid)) drop(uid);
	else trash(uid);
}

/** Undo moving a note to Trash. Once that has reached the server, it takes the server
 * to bring it back. False if that wasn't possible (offline). */
async function untrash(uid: string): Promise<boolean> {
	const note = byUid.get(uid);
	if (note?.trash) {
		trashedAt.delete(uid);
		keep({ ...note, trash: false, version: note.version + 1 });
		return true;
	}
	try {
		const restored = await call<ServerNote>('POST', `/notes/${encodeURIComponent(uid)}/restore`);
		const local = byUid.get(uid);
		if (!local || !hasChanges(local)) keep(fromServer(restored));
		return true;
	} catch {
		return false;
	}
}

export const notebook = {
	/** Every note on this device, Trash-bound ones included (filter out `trash`) */
	get notes() {
		return notes;
	},
	get loaded() {
		return loaded;
	},
	get state() {
		return syncState;
	},
	/** Notes with changes not yet on the server */
	get waiting() {
		return notes.filter(hasChanges).length;
	},
	start,
	clear,
	sync,
	create,
	createMany,
	update,
	trash,
	discard,
	untrash
};
