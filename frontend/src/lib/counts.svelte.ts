import { api } from '#lib/api.ts';
import type { ClipsSummary } from '#lib/types.ts';

/** Clips waiting in Uncategorized (header badge and the Projects page card), clips still processing. */
export const counts = $state<ClipsSummary>({
	uncategorized: 0,
	uncategorized_cover_url: null,
	uncategorized_updated_at: null,
	processing: 0,
	trash: 0
});

export async function refreshCounts(): Promise<void> {
	try {
		Object.assign(counts, await api.get<ClipsSummary>('/clips/summary'));
	} catch {
		// keep the old numbers
	}
}
