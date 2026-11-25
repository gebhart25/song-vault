<script lang="ts">
	import { goto } from '$app/navigation';
	import { ApiError, api } from '#lib/api.ts';
	import { applyStatus, isPin } from '#lib/auth.svelte.ts';
	import AuthCard from '#lib/components/AuthCard.svelte';
	import PinInput from '#lib/components/PinInput.svelte';
	import type { AuthStatus } from '#lib/types.ts';

	let pin = $state('');
	let confirm = $state('');
	let error = $state('');
	let busy = $state(false);

	const problem = $derived(
		confirm.length >= pin.length && confirm.length > 0 && confirm !== pin ? "The PINs don't match." : ''
	);
	const ready = $derived(isPin(pin) && confirm === pin);

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		if (!ready) return;
		busy = true;
		error = '';
		try {
			applyStatus(await api.post<AuthStatus>('/auth/setup', { pin }));
			await goto('/', { replaceState: true });
		} catch (e) {
			error = e instanceof ApiError ? e.message : 'Setup failed.';
		} finally {
			busy = false;
		}
	}
</script>

<AuthCard title="Welcome to Song Vault" subtitle="Choose a 4 to 6 digit PIN. Anyone on your network will need it to open the app.">
	<form onsubmit={submit}>
		<PinInput id="pin" label="PIN" autocomplete="new-password" bind:value={pin} autofocus />
		<PinInput id="confirm" label="Enter it again" autocomplete="new-password" bind:value={confirm} />
		{#if problem || error}<p class="error" role="alert">{error || problem}</p>{/if}
		<button class="btn btn-primary" type="submit" disabled={busy || !ready}>
			{busy ? 'Saving…' : 'Set PIN'}
		</button>
	</form>
</AuthCard>

<style>
	form {
		display: grid;
		gap: 0.6rem;
	}
	.btn {
		min-height: 44px;
		margin-top: 0.35rem;
	}
</style>
