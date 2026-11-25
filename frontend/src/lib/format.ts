/** "2026-11-23T14:15:00Z" -> "11.23.26" in local time (spec 4.3). */
export function shortDate(iso: string | null | undefined): string {
	if (!iso) return '';
	const d = new Date(iso);
	if (Number.isNaN(d.getTime())) return '';
	const pad = (n: number) => String(n).padStart(2, '0');
	return `${pad(d.getMonth() + 1)}.${pad(d.getDate())}.${pad(d.getFullYear() % 100)}`;
}

/** 83.4 -> "1:23", 3725 -> "1:02:05". */
export function duration(seconds: number | null | undefined): string {
	if (seconds == null || !Number.isFinite(seconds)) return '';
	const total = Math.round(seconds);
	const h = Math.floor(total / 3600);
	const m = Math.floor((total % 3600) / 60);
	const s = total % 60;
	const ss = String(s).padStart(2, '0');
	return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`;
}

/** 1536 -> "1.5 KB", 294483395 -> "281 MB" */
export function fileSize(bytes: number): string {
	const units = ['bytes', 'KB', 'MB', 'GB', 'TB'];
	let n = bytes;
	let i = 0;
	while (n >= 1024 && i < units.length - 1) {
		n /= 1024;
		i++;
	}
	return i === 0 ? `${n} bytes` : `${n < 10 ? n.toFixed(1) : Math.round(n)} ${units[i]}`;
}

/** "Nov 23, 2026, 4:25 PM" in local time. */
export function dateTime(iso: string | null | undefined): string {
	if (!iso) return '';
	const d = new Date(iso);
	return Number.isNaN(d.getTime())
		? ''
		: d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

/** ISO -> value for <input type="datetime-local"> in local time. */
export function toLocalInput(iso: string | null | undefined): string {
	if (!iso) return '';
	const d = new Date(iso);
	if (Number.isNaN(d.getTime())) return '';
	const local = new Date(d.getTime() - d.getTimezoneOffset() * 60_000);
	return local.toISOString().slice(0, 16);
}

export function plural(n: number, one: string, many = `${one}s`): string {
	return `${n} ${n === 1 ? one : many}`;
}
