import { goto } from '$app/navigation';
import { auth } from '#lib/auth.svelte.ts';

export class ApiError extends Error {
	constructor(
		public status: number,
		message: string
	) {
		super(message);
	}
}

function errorMessage(status: number, data: unknown, fallback: string): string {
	if (data && typeof data === 'object' && 'detail' in data) {
		const detail = (data as { detail: unknown }).detail;
		if (typeof detail === 'string') return detail;
		// FastAPI validation errors: [{ msg: "..." }, ...]
		if (Array.isArray(detail) && detail[0]?.msg) return String(detail[0].msg).replace(/^Value error, /, '');
	}
	return status >= 500 ? 'Something went wrong on the server.' : fallback;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
	// A Blob (an image, say) is sent as it is; anything else as JSON.
	const raw = body instanceof Blob;
	let res: Response;
	try {
		res = await fetch(`/api${path}`, {
			method,
			credentials: 'same-origin',
			headers:
				body === undefined ? undefined : { 'Content-Type': raw ? body.type || 'application/octet-stream' : 'application/json' },
			body: body === undefined ? undefined : raw ? body : JSON.stringify(body)
		});
	} catch {
		throw new ApiError(0, "Can't reach the server.");
	}

	if (res.status === 401 && !path.startsWith('/auth/')) {
		auth.authenticated = false;
		await goto('/login', { replaceState: true });
	}
	if (!res.ok) {
		const data = await res.json().catch(() => null);
		throw new ApiError(res.status, errorMessage(res.status, data, res.statusText || 'Request failed'));
	}
	if (res.status === 204) return undefined as T;
	return (await res.json()) as T;
}

export const api = {
	get: <T>(path: string) => request<T>('GET', path),
	post: <T>(path: string, body?: unknown) => request<T>('POST', path, body ?? {}),
	patch: <T>(path: string, body: unknown) => request<T>('PATCH', path, body),
	put: <T>(path: string, body: unknown) => request<T>('PUT', path, body),
	del: (path: string) => request<void>('DELETE', path)
};
