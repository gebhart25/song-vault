// A song's parts (spec 3.3): numbered repeats like Verse 1 / Verse 2.

/** "Verse 2" -> ["Verse", 2]; "Pre-Chorus" -> ["Pre-Chorus", null] (split as the server does) */
export function splitNumber(name: string): [string, number | null] {
	const m = name.match(/^(.*\S)\s+(\d{1,3})$/);
	return m ? [m[1], Number(m[2])] : [name, null];
}

/** The name "Add another" gives the next row of a part: Verse (or Verse 1) -> "Verse 2". */
export function nextPartName(names: string[], name: string): string {
	const base = splitNumber(name)[0];
	const numbers = names
		.filter((n) => splitNumber(n)[0].toLowerCase() === base.toLowerCase())
		.map((n) => splitNumber(n)[1] ?? 1);
	return `${base} ${Math.max(1, ...numbers) + 1}`;
}
