import { fetchWithTimeout } from '#lib/net.ts';
import type { AuthStatus } from '#lib/types.ts';

export const auth = $state({
	checked: false,
	setupRequired: false,
	authenticated: false,
	pinSet: true,
	/** The server can't be reached, but this device signed in before: the app opens
	 * anyway, and the Notebook works (spec 5.11). */
	offline: false
});

/** Set while this device is signed in, so it can open offline. */
const SIGNED_IN_KEY = 'songvault.signedIn';

export function applyStatus(status: AuthStatus): void {
	auth.setupRequired = status.setup_required;
	auth.authenticated = status.authenticated;
	auth.pinSet = status.pin_set;
	auth.offline = false;
	auth.checked = true;
	if (status.authenticated) localStorage.setItem(SIGNED_IN_KEY, '1');
	else localStorage.removeItem(SIGNED_IN_KEY);
}

export async function refreshAuth(): Promise<void> {
	try {
		const res = await fetchWithTimeout('/api/auth/status', {}, 4000);
		if (!res.ok) throw new Error(`status ${res.status}`); // e.g. a proxy in front of a stopped server
		applyStatus((await res.json()) as AuthStatus);
	} catch {
		if (localStorage.getItem(SIGNED_IN_KEY) === '1') {
			auth.setupRequired = false;
			auth.authenticated = true;
			auth.pinSet = true;
			auth.offline = true;
			auth.checked = true;
		} else if (!auth.checked) {
			// Never signed in here: show the sign-in screen, which reports the error on submit.
			applyStatus({ setup_required: false, authenticated: false, pin_set: true });
		}
	}
}

export async function logout(): Promise<void> {
	localStorage.removeItem(SIGNED_IN_KEY);
	await fetchWithTimeout('/api/auth/logout', { method: 'POST' }).catch(() => {});
	auth.authenticated = false;
	auth.offline = false;
}

/** Only same-site paths are allowed as a post-sign-in destination. */
export function safeNext(next: string | null): string {
	const ok = !!next && next.startsWith('/') && !next.startsWith('//') && !next.startsWith('/\\');
	return ok ? (next as string) : '/';
}

/** A PIN is 4 to 6 digits (spec 2.6). */
export const PIN_MIN = 4;
export const PIN_MAX = 6;
export const isPin = (value: string) => /^[0-9]{4,6}$/.test(value);
