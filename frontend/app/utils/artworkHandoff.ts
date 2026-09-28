export interface ArtworkLoad {
	ready: Promise<void>;
	cancel: () => void;
}

export type ArtworkHandoffResult =
	| { status: "ready"; url: string }
	| { status: "empty" }
	| { status: "failed"; url: string }
	| { status: "stale" };

export type ArtworkLoader = (url: string) => ArtworkLoad;

export function decodeArtwork(url: string): ArtworkLoad {
	const image = new Image();
	image.decoding = "async";
	image.referrerPolicy = "no-referrer";
	image.src = url;

	let settled = false;
	let rejectCancellation: ((reason: Error) => void) | undefined;
	const ready = new Promise<void>((resolve, reject) => {
		rejectCancellation = reject;
		void image.decode().then(resolve, reject);
	}).finally(() => {
		settled = true;
	});

	return {
		ready,
		cancel() {
			if (settled) return;
			image.removeAttribute("src");
			rejectCancellation?.(new Error("Artwork load cancelled"));
		},
	};
}

export class ArtworkHandoff {
	readonly #loads = new Map<string, ArtworkLoad>();
	#revision = 0;

	constructor(private readonly load: ArtworkLoader = decodeArtwork) {}

	get retainedUrls(): readonly string[] {
		return [...this.#loads.keys()];
	}

	async update(currentUrl: string | null, nextUrl: string | null): Promise<ArtworkHandoffResult> {
		const revision = ++this.#revision;
		const wanted = new Set([currentUrl, nextUrl].filter((url): url is string => Boolean(url)));

		for (const [url, pending] of this.#loads) {
			if (wanted.has(url)) continue;
			pending.cancel();
			this.#loads.delete(url);
		}

		const current = currentUrl ? this.#getOrStart(currentUrl) : null;
		if (nextUrl) this.#getOrStart(nextUrl);
		if (!currentUrl || !current)
			return revision === this.#revision ? { status: "empty" } : { status: "stale" };

		try {
			await current.ready;
		} catch {
			if (this.#loads.get(currentUrl) === current) this.#loads.delete(currentUrl);
			return revision === this.#revision
				? { status: "failed", url: currentUrl }
				: { status: "stale" };
		}

		return revision === this.#revision && this.#loads.get(currentUrl) === current
			? { status: "ready", url: currentUrl }
			: { status: "stale" };
	}

	dispose() {
		this.#revision++;
		for (const pending of this.#loads.values()) pending.cancel();
		this.#loads.clear();
	}

	#getOrStart(url: string) {
		const retained = this.#loads.get(url);
		if (retained) return retained;

		const pending = this.load(url);
		this.#loads.set(url, pending);
		void pending.ready.catch(() => {
			if (this.#loads.get(url) === pending) this.#loads.delete(url);
		});
		return pending;
	}
}
