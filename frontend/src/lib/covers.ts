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
	{ id: 'aurora', name: 'Aurora' },
	{ id: 'campfire', name: 'Campfire' },
	{ id: 'city-lights', name: 'City lights' },
	{ id: 'rain', name: 'Rain' },
	{ id: 'desert', name: 'Desert' },
	{ id: 'microphone', name: 'Microphone' },
	{ id: 'moonlight', name: 'Moonlight' },
	{ id: 'headphones', name: 'Headphones' },
	{ id: 'lighthouse', name: 'Lighthouse' }
];

export const stockUrl = (id: string) => `/covers/${id}.svg`;

/** Solid colours for a song's picture, chosen to sit well on the dark Projects page; any
 * other colour can be picked too. */
export const COVER_COLORS: { color: string; name: string }[] = [
	{ color: '#e05d44', name: 'Vermilion' },
	{ color: '#f0a646', name: 'Amber' },
	{ color: '#e8c547', name: 'Mustard' },
	{ color: '#7cb26b', name: 'Sage' },
	{ color: '#2d6a4f', name: 'Pine' },
	{ color: '#2f9e8f', name: 'Teal' },
	{ color: '#0e7490', name: 'Lagoon' },
	{ color: '#3a7bd5', name: 'Blue' },
	{ color: '#5b4fc4', name: 'Indigo' },
	{ color: '#9a5cc6', name: 'Violet' },
	{ color: '#d0558f', name: 'Rose' },
	{ color: '#7f1d1d', name: 'Wine' },
	{ color: '#8c5a3c', name: 'Walnut' },
	{ color: '#4a5568', name: 'Slate' },
	{ color: '#1f2937', name: 'Charcoal' },
	{ color: '#f2ede4', name: 'Cream' }
];

