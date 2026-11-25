<script lang="ts">
	import '../app.css';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { auth, logout, refreshAuth, safeNext } from '#lib/auth.svelte.ts';
	import Icon from '#lib/components/Icon.svelte';
	import UploadPanel from '#lib/components/UploadPanel.svelte';
	import { counts, refreshCounts } from '#lib/counts.svelte.ts';
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

	// Header badges: refresh on navigation and when an upload lands; poll while clips process.
	$effect(() => {
		if (!auth.authenticated) return;
		void page.url.pathname;
		void uploads.version;
		refreshCounts();
	});
	$effect(() => {
		if (!auth.authenticated || counts.processing === 0) return;
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
				<a href="/uploads" class="navlink" aria-current={current('/uploads')}>History</a>
			</nav>
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
						<a role="menuitem" href="/pin" onclick={() => (accountOpen = false)}><Icon name="lock" size={18} /> Change PIN</a>
						<button role="menuitem" onclick={signOut}><Icon name="logout" size={18} /> Sign out</button>
					</div>
				{/if}
			</div>
		</header>
	{/if}
	<main>
		{@render children()}
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
	main {
		padding-bottom: calc(2rem + env(safe-area-inset-bottom));
	}
</style>
