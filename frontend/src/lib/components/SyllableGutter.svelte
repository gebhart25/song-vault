<script lang="ts">
	import { syllableCounts } from '#lib/words.ts';

	// Syllable counts beside a textarea, one per line (spec 5.11, writing helpers). Put it
	// right after the textarea, in the same parent. It makes the textarea a little narrower
	// for a column of counts, and lays an invisible copy of the text over it so each count
	// sits level with the start of its line, however the lines wrap.
	let { textarea, text }: { textarea: HTMLTextAreaElement | undefined; text: string } = $props();

	const GUTTER = '2.4rem';
	const COPIED = [
		'font-family',
		'font-size',
		'font-weight',
		'font-style',
		'line-height',
		'letter-spacing',
		'word-spacing',
		'text-transform',
		'text-indent',
		'tab-size',
		'padding-top',
		'padding-right',
		'padding-bottom',
		'padding-left',
		'border-top-width',
		'border-right-width',
		'border-bottom-width',
		'border-left-width'
	];

	let box = $state('');
	let scroll = $state(0);
	let counts = $state<(number | null)[]>([]);
	const lines = $derived(text.split('\n'));

	function place() {
		const el = textarea;
		if (!el) return;
		const cs = getComputedStyle(el);
		const gap = parseFloat(cs.paddingRight) + parseFloat(cs.borderRightWidth);
		box =
			COPIED.map((p) => `${p}:${cs.getPropertyValue(p)}`).join(';') +
			`;left:${el.offsetLeft}px;top:${el.offsetTop}px;width:${el.offsetWidth}px;height:${el.offsetHeight}px` +
			`;--gap:${gap}px;--lh:${cs.lineHeight}`;
	}

	$effect(() => {
		const el = textarea;
		if (!el) return;
		const width = el.style.width;
		el.style.width = `calc(100% - ${GUTTER})`;
		const onScroll = () => (scroll = el.scrollTop);
		const observer = new ResizeObserver(place);
		observer.observe(el);
		if (el.parentElement) observer.observe(el.parentElement);
		el.addEventListener('scroll', onScroll);
		place();
		return () => {
			el.style.width = width;
			observer.disconnect();
			el.removeEventListener('scroll', onScroll);
		};
	});

	// The text moved things around (a line wrapped, a chip above went away): measure again.
	$effect(() => {
		void text;
		place();
	});

	$effect(() => {
		const these = lines;
		const timer = setTimeout(() => {
			counts = syllableCounts(these, (better) => {
				if (lines === these) counts = better;
			});
		}, 150);
		return () => clearTimeout(timer);
	});
</script>

<div class="ghost" style={box} aria-hidden="true">
	<div class="lines" style:translate="0 {-scroll}px">
		{#each lines as line, i (i)}
			<div class="line">
				<span class="text">{line || '​'}</span>{#if counts[i] != null}<span class="count">{counts[i]}</span>{/if}
			</div>
		{/each}
	</div>
</div>

<style>
	.ghost {
		position: absolute;
		z-index: 1;
		box-sizing: border-box;
		border-style: solid;
		border-color: transparent;
		white-space: pre-wrap;
		overflow-wrap: break-word;
		word-break: normal;
		pointer-events: none;
		/* Clip above and below (the textarea's scroll), not the counts out to the right */
		clip-path: inset(0 -4rem 0 0);
	}
	.text {
		color: transparent;
	}
	.line {
		position: relative;
	}
	.count {
		position: absolute;
		top: 0;
		left: calc(100% + var(--gap) + 0.4rem);
		width: 2rem;
		color: var(--text-faint);
		font-size: 0.78em;
		font-weight: 650;
		font-variant-numeric: tabular-nums;
		line-height: var(--lh);
		white-space: nowrap;
	}
</style>
