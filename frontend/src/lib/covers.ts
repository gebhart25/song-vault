// Song pictures (spec 3.1). The stock ones ship with the app in static/covers/; the
// server only checks a name's shape, so this list is where they're named.

export const STOCK_COVERS: { id: string; name: string }[] = [
	{ id: 'sunset', name: 'Sunset' },
	{ id: 'night-drive', name: 'Night drive' },
	{ id: 'vinyl', name: 'Vinyl' },
	{ id: 'cassette', name: 'Cassette' },
	{ id: 'waveform', name: 'Waveform' },
	{ id: 'acoustic', name: 'Acoustic' },
	{ id: 'keys', name: 'Keys' },
	{ id: 'ocean', name: 'Ocean' },
	{ id: 'forest', name: 'Forest' },
	{ id: 'neon', name: 'Neon' },
	{ id: 'sheet-music', name: 'Sheet music' },
	{ id: 'aurora', name: 'Aurora' }
];

export const stockUrl = (id: string) => `/covers/${id}.svg`;

const MAX_SIDE = 1600;

/** An image file as a JPEG of at most 1600 px a side, turned the right way up (the
 * browser applies the photo's orientation), ready to upload. Throws if the browser
 * can't read the file. */
export async function shrinkImage(file: File): Promise<Blob> {
	const bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
	try {
		const scale = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height));
		const canvas = document.createElement('canvas');
		canvas.width = Math.max(1, Math.round(bitmap.width * scale));
		canvas.height = Math.max(1, Math.round(bitmap.height * scale));
		const g = canvas.getContext('2d');
		if (!g) throw new Error('No canvas');
		g.fillStyle = '#15181d'; // under any transparent parts
		g.fillRect(0, 0, canvas.width, canvas.height);
		g.imageSmoothingQuality = 'high';
		g.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
		return await new Promise<Blob>((resolve, reject) =>
			canvas.toBlob((b) => (b ? resolve(b) : reject(new Error('Could not encode'))), 'image/jpeg', 0.9)
		);
	} finally {
		bitmap.close();
	}
}
