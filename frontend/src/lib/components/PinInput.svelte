<script lang="ts">
	import { PIN_MAX } from '#lib/auth.svelte.ts';

	// A masked 4–6 digit field that brings up the number pad on phones.
	let {
		value = $bindable(''),
		id,
		label,
		autocomplete = 'current-password',
		autofocus = false,
		digitsOnly = true
	}: {
		value?: string;
		id: string;
		label: string;
		autocomplete?: 'current-password' | 'new-password';
		autofocus?: boolean;
		/** False for the old password, which can contain letters. */
		digitsOnly?: boolean;
	} = $props();

	let input: HTMLInputElement | undefined = $state();

	$effect(() => {
		if (autofocus) input?.focus();
	});

	function oninput(event: Event & { currentTarget: HTMLInputElement }) {
		if (!digitsOnly) return;
		const digits = event.currentTarget.value.replace(/[^0-9]/g, '').slice(0, PIN_MAX);
		if (digits !== event.currentTarget.value) event.currentTarget.value = digits;
		value = digits;
	}
</script>

<label class="pin-label" for={id}>{label}</label>
{#if digitsOnly}
	<input
		bind:this={input}
		{id}
		class="field pin"
		type="password"
		inputmode="numeric"
		pattern="[0-9]*"
		maxlength={PIN_MAX}
		{autocomplete}
		placeholder="••••"
		{value}
		{oninput}
	/>
{:else}
	<input bind:this={input} {id} class="field" type="password" {autocomplete} bind:value />
{/if}

<style>
	.pin-label {
		font-size: 0.875rem;
		color: var(--text-muted);
	}
	.pin {
		font-size: 1.6rem;
		letter-spacing: 0.55em;
		text-align: center;
		padding-left: calc(0.75rem + 0.55em);
		min-height: 54px;
		font-variant-numeric: tabular-nums;
	}
	.pin::placeholder {
		letter-spacing: 0.55em;
	}
</style>
