import type { AuthStatus } from '#lib/types.ts';

export const auth = $state({
	checked: false,
	setupRequired: false,
	authenticated: false,
	pinSet: true
});

export function applyStatus(status: AuthStatus): void {
	auth.setupRequired = status.setup_required;
	auth.authenticated = status.authenticated;
	auth.pinSet = status.pin_set;
	auth.checked = true;
}

export async function refreshAuth(): Promise<void> {
	try {
		const res = await fetch('/api/auth/status', { credentials: 'same-origin' });
		applyStatus((await res.json()) as AuthStatus);
	} catch {
		// Server unreachable: show the sign-in screen, which reports the error on submit.
		applyStatus({ setup_required: false, authenticated: false, pin_set: true });
	}
}

export async function logout(): Promise<void> {
	await fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' }).catch(() => {});
	auth.authenticated = false;
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
