<script lang="ts">
	import '../app.css';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { auth, logout, refreshAuth, safeNext } from '#lib/auth.svelte.ts';
	import Icon from '#lib/components/Icon.svelte';
	import UploadPanel from '#lib/components/UploadPanel.svelte';
	import { counts, refreshCounts } from '#lib/counts.svelte.ts';
	import { plural } from '#lib/format.ts';
	import { notebook } from '#lib/notebook.svelte.ts';
	import { uploads } from '#lib/uploads.svelte.ts';
	import type { LayoutProps } from './$types';

	let { children }: LayoutProps = $props();

	const PUBLIC_ROUTES = ['/login', '/setup'];
	/** Full-screen cards without the top bar. */
	const BARE_ROUTES = [...PUBLIC_ROUTES, '/pin'];

	refreshAuth();

	let accountOpen = $state(false);

	// Where the user must be sent before this page can render (null = stay).
	// Signing in returns to the page that asked for it (e.g. a share from the share sheet).
	const redirect = $derived.by(() => {
		if (!auth.checked) return null;
		const path = page.url.pathname;
		if (auth.setupRequired) return path === '/setup' ? null : '/setup';
		if (!auth.authenticated) {
			if (path === '/login') return null;
			const here = path + page.url.search;
			return here === '/' ? '/login' : `/login?next=${encodeURIComponent(here)}`;
		}
		// Signed in with the old password: choose a PIN once.
		if (!auth.pinSet) return path === '/pin' ? null : '/pin';
		return PUBLIC_ROUTES.includes(path) ? safeNext(page.url.searchParams.get('next')) : null;
	});
	const bare = $derived(BARE_ROUTES.includes(page.url.pathname));

	$effect(() => {
		if (redirect) goto(redirect, { replaceState: true });
	});

	// Offline (spec 5.11): only the Notebook works. Keep checking for the server, and carry
	// on as normal once it answers.
	const offlineHere = $derived(auth.offline && !page.url.pathname.startsWith('/notebook'));
	let rechecking = $state(false);
	async function recheck() {
		if (rechecking || document.visibilityState !== 'visible') return;
		rechecking = true;
		try {
			await refreshAuth();
		} finally {
			rechecking = false;
		}
	}
	$effect(() => {
		if (!auth.offline) return;
		const timer = setInterval(recheck, 20_000);
		window.addEventListener('online', recheck);
		document.addEventListener('visibilitychange', recheck);
		return () => {
			clearInterval(timer);
			window.removeEventListener('online', recheck);
			document.removeEventListener('visibilitychange', recheck);
		};
	});

	// The Notebook syncs in the background on every page, so notes written offline go up
	// as soon as the server can be reached.
	$effect(() => {
		if (auth.authenticated) void notebook.start();
	});

	// Header badges: refresh on navigation and when an upload lands; poll while clips process.
	$effect(() => {
		if (!auth.authenticated || auth.offline) return;
		void page.url.pathname;
		void uploads.version;
		refreshCounts();
	});
	$effect(() => {
		if (!auth.authenticated || auth.offline || counts.processing === 0) return;
		const timer = setInterval(refreshCounts, 8000);
		return () => clearInterval(timer);
	});

	// Leaving the page would stop uploads in progress.
	$effect(() => {
		if (!uploads.active) return;
		const warn = (e: BeforeUnloadEvent) => e.preventDefault();
		window.addEventListener('beforeunload', warn);
		return () => window.removeEventListener('beforeunload', warn);
	});

	// Record from a song page into that song (spec 5.7).
	const recordHref = $derived(
		page.route.id?.startsWith('/projects/[id]') && page.params.id ? `/record?song=${page.params.id}` : '/record'
	);

	function current(path: string): 'page' | undefined {
		const here = page.url.pathname;
		const home = here === '/' || here.startsWith('/projects') || here.startsWith('/uncategorized');
		return (path === '/' ? home : here.startsWith(path)) ? 'page' : undefined;
	}

	async function signOut() {
		accountOpen = false;
		// Signing out clears the Notebook from this device (spec 5.11).
		const waiting = notebook.waiting;
		if (
			waiting > 0 &&
			!confirm(
				`${plural(waiting, 'Notebook change')} ${waiting === 1 ? "hasn't" : "haven't"} reached the server yet. ` +
					'Signing out deletes them from this device. Sign out anyway?'
			)
		)
			return;
		await notebook.clear();
		await logout();
		await goto('/login', { replaceState: true });
	}

	function closeAccount(event: MouseEvent) {
		if (accountOpen && !(event.target as Element).closest('.account')) accountOpen = false;
	}
</script>

<svelte:window onclick={closeAccount} />

{#if !auth.checked || redirect}
	<div class="boot" aria-busy="true"></div>
{:else}
	{#if auth.authenticated && !bare}
		<header class="topbar no-print">
			<a href="/" class="brand" aria-label="Song Vault home">
				<span class="logo"><Icon name="music" size={18} /></span>
				<span class="brand-name">Song Vault</span>
			</a>
			<nav>
				<a href="/" class="navlink" aria-current={current('/')}>
					Projects{#if counts.uncategorized > 0}<span class="badge"
							>{counts.uncategorized}<span class="visually-hidden"> uncategorized</span></span
						>{/if}
				</a>
				<a href="/notebook" class="navlink" aria-current={current('/notebook')}>Notebook</a>
				<a href="/uploads" class="navlink wide-only" aria-current={current('/uploads')}>History</a>
			</nav>
			{#if auth.offline}
				<span class="offline-chip" title="Song Vault can't be reached. The Notebook still works.">
					<Icon name="cloud-off" size={16} /><span class="label">Offline</span>
				</span>
			{:else}
				<a class="btn btn-sm btn-icon record" href={recordHref} aria-label="Record" title="Record a take">
					<Icon name="record" size={17} />
				</a>
				<button
					class="btn btn-primary btn-sm upload"
					onclick={() => uploads.pick({ projectId: null, labelIds: [], label: 'Uncategorized' })}
					title="Upload recordings to Uncategorized"
				>
					<Icon name="upload" size={17} />
					<span class="label">Upload</span>
				</button>
			{/if}
			<div class="account">
				<button
					class="btn btn-ghost btn-sm btn-icon"
					aria-label="Account"
					aria-haspopup="menu"
					aria-expanded={accountOpen}
					onclick={() => (accountOpen = !accountOpen)}
				>
					<Icon name="user" size={19} />
				</button>
				{#if accountOpen}
					<div class="account-menu" role="menu">
						<!-- On narrow phones History moves here, to make room for the Notebook -->
						<a role="menuitem" class="narrow-only" href="/uploads" onclick={() => (accountOpen = false)}
							><Icon name="upload" size={18} /> Upload history</a
						>
						<a role="menuitem" href="/pin" onclick={() => (accountOpen = false)}><Icon name="lock" size={18} /> Change PIN</a>
						<button role="menuitem" onclick={signOut}><Icon name="logout" size={18} /> Sign out</button>
					</div>
				{/if}
			</div>
		</header>
	{/if}
	<main>
		{#if offlineHere}
			<div class="offline-page">
				<div class="offline-icon"><Icon name="cloud-off" size={28} /></div>
				<h1>Song Vault can't be reached</h1>
				<p class="muted">
					You're away from your home network, or the server is down. Your Notebook works offline, and what you
					write there syncs once you're back.
				</p>
				<div class="offline-actions">
					<a class="btn btn-primary" href="/notebook"><Icon name="notes" size={18} /> Open the Notebook</a>
					<button class="btn" disabled={rechecking} onclick={recheck}>{rechecking ? 'Checking…' : 'Try again'}</button>
				</div>
			</div>
		{:else}
			{@render children()}
		{/if}
	</main>
	{#if auth.authenticated}<UploadPanel />{/if}
{/if}

<style>
	.boot {
		min-height: 100dvh;
	}
	.topbar {
		position: sticky;
		top: 0;
		z-index: 20;
		display: flex;
		align-items: center;
		gap: 0.75rem;
		height: var(--header-h);
		padding: 0 max(0.75rem, env(safe-area-inset-left));
		background: rgb(17 19 23 / 0.88);
		backdrop-filter: blur(10px);
		border-bottom: 1px solid var(--border);
	}
	.brand {
		display: inline-flex;
		align-items: center;
		gap: 0.55rem;
		font-weight: 700;
		letter-spacing: -0.01em;
	}
	.logo {
		display: grid;
		place-items: center;
		width: 30px;
		height: 30px;
		border-radius: 8px;
		background: var(--accent);
		color: var(--accent-ink);
	}
	nav {
		display: flex;
		gap: 0.15rem;
		flex: 1;
		min-width: 0;
		/* Never wider than the bar, whatever the phone */
		overflow-x: auto;
		scrollbar-width: none;
	}
	.narrow-only {
		display: none !important;
	}
	.offline-chip {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
		flex-shrink: 0;
		padding: 0.3rem 0.6rem;
		border-radius: 999px;
		background: var(--surface-2);
		color: var(--text-muted);
		font-size: 0.82rem;
		font-weight: 600;
	}
	.offline-page {
		display: grid;
		justify-items: center;
		gap: 0.7rem;
		max-width: 460px;
		margin: 3rem auto 0;
		padding: 0 var(--gutter);
		text-align: center;
	}
	.offline-page h1 {
		font-size: 1.35rem;
	}
	.offline-page p {
		margin: 0;
	}
	.offline-icon {
		display: grid;
		place-items: center;
		width: 56px;
		height: 56px;
		border-radius: 16px;
		background: var(--surface-2);
		color: var(--text-muted);
	}
	.offline-actions {
		display: flex;
		flex-wrap: wrap;
		justify-content: center;
		gap: 0.5rem;
		margin-top: 0.4rem;
	}
	.navlink {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
		padding: 0.35rem 0.6rem;
		border-radius: var(--radius-sm);
		color: var(--text-muted);
		font-weight: 550;
		font-size: 0.93rem;
		white-space: nowrap;
	}
	.navlink:hover {
		color: var(--text);
	}
	.navlink[aria-current='page'] {
		color: var(--text);
		background: var(--surface-2);
	}
	.badge {
		min-width: 1.25rem;
		padding: 0 0.35rem;
		border-radius: 999px;
		background: var(--accent);
		color: var(--accent-ink);
		font-size: 0.72rem;
		font-weight: 700;
		text-align: center;
	}
	.account {
		position: relative;
		flex-shrink: 0;
		color: var(--text-muted);
	}
	.account-menu {
		position: absolute;
		right: 0;
		top: calc(100% + 8px);
		z-index: 25;
		min-width: 180px;
		padding: 0.35rem;
		border-radius: var(--radius);
		background: var(--surface-2);
		border: 1px solid var(--border);
		box-shadow: 0 16px 40px rgb(0 0 0 / 0.45);
	}
	.account-menu a,
	.account-menu button {
		display: flex;
		align-items: center;
		gap: 0.6rem;
		width: 100%;
		padding: 0.55rem 0.65rem;
		border: 0;
		border-radius: var(--radius-sm);
		background: transparent;
		color: var(--text);
		text-align: left;
		cursor: pointer;
		font-size: 0.95rem;
	}
	.account-menu a:hover,
	.account-menu button:hover {
		background: var(--surface-3);
	}
	.record :global(svg) {
		color: #ff6b6b;
		fill: currentColor;
	}
	@media (max-width: 640px) {
		.brand-name,
		.upload .label {
			display: none;
		}
		.upload {
			width: 34px;
			padding: 0;
		}
		.topbar {
			gap: 0.4rem;
		}
		.navlink {
			padding: 0.35rem 0.45rem;
		}
	}
	/* Narrow phones: no room for the logo and four links, so History goes in the account menu */
	@media (max-width: 480px) {
		.brand,
		.wide-only,
		.offline-chip .label {
			display: none;
		}
		.narrow-only {
			display: flex !important;
		}
	}
	main {
		padding-bottom: calc(2rem + env(safe-area-inset-bottom));
	}
</style>
