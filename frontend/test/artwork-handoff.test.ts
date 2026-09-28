import { describe, expect, it, vi } from "vitest";
import { ArtworkHandoff, type ArtworkLoad, type ArtworkLoader } from "../app/utils/artworkHandoff";

function deferredLoads() {
	const loads = new Map<
		string,
		{
			resolve: () => void;
			reject: (reason?: unknown) => void;
			cancel: ReturnType<typeof vi.fn>;
		}
	>();
	const loader: ArtworkLoader = (url) => {
		let resolve!: () => void;
		let reject!: (reason?: unknown) => void;
		const ready = new Promise<void>((done, fail) => {
			resolve = done;
			reject = fail;
		});
		const cancel = vi.fn(() => reject(new Error("cancelled")));
		loads.set(url, { resolve, reject, cancel });
		return { ready, cancel } satisfies ArtworkLoad;
	};
	return { loads, loader };
}

describe("artwork handoff", () => {
	it("reveals the current cover only after it has decoded", async () => {
		const { loads, loader } = deferredLoads();
		const handoff = new ArtworkHandoff(loader);
		const result = handoff.update("current", null);

		expect(loads.has("current")).toBe(true);
		loads.get("current")!.resolve();
		await expect(result).resolves.toEqual({ status: "ready", url: "current" });
	});

	it("never lets a late earlier cover replace the current track", async () => {
		const { loads, loader } = deferredLoads();
		const handoff = new ArtworkHandoff(loader);
		const earlier = handoff.update("earlier", null);
		const current = handoff.update("current", null);

		expect(loads.get("earlier")!.cancel).toHaveBeenCalledOnce();
		loads.get("current")!.resolve();
		await expect(current).resolves.toEqual({ status: "ready", url: "current" });
		await expect(earlier).resolves.toEqual({ status: "stale" });
	});

	it("promotes the preloaded next cover without loading it twice", async () => {
		const { loads, loader } = deferredLoads();
		const countedLoader = vi.fn(loader);
		const handoff = new ArtworkHandoff(countedLoader);
		const first = handoff.update("first", "next");
		loads.get("first")!.resolve();
		loads.get("next")!.resolve();
		await first;

		await expect(handoff.update("next", "later")).resolves.toEqual({
			status: "ready",
			url: "next",
		});
		expect(countedLoader.mock.calls.filter(([url]) => url === "next")).toHaveLength(1);
	});

	it("replaces only the next preload when the queue is reordered", async () => {
		const { loads, loader } = deferredLoads();
		const countedLoader = vi.fn(loader);
		const handoff = new ArtworkHandoff(countedLoader);
		const beforeReorder = handoff.update("current", "old-next");
		const afterReorder = handoff.update("current", "new-next");

		expect(loads.get("old-next")!.cancel).toHaveBeenCalledOnce();
		expect(countedLoader.mock.calls.filter(([url]) => url === "current")).toHaveLength(1);
		loads.get("current")!.resolve();
		loads.get("new-next")!.resolve();
		await expect(beforeReorder).resolves.toEqual({ status: "stale" });
		await expect(afterReorder).resolves.toEqual({ status: "ready", url: "current" });
	});

	it("returns a retryable failure without revealing a broken cover", async () => {
		const { loads, loader } = deferredLoads();
		const handoff = new ArtworkHandoff(loader);
		const result = handoff.update("broken", null);
		loads.get("broken")!.reject(new Error("decode failed"));

		await expect(result).resolves.toEqual({ status: "failed", url: "broken" });
		expect(handoff.retainedUrls).toEqual([]);
	});

	it("retains only the current and next useful cover", async () => {
		const { loads, loader } = deferredLoads();
		const handoff = new ArtworkHandoff(loader);
		const first = handoff.update("first", "second");
		loads.get("first")!.resolve();
		loads.get("second")!.resolve();
		await first;

		const later = handoff.update("third", "fourth");
		expect(handoff.retainedUrls).toEqual(["third", "fourth"]);
		loads.get("third")!.resolve();
		loads.get("fourth")!.resolve();
		await later;
	});
});
