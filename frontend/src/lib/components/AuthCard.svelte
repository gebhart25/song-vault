<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from './Icon.svelte';

	let { title, subtitle, children }: { title: string; subtitle?: string; children: Snippet } = $props();
</script>

<div class="wrap">
	<div class="card">
		<div class="logo"><Icon name="music" size={26} /></div>
		<h1>{title}</h1>
		{#if subtitle}<p class="muted">{subtitle}</p>{/if}
		{@render children()}
	</div>
</div>

<style>
	.wrap {
		min-height: 100dvh;
		display: grid;
		place-items: center;
		padding: 1.5rem;
		background: radial-gradient(60rem 30rem at 50% -10%, rgb(240 166 70 / 0.1), transparent 70%);
	}
	.card {
		width: min(100%, 380px);
		padding: 2rem 1.75rem;
		border-radius: 18px;
		background: var(--surface);
		border: 1px solid var(--border);
		box-shadow: 0 20px 60px rgb(0 0 0 / 0.35);
	}
	.logo {
		display: grid;
		place-items: center;
		width: 48px;
		height: 48px;
		margin-bottom: 1.25rem;
		border-radius: 13px;
		background: var(--accent);
		color: var(--accent-ink);
	}
	h1 {
		font-size: 1.4rem;
	}
	p {
		margin: 0.4rem 0 1.4rem;
		font-size: 0.95rem;
	}
</style>
