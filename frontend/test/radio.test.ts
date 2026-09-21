import { track, discovery } from "./engine-fixtures";
import { describe, expect, it, vi } from "vitest";
import { createRadioClient } from "../app/player/radio";
import type { RadioPreview, RadioSource } from "../shared/radio";

const source: RadioSource = {
	kind: "track",
	source_url: "https://music.youtube.com/watch?v=Pqp9fDRp1lw",
	title: "Hazy Mercer",
};
const preview: RadioPreview = {
	seed: { kind: "track", identity: track.identity, source_url: track.source_url },
};

describe("radio previews", () => {
	it("uses the provider's playlist identity as the radio seed", async () => {
		const seed = {
			kind: "playlist" as const,
			identity: { namespace: "youtube", external_id: "PL12345678901234" },
			source_url: "https://music.youtube.com/playlist?list=PL12345678901234",
		};
		const request = vi
			.fn<typeof fetch>()
			.mockResolvedValue(Response.json(discovery("playlist", { playlist: seed })));
		const client = createRadioClient(request);
		await client.open({ kind: "playlist", source_url: seed.source_url, title: "Playlist" });
		expect(request.mock.calls[0]![0]).toBe("/api/catalog/playlist");
		expect(client.preview.value?.seed).toEqual(seed);
	});
	it("resolves a seed through the catalog without queue mutation", async () => {
		const request = vi
			.fn<typeof fetch>()
			.mockResolvedValue(new Response(JSON.stringify(track)));
		const client = createRadioClient(request);
		await client.open(source);
		expect(client.preview.value).toEqual(preview);
		const [path, options] = request.mock.calls[0]!;
		expect(path).toBe("/api/catalog/track");
		expect(JSON.parse(String(options?.body))).toEqual({ source_url: source.source_url });
		expect(new Headers(options?.headers).get("Idempotency-Key")).toBeNull();
		expect(client.loading.value).toBe(false);
	});
	it("discards an old response after another seed was opened", async () => {
		let resolve!: (response: Response) => void;
		const pending = new Promise<Response>((done) => {
			resolve = done;
		});
		const request = vi
			.fn<typeof fetch>()
			.mockReturnValueOnce(pending)
			.mockResolvedValueOnce(
				new Response(JSON.stringify({ ...track, source_url: "https://youtu.be/another" })),
			);
		const client = createRadioClient(request);
		const first = client.open(source);
		await client.open({ ...source, title: "Other" });
		resolve(new Response(JSON.stringify(track)));
		await first;
		expect(client.preview.value?.seed.source_url).toBe("https://youtu.be/another");
		expect(client.source.value?.title).toBe("Other");
	});
	it("clears previews on sign-out and ignores late responses", async () => {
		let resolve!: (response: Response) => void;
		const client = createRadioClient(
			vi.fn<typeof fetch>().mockReturnValue(
				new Promise((done) => {
					resolve = done;
				}),
			),
		);
		const opening = client.open(source);
		client.dispose();
		resolve(new Response(JSON.stringify(track)));
		await opening;
		expect(client.preview.value).toBeNull();
		expect(client.source.value).toBeNull();
		expect(client.loading.value).toBe(false);
	});
	it("keeps provider errors recoverable without changing the queue", async () => {
		const request = vi
			.fn<typeof fetch>()
			.mockResolvedValueOnce(
				new Response(JSON.stringify({ detail: "Radio is unavailable." }), { status: 502 }),
			)
			.mockResolvedValueOnce(new Response(JSON.stringify(track)));
		const client = createRadioClient(request);
		await client.open(source);
		expect(client.error.value).toBe("Radio is unavailable.");
		await client.open(source);
		expect(client.error.value).toBe("");
		expect(client.preview.value).toEqual(preview);
	});
});
