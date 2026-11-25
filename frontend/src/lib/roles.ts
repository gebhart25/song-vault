// Roles a take can have (spec 5.1). Each part has one Current take per role, so a
// solo's rhythm and lead takes can both be Current.

export const DEFAULT_ROLES = ['Rhythm', 'Lead', 'Acoustic', 'Bass', 'Vocals', 'Harmony', 'Keys', 'Drums'];

/** The usual roles, then any others already used, without repeats (ignoring case). */
export function roleOptions(used: (string | null | undefined)[]): string[] {
	const seen = new Set<string>();
	const out: string[] = [];
	for (const role of [...DEFAULT_ROLES, ...used]) {
		const key = role?.trim().toLowerCase();
		if (!role || !key || seen.has(key)) continue;
		seen.add(key);
		out.push(role.trim());
	}
	return out;
}

export const sameRole = (a: string | null | undefined, b: string | null | undefined) =>
	(a ?? '').trim().toLowerCase() === (b ?? '').trim().toLowerCase();

