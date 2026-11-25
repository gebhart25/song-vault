// A menu on long-press (phones) or right-click (desktop), for links and other elements
// that already do something when tapped (spec 4.3 for tiles; the Projects page cards).

/** Where the menu was asked for. `sheet`: it came from a touch, so show a bottom sheet. */
export interface MenuAt {
	x: number;
	y: number;
	sheet: boolean;
}

const LONG_PRESS_MS = 520;

/** `use:contextMenu={(at) => ...}`. The tap that ends a long-press doesn't also follow the link. */
export function contextMenu(node: HTMLElement, open: (at: MenuAt) => void) {
	let handler = open;
	let timer: ReturnType<typeof setTimeout> | undefined;
	let pressAt: { x: number; y: number } | null = null;
	let openedAt = 0;
	let swallowClick = false;

	function fire(x: number, y: number, sheet: boolean) {
		cancel();
		if (Date.now() - openedAt < 800) return; // Chrome reports the same press twice
		openedAt = Date.now();
		swallowClick = sheet;
		handler({ x, y, sheet });
	}

	function cancel() {
		clearTimeout(timer);
		pressAt = null;
	}

	function oncontextmenu(e: MouseEvent) {
		e.preventDefault();
		const touch = (e as PointerEvent).pointerType === 'touch' || matchMedia('(pointer: coarse)').matches;
		if (e.clientX === 0 && e.clientY === 0) {
			// From the keyboard (menu key, Shift+F10): open it at the element.
			const r = node.getBoundingClientRect();
			fire(r.left + Math.min(r.width / 2, 120), r.top + Math.min(r.height / 2, 80), touch);
		} else fire(e.clientX, e.clientY, touch);
	}

	// Chrome on Android fires contextmenu on a long-press; the timer covers browsers that don't.
	function onpointerdown(e: PointerEvent) {
		swallowClick = false;
		if (e.pointerType !== 'touch') return;
		const at = { x: e.clientX, y: e.clientY };
		pressAt = at;
		clearTimeout(timer);
		timer = setTimeout(() => pressAt === at && fire(at.x, at.y, true), LONG_PRESS_MS);
	}

	function onpointermove(e: PointerEvent) {
		if (pressAt && Math.hypot(e.clientX - pressAt.x, e.clientY - pressAt.y) > 10) cancel();
	}

	function onclick(e: MouseEvent) {
		if (!swallowClick) return;
		swallowClick = false;
		e.preventDefault();
		e.stopPropagation();
	}

	node.addEventListener('contextmenu', oncontextmenu);
	node.addEventListener('pointerdown', onpointerdown);
	node.addEventListener('pointermove', onpointermove);
	node.addEventListener('pointerup', cancel);
	node.addEventListener('pointercancel', cancel);
	node.addEventListener('pointerleave', cancel);
	node.addEventListener('click', onclick, true);
	return {
		update(next: (at: MenuAt) => void) {
			handler = next;
		},
		destroy() {
			cancel();
			node.removeEventListener('contextmenu', oncontextmenu);
			node.removeEventListener('pointerdown', onpointerdown);
			node.removeEventListener('pointermove', onpointermove);
			node.removeEventListener('pointerup', cancel);
			node.removeEventListener('pointercancel', cancel);
			node.removeEventListener('pointerleave', cancel);
			node.removeEventListener('click', onclick, true);
		}
	};
}
