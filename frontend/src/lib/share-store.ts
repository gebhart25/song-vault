/**
 * Files shared to Song Vault from the Android share sheet (spec 2.1).
 * The service worker receives the share and keeps the files here (IndexedDB,
 * stored on disk) until the Share page has uploaded them.
 */

export interface SharedBatch {
	id: string;
	created: number;
	files: File[];
	title: string;
	text: string;
}

const DB_NAME = 'songvault-share';
const STORE = 'shares';

function open(): Promise<IDBDatabase> {
	return new Promise((resolve, reject) => {
		const req = indexedDB.open(DB_NAME, 1);
		req.onupgradeneeded = () => req.result.createObjectStore(STORE, { keyPath: 'id' });
		req.onsuccess = () => resolve(req.result);
		req.onerror = () => reject(req.error);
	});
}

async function run<T>(mode: IDBTransactionMode, action: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
	const db = await open();
	try {
		return await new Promise<T>((resolve, reject) => {
			const tx = db.transaction(STORE, mode);
			const req = action(tx.objectStore(STORE));
			tx.oncomplete = () => resolve(req.result);
			tx.onerror = () => reject(tx.error);
			tx.onabort = () => reject(tx.error);
		});
	} finally {
		db.close();
	}
}

export function saveShare(batch: SharedBatch): Promise<IDBValidKey> {
	return run('readwrite', (s) => s.put(batch));
}

export function getShare(id: string): Promise<SharedBatch | undefined> {
	return run('readonly', (s) => s.get(id) as IDBRequest<SharedBatch | undefined>);
}

export async function listShares(): Promise<SharedBatch[]> {
	const all = await run('readonly', (s) => s.getAll() as IDBRequest<SharedBatch[]>);
	return all.sort((a, b) => b.created - a.created);
}

export function deleteShare(id: string): Promise<undefined> {
	return run('readwrite', (s) => s.delete(id));
}
