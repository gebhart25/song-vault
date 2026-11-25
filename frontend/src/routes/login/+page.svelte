<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { ApiError, api } from '#lib/api.ts';
	import { applyStatus, auth, isPin, safeNext } from '#lib/auth.svelte.ts';
	import AuthCard from '#lib/components/AuthCard.svelte';
	import PinInput from '#lib/components/PinInput.svelte';
	import type { AuthStatus } from '#lib/types.ts';

	// Installs from before the PIN still sign in with their password once.
	const legacy = $derived(!auth.pinSet);

	let pin = $state('');
	let error = $state('');
	let busy = $state(false);

	const ready = $derived(legacy ? pin.length > 0 : isPin(pin));

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		if (!ready) return;
		busy = true;
		error = '';
		try {
			applyStatus(await api.post<AuthStatus>('/auth/login', { pin }));
			await goto(safeNext(page.url.searchParams.get('next')), { replaceState: true });
		} catch (e) {
			error = e instanceof ApiError ? e.message : 'Sign-in failed.';
			pin = '';
		} finally {
			busy = false;
		}
	}
</script>

<AuthCard
	title="Sign in"
	subtitle={legacy ? 'Enter your Song Vault password. You’ll choose a PIN next.' : 'Enter your Song Vault PIN.'}
>
	<form onsubmit={submit}>
		{#key legacy}
			<PinInput id="pin" label={legacy ? 'Password' : 'PIN'} bind:value={pin} digitsOnly={!legacy} autofocus />
		{/key}
		{#if error}<p class="error" role="alert">{error}</p>{/if}
		<button class="btn btn-primary" type="submit" disabled={busy || !ready}>
			{busy ? 'Signing in…' : 'Sign in'}
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
