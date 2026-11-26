/** `fetch` to our own server that gives up after `ms`. Away from home the app's address
 * can point at something else on the network the phone is on, so a request may hang
 * instead of failing (spec 5.11). */
export async function fetchWithTimeout(path: string, init: RequestInit = {}, ms = 5000): Promise<Response> {
	const controller = new AbortController();
	const timer = setTimeout(() => controller.abort(), ms);
	try {
		return await fetch(path, { credentials: 'same-origin', ...init, signal: controller.signal });
	} finally {
		clearTimeout(timer);
	}
}
