// Streams a File through SHA-256 off the main thread (spec 2.4). Reading in slices
// keeps memory flat, so multi-GB videos don't crash a phone's tab.
import { createSHA256 } from 'hash-wasm';

const SLICE = 8 * 1024 * 1024;

type Ctx = {
	postMessage(message: unknown): void;
	onmessage: ((event: MessageEvent<{ file: File }>) => void) | null;
};
const ctx = self as unknown as Ctx;

ctx.onmessage = async (event) => {
	const { file } = event.data;
	try {
		const hasher = await createSHA256();
		hasher.init();
		for (let offset = 0; offset < file.size; offset += SLICE) {
			const slice = new Uint8Array(await file.slice(offset, offset + SLICE).arrayBuffer());
			hasher.update(slice);
			ctx.postMessage({ progress: Math.min(offset + SLICE, file.size) });
		}
		ctx.postMessage({ digest: hasher.digest('hex') });
	} catch (error) {
		ctx.postMessage({ error: String(error) });
	}
};
