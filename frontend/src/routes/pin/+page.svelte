<script lang="ts">
	import { goto } from '$app/navigation';
	import { ApiError, api } from '#lib/api.ts';
	import { applyStatus, auth, isPin } from '#lib/auth.svelte.ts';
	import AuthCard from '#lib/components/AuthCard.svelte';
	import PinInput from '#lib/components/PinInput.svelte';
	import type { AuthStatus } from '#lib/types.ts';

	// Choose a PIN (installs switching from the old password) or change it.
	const changing = auth.pinSet;

	let current = $state('');
	let pin = $state('');
	let confirm = $state('');
	let error = $state('');
	let busy = $state(false);
	let done = $state(false);

	const problem = $derived(
		confirm.length >= pin.length && confirm.length > 0 && confirm !== pin ? "The new PINs don't match." : ''
	);
	const ready = $derived((!changing || isPin(current)) && isPin(pin) && confirm === pin);

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		if (!ready) return;
		busy = true;
		error = '';
		try {
			const status = await api.post<AuthStatus>('/auth/pin', changing ? { current, pin } : { pin });
			applyStatus(status);
			if (changing) done = true;
			else await goto('/', { replaceState: true });
		} catch (e) {
			error = e instanceof ApiError ? e.message : 'Could not save the PIN.';
			if (e instanceof ApiError && e.status === 403) current = '';
		} finally {
			busy = false;
		}
	}
</script>

{#if done}
	<AuthCard title="PIN changed" subtitle="Use the new PIN next time you sign in. Devices that are signed in stay signed in.">
		<a class="btn btn-primary" href="/">Back to Song Vault</a>
	</AuthCard>
{:else}
	<AuthCard
		title={changing ? 'Change PIN' : 'Choose a PIN'}
		subtitle={changing
			? 'Enter your current PIN, then the new one.'
			: 'Song Vault now uses a 4 to 6 digit PIN instead of a password. You only need to do this once.'}
	>
		<form onsubmit={submit}>
			{#if changing}
				<PinInput id="current" label="Current PIN" bind:value={current} autofocus />
			{/if}
			<PinInput id="pin" label={changing ? 'New PIN' : 'PIN'} autocomplete="new-password" bind:value={pin} autofocus={!changing} />
			<PinInput id="confirm" label="Enter it again" autocomplete="new-password" bind:value={confirm} />
			{#if problem || error}<p class="error" role="alert">{error || problem}</p>{/if}
			<button class="btn btn-primary" type="submit" disabled={busy || !ready}>
				{busy ? 'Saving…' : changing ? 'Change PIN' : 'Set PIN'}
			</button>
			{#if changing}<a class="btn btn-ghost" href="/">Cancel</a>{/if}
		</form>
	</AuthCard>
{/if}

<style>
	form {
		display: grid;
		gap: 0.6rem;
	}
	.btn {
		min-height: 44px;
	}
	.btn-primary {
		margin-top: 0.35rem;
	}
</style>
