/**
 * Song Vault service worker.
 *
 * 1. Share target (spec 2.1): Android posts shared videos to /share-target.
 *    The files are kept in IndexedDB and the app opens /share?id=… to upload them.
 *    Shared text with no files opens the Notebook with a new note filled in (spec 5.11).
 * 2. App shell cache (spec 2.1): built assets are cached so the app opens fast, and
 *    without the server (the Notebook works offline, spec 5.11).
 *    API calls always go to the network.
 */
import { version } from '$app/env';
import { assets, immutable } from '$app/manifest';
import { self } from '$app/service-worker';
import { saveShare } from '#lib/share-store.ts';

const CACHE = `songvault-${version}`;
const SHELL_URL = '/';

const toPath = (p: string) => '/' + p.replace(/^\/+/, '');
const PRECACHE = [...immutable.map((f) => toPath(f.path)), ...assets.map((f) => toPath(f.path))];

self.addEventListener('install', (event: ExtendableEvent) => {
	event.waitUntil(
		(async () => {
			const cache = await caches.open(CACHE);
			await cache.addAll([...PRECACHE, SHELL_URL]);
			await self.skipWaiting();
		})()
	);
});

self.addEventListener('activate', (event: ExtendableEvent) => {
	event.waitUntil(
		(async () => {
			for (const key of await caches.keys()) {
				if (key.startsWith('songvault-') && key !== CACHE) await caches.delete(key);
			}
			await self.clients.claim();
		})()
	);
});

async function receiveShare(request: Request): Promise<Response> {
	const form = await request.formData();
	const files = form.getAll('media').filter((f): f is File => f instanceof File && f.size > 0);
	const id = crypto.randomUUID();
	const title = String(form.get('title') ?? '').trim();
	const text = String(form.get('text') ?? '').trim();
	const link = String(form.get('url') ?? '').trim();
	const words = link && !text.includes(link) ? [text, link].filter(Boolean).join('\n') : text;
	let target = '/share?empty=1';
	if (files.length) {
		await saveShare({ id, created: Date.now(), files, title, text: words });
		target = `/share?id=${id}`;
	} else if (title || words) {
		// Just text: a new note in the Notebook, saved when the user taps Save.
		await saveShare({ id, created: Date.now(), files: [], title, text: words });
		target = `/notebook?share=${id}`;
	}
	return Response.redirect(new URL(target, self.location.origin).href, 303);
}

/** Network first for pages, but not for long: away from home the server's address can
 * hang instead of failing, and the app shell is enough to open the Notebook. */
const PAGE_TIMEOUT_MS = 4000;

async function cacheFirst(request: Request): Promise<Response> {
	const cached = await caches.match(request);
	return cached ?? fetch(request);
}

async function networkFirstShell(request: Request): Promise<Response> {
	const shell = async () => (await caches.match(SHELL_URL)) ?? Response.error();
	let timer: ReturnType<typeof setTimeout> | undefined;
	const timedOut = new Promise<Response>((resolve) => {
		timer = setTimeout(() => resolve(shell()), PAGE_TIMEOUT_MS);
	});
	try {
		return await Promise.race([fetch(request), timedOut]);
	} catch {
		// Offline: the single-page app shell still opens (the Notebook works; other pages
		// say the server can't be reached).
		return await shell();
	} finally {
		clearTimeout(timer);
	}
}

self.addEventListener('fetch', (event: FetchEvent) => {
	const { request } = event;
	const url = new URL(request.url);
	if (url.origin !== self.location.origin) return;

	if (request.method === 'POST' && url.pathname === '/share-target') {
		event.respondWith(receiveShare(request));
		return;
	}
	if (request.method !== 'GET' || url.pathname.startsWith('/api/')) return;

	if (PRECACHE.includes(url.pathname)) {
		event.respondWith(cacheFirst(request));
	} else if (request.mode === 'navigate') {
		event.respondWith(networkFirstShell(request));
	}
});
