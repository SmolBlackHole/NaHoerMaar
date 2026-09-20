import { afterEach, describe, expect, it, vi } from "vitest";
import { createApp, toWebHandler } from "h3";
import artwork from "../server/routes/artwork/[...path].get";

const handle = toWebHandler(createApp().use(artwork));
const request = (path: string) => handle(new Request(`http://localhost/artwork/${path}`));
afterEach(() => vi.unstubAllGlobals());

describe("public artwork thumbnails", () => {
	it("fetches only the fixed thumbnail host without forwarding user credentials", async () => {
		const fetch = vi.fn().mockResolvedValue(
			new Response(new Uint8Array([1, 2, 3]), {
				headers: { "Content-Type": "image/webp" },
			}),
		);
		vi.stubGlobal("fetch", fetch);
		const result = await request("vi_webp/pFnD39VcKIg/maxresdefault.webp");
		expect(result.status).toBe(200);
		expect(fetch.mock.calls[0]?.[0]).toBe(
			"https://i.ytimg.com/vi_webp/pFnD39VcKIg/maxresdefault.webp",
		);
		expect(fetch.mock.calls[0]?.[1]).toMatchObject({ redirect: "error" });
		expect(fetch.mock.calls[0]?.[1].headers).toBeUndefined();
		expect(result.headers.get("content-type")).toBe("image/webp");
		expect(result.headers.get("cache-control")).toBe("public, max-age=3600");
	});
	it("rejects arbitrary URLs and query strings before fetching", async () => {
		const fetch = vi.fn();
		vi.stubGlobal("fetch", fetch);
		for (const path of [
			"https://example.com/private",
			"vi/short/hqdefault.jpg",
			"vi/pFnD39VcKIg/hqdefault.jpg?url=other",
		])
			expect((await request(path)).status).toBe(404);
		expect(fetch).not.toHaveBeenCalled();
	});
	it("rejects non-image and oversized responses", async () => {
		const fetch = vi
			.fn()
			.mockResolvedValueOnce(
				new Response("<html>", { headers: { "Content-Type": "text/html" } }),
			)
			.mockResolvedValueOnce(
				new Response(new Uint8Array(2 * 1024 * 1024 + 1), {
					headers: { "Content-Type": "image/jpeg" },
				}),
			);
		vi.stubGlobal("fetch", fetch);
		const path = "vi/pFnD39VcKIg/hqdefault.jpg";
		expect((await request(path)).status).toBe(502);
		expect((await request(path)).status).toBe(502);
	});
});
