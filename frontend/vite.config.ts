import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [
		sveltekit({
			compilerOptions: {
				// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
				runes: ({ filename }) =>
					filename.split(/[/\\]/).includes('node_modules') ? undefined : true
			},
			// A single-page app: FastAPI serves build/ and answers unknown paths with index.html.
			adapter: adapter({ fallback: 'index.html' })
		})
	],
	server: {
		// `npm run dev` talks to the FastAPI dev server.
		proxy: { '/api': 'http://127.0.0.1:8000' }
	}
});
