/**
 * Song Vault service worker.
 *
 * 1. Share target (spec 2.1): Android posts shared videos to /share-target.
 *    The files are kept in IndexedDB and the app opens /share?id=… to upload them.
 * 2. App shell cache (spec 2.1): built assets are cached so the app opens fast.
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
	if (files.length) {
		await saveShare({
			id,
			created: Date.now(),
			files,
			title: String(form.get('title') ?? ''),
			text: String(form.get('text') ?? '')
		});
	}
	const target = files.length ? `/share?id=${id}` : '/share?empty=1';
	return Response.redirect(new URL(target, self.location.origin).href, 303);
}

async function cacheFirst(request: Request): Promise<Response> {
	const cached = await caches.match(request);
	return cached ?? fetch(request);
}

async function networkFirstShell(request: Request): Promise<Response> {
	try {
		return await fetch(request);
	} catch {
		// Offline: the single-page app shell still opens; API calls will report the error.
		return (await caches.match(SHELL_URL)) ?? Response.error();
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
