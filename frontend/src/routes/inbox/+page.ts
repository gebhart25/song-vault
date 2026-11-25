import { redirect } from '@sveltejs/kit';

// The Inbox became Uncategorized (2026-11-23).
export function load() {
	redirect(308, '/uncategorized');
}
