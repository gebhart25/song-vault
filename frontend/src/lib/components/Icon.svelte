<script lang="ts" module>
	// Simple 24x24 stroke icons.
	const PATHS = {
		plus: 'M12 5v14M5 12h14',
		check: 'M5 12.5l4.5 4.5L19 7.5',
		x: 'M6 6l12 12M18 6L6 18',
		'chevron-down': 'M6 9l6 6 6-6',
		'chevron-right': 'M9 6l6 6-6 6',
		'chevron-left': 'M15 6l-6 6 6 6',
		'arrow-up': 'M12 19V5M6 11l6-6 6 6',
		'arrow-down': 'M12 5v14M6 13l6 6 6-6',
		'arrow-left': 'M19 12H5M11 6l-6 6 6 6',
		search: 'M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM20 20l-4-4',
		more: 'M5 12h.01M12 12h.01M19 12h.01',
		trash: 'M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3',
		archive: 'M4 5h16v4H4zM5 9v10h14V9M10 13h4',
		edit: 'M4 20h4L19 9l-4-4L4 16v4zM13.5 6.5l4 4',
		logout: 'M15 4h4v16h-4M10 8l-4 4 4 4M6 12h11',
		music: 'M9 18V6l11-2v12M9 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0zM20 16a3 3 0 1 1-6 0 3 3 0 0 1 6 0z',
		video: 'M3 7h12v10H3zM15 10l6-3v10l-6-3',
		mic: 'M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3zM5 11a7 7 0 0 0 14 0M12 18v3',
		rows: 'M4 6h16M4 12h16M4 18h16',
		upload: 'M12 16V4M7 9l5-5 5 5M4 20h16',
		lock: 'M6 11h12v9H6zM8.5 11V8a3.5 3.5 0 0 1 7 0v3',
		user: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4.5 21a7.5 7.5 0 0 1 15 0',
		inbox: 'M4 13l2.5-7h11L20 13v6H4zM4 13h4.5l1 2h5l1-2H20',
		undo: 'M9 14L4 9l5-5M4 9h10.5a5.5 5.5 0 0 1 0 11H11',
		eraser: 'M8 20h12M5.5 15.5l9-9a2 2 0 0 1 2.8 0l1.2 1.2a2 2 0 0 1 0 2.8L12 17H7z',
		lyrics: 'M7 3h7l5 5v13H7zM14 3v5h5M10 12.5h6M10 16.5h4',
		send: 'M5 12h13M13 6l6 6-6 6',
		star: 'M12 3.8l2.5 5.2 5.7.8-4.1 4 1 5.6-5.1-2.7-5.1 2.7 1-5.6-4.1-4 5.7-.8z',
		'skip-back': 'M18 6l-8 6 8 6zM6 6v12',
		timer: 'M12 21a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM12 9v4l2.5 2.5M9.5 2.5h5',
		'skip-forward': 'M6 6l8 6-8 6zM18 6v12',
		repeat: 'M17 3l3 3-3 3M4 11V9a3 3 0 0 1 3-3h13M7 21l-3-3 3-3M20 13v2a3 3 0 0 1-3 3H4',
		filter: 'M4 5h16l-6 7.5V19l-4 2v-8.5z',
		printer: 'M7 9V3h10v6M7 17H4v-7h16v7h-3M7 14h10v7H7z',
		play: 'M7 4.5v15l12-7.5z',
		pause: 'M7 5h3.5v14H7zM13.5 5H17v14h-3.5z',
		select: 'M4 4h16v16H4zM8 12l3 3 5-6',
		folder: 'M3 6h6l2 2h10v11H3z',
		tag: 'M3 12V4h8l10 10-8 8zM7.5 8.5h.01',
		notes: 'M5 4h14v16H5zM9 9h6M9 13h6M9 17h3',
		minus: 'M5 12h14',
		columns: 'M4 4h7v16H4zM13 4h7v16h-7z',
		strum: 'M8 4v15M4.5 15.5 8 19l3.5-3.5M16 20V5M12.5 8.5 16 5l3.5 3.5',
		headphones: 'M4 15v-3a8 8 0 0 1 16 0v3M4 15h3.5v5H4zM16.5 15H20v5h-3.5z',
		record: 'M12 19a7 7 0 1 0 0-14 7 7 0 0 0 0 14z',
		download: 'M12 4v12M7 11l5 5 5-5M4 20h16',
		camera: 'M4 8h3l2-3h6l2 3h3v11H4zM12 16.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z',
		flip: 'M4 12a8 8 0 0 1 14-5.3M20 12a8 8 0 0 1-14 5.3M18 3v4h-4M6 21v-4h4',
		fullscreen: 'M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5',
		scissors: 'M6 9a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM8.1 7.9 20 18M8.1 16.1 20 6',
		image: 'M4 5h16v14H4zM4 16l4.5-4.5 3.5 3.5 2.5-2.5L20 18M15.5 9.5h.01'
	} as const;

	export type IconName = keyof typeof PATHS;
</script>

<script lang="ts">
	let { name, size = 20, stroke = 2 }: { name: IconName; size?: number; stroke?: number } = $props();
</script>

<svg
	width={size}
	height={size}
	viewBox="0 0 24 24"
	fill="none"
	stroke="currentColor"
	stroke-width={name === 'more' ? 3.5 : stroke}
	stroke-linecap="round"
	stroke-linejoin="round"
	aria-hidden="true"
>
	<path d={PATHS[name]} />
</svg>
