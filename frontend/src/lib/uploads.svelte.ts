/**
 * Upload queue (spec 2.4, 3.2). Files upload one at a time over tus, so long
 * videos resume after dropped Wi-Fi. While a file uploads, a Web Worker hashes
 * it; the server then checks its own SHA-256 against ours before the clip counts
 * as "Stored on server".
 */
import * as tus from 'tus-js-client';
import { ApiError, api } from '#lib/api.ts';
import type { ClipDetail } from '#lib/types.ts';

export interface UploadTarget {
	projectId: number | null;
	labelIds: number[];
	/** Shown in the queue, e.g. "Uncategorized" or "Midnight Drive · Verse" */
	label: string;
}

export type UploadStatus = 'queued' | 'uploading' | 'verifying' | 'done' | 'duplicate' | 'error';

export interface UploadItem {
	key: number;
	name: string;
	size: number;
	target: UploadTarget;
	status: UploadStatus;
	sent: number;
	hashed: number;
	error: string;
	clipId: number | null;
}

const ACCEPT = 'video/*,audio/*,.mp4,.mov,.m4v,.webm,.mkv,.3gp,.m4a,.mp3,.wav,.aac,.ogg,.opus,.flac';
const CHUNK_SIZE = 32 * 1024 * 1024; // stays well under Cloudflare's 100 MB request cap

// Non-reactive per-item state (File and tus objects must not be proxied).
const files = new Map<number, File>();
const tusUploads = new Map<number, tus.Upload>();
const finishUrls = new Map<number, string>();
// Callbacks for a group of files added together (e.g. one share from the share sheet).
const batches: { keys: Set<number>; onDone: () => void }[] = [];

class UploadQueue {
	items = $state<UploadItem[]>([]);
	/** Bumped whenever a clip is stored; pages reload when it changes. */
	version = $state(0);
	expanded = $state(true);
	#nextKey = 1;
	#running = false;

	get active(): boolean {
		return this.items.some((i) => i.status === 'queued' || i.status === 'uploading' || i.status === 'verifying');
	}

	/** Queue files; `onDone` runs once every one of them is stored on the server. */
	add(list: FileList | File[], target: UploadTarget, onDone?: () => void): number[] {
		const keys: number[] = [];
		for (const file of Array.from(list)) {
			const key = this.#nextKey++;
			keys.push(key);
			files.set(key, file);
			this.items.push({
				key,
				name: file.name,
				size: file.size,
				target,
				status: 'queued',
				sent: 0,
				hashed: 0,
				error: '',
				clipId: null
			});
		}
		if (onDone && keys.length) batches.push({ keys: new Set(keys), onDone });
		this.expanded = true;
		void this.#pump();
		return keys;
	}

	#settleBatches(): void {
		for (let i = batches.length - 1; i >= 0; i--) {
			const done = [...batches[i].keys].every((k) => {
				const status = this.#item(k)?.status;
				return status === 'done' || status === 'duplicate';
			});
			if (done) {
				const [batch] = batches.splice(i, 1);
				batch.onDone();
			}
		}
	}

	/** Opens the system file picker (must be called from a click handler). */
	pick(target: UploadTarget): void {
		const input = document.createElement('input');
		input.type = 'file';
		input.multiple = true;
		input.accept = ACCEPT;
		input.addEventListener('change', () => {
			if (input.files?.length) this.add(input.files, target);
		});
		input.click();
	}

	retry(key: number): void {
		const item = this.#item(key);
		if (!item || item.status !== 'error') return;
		item.status = 'queued';
		item.error = '';
		void this.#pump();
	}

	async cancel(key: number): Promise<void> {
		const item = this.#item(key);
		if (!item) return;
		const upload = tusUploads.get(key);
		if (upload && item.status === 'uploading') await upload.abort(true).catch(() => {});
		this.#forget(key);
		for (const batch of batches) batch.keys.delete(key);
		this.items = this.items.filter((i) => i.key !== key);
		this.#settleBatches();
	}

	clearFinished(): void {
		for (const i of this.items) if (i.status === 'done' || i.status === 'duplicate') this.#forget(i.key);
		this.items = this.items.filter((i) => i.status !== 'done' && i.status !== 'duplicate');
	}

	#item(key: number): UploadItem | undefined {
		return this.items.find((i) => i.key === key);
	}

	#forget(key: number): void {
		files.delete(key);
		tusUploads.delete(key);
		finishUrls.delete(key);
	}

	async #pump(): Promise<void> {
		if (this.#running) return;
		this.#running = true;
		try {
			let next: UploadItem | undefined;
			while ((next = this.items.find((i) => i.status === 'queued'))) {
				await this.#run(next.key);
			}
		} finally {
			this.#running = false;
		}
	}

	async #run(key: number): Promise<void> {
		const item = this.#item(key);
		const file = files.get(key);
		if (!item || !file) return;
		try {
			let finishUrl = finishUrls.get(key);
			let digest: Promise<string>;
			if (!finishUrl) {
				item.status = 'uploading';
				digest = hashInWorker(file, (n) => (item.hashed = n));
				finishUrl = await this.#upload(key, item, file);
				finishUrls.set(key, finishUrl);
			} else {
				digest = hashInWorker(file, (n) => (item.hashed = n)); // retrying only the check
			}
			item.status = 'verifying';
			const clip = await api.post<ClipDetail>(finishUrl, { sha256: await digest });
			item.status = clip.duplicate ? 'duplicate' : 'done';
			item.clipId = clip.id;
			finishUrls.delete(key);
			this.version++;
			this.#settleBatches();
		} catch (error) {
			item.status = 'error';
			item.error = describe(error);
			if (error instanceof ApiError && error.status === 422) finishUrls.delete(key); // start over
		}
	}

	#upload(key: number, item: UploadItem, file: File): Promise<string> {
		const { target } = item;
		const metadata: Record<string, string> = {
			filename: file.name,
			filetype: file.type,
			last_modified: String(file.lastModified),
			timezone: Intl.DateTimeFormat().resolvedOptions().timeZone
		};
		if (target.projectId !== null) metadata.project_id = String(target.projectId);
		if (target.labelIds.length) metadata.label_ids = target.labelIds.join(',');

		return new Promise((resolve, reject) => {
			const upload = new tus.Upload(file, {
				endpoint: '/api/uploads',
				chunkSize: CHUNK_SIZE,
				retryDelays: [0, 1000, 3000, 5000, 10_000, 20_000, 30_000, 60_000],
				metadata,
				removeFingerprintOnSuccess: true,
				onProgress: (sent) => (item.sent = sent),
				onSuccess: () => {
					// The upload URL is absolute; the API helper wants it without the /api prefix.
					const path = new URL(upload.url ?? '', location.href).pathname.replace(/^\/api/, '');
					resolve(`${path}/finish`);
				},
				onError: reject
			});
			tusUploads.set(key, upload);
			upload
				.findPreviousUploads()
				.then((previous) => {
					if (previous.length) upload.resumeFromPreviousUpload(previous[0]);
					upload.start();
				})
				.catch(reject);
		});
	}
}

function hashInWorker(file: File, onProgress: (bytes: number) => void): Promise<string> {
	return new Promise((resolve, reject) => {
		const worker = new Worker(new URL('./hash.worker.ts', import.meta.url), { type: 'module' });
		worker.onmessage = (event: MessageEvent<{ progress?: number; digest?: string; error?: string }>) => {
			const { progress, digest, error } = event.data;
			if (progress !== undefined) onProgress(progress);
			if (digest) {
				worker.terminate();
				resolve(digest);
			}
			if (error) {
				worker.terminate();
				reject(new Error(error));
			}
		};
		worker.onerror = (event) => {
			worker.terminate();
			reject(new Error(event.message || 'Could not read the file'));
		};
		worker.postMessage({ file });
	});
}

function describe(error: unknown): string {
	if (error instanceof ApiError) return error.message;
	if (error instanceof tus.DetailedError) {
		const status = error.originalResponse?.getStatus();
		if (status === 401) return 'Signed out. Sign in and retry.';
		if (status === 415) return 'Not a video or audio file.';
		if (status === 413) return 'The file is too large.';
		const body = error.originalResponse?.getBody();
		try {
			const detail = JSON.parse(body ?? '').detail;
			if (typeof detail === 'string') return detail;
		} catch {
			// not JSON
		}
		return status ? `Upload failed (${status}).` : "Can't reach the server. Retry when you're back online.";
	}
	return error instanceof Error ? error.message : 'Upload failed.';
}

export const uploads = new UploadQueue();
