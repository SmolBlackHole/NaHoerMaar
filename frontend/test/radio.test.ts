import { describe, expect, it, vi } from "vitest";
import { createRadioClient } from "../app/player/radio";
import type { RadioPreview, RadioSource } from "../shared/radio";

const source: RadioSource = {
	kind: "track",
	source_url: "https://music.youtube.com/watch?v=Pqp9fDRp1lw",
	title: "Hazy Mercer",
};
const preview: RadioPreview = {
	id: "one",
	seed: { kind: "track", identifier: "Pqp9fDRp1lw", title: source.title },
	entries: [],
};

describe("radio previews", () => {
	it("uses an authenticated request and a unique request identity", async () => {
		const request = vi
			.fn<typeof fetch>()
			.mockResolvedValue(new Response(JSON.stringify(preview)));
		const client = createRadioClient(request);
		await client.open(source);
		expect(client.preview.value).toEqual(preview);
		const [path, options] = request.mock.calls[0]!;
		expect(path).toBe("/api/radio/preview");
		expect(JSON.parse(String(options?.body))).toEqual(source);
		expect(new Headers(options?.headers).get("Idempotency-Key")).toBeTruthy();
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
			.mockResolvedValueOnce(new Response(JSON.stringify({ ...preview, id: "two" })));
		const client = createRadioClient(request);
		const first = client.open(source);
		await client.open({ ...source, title: "Other" });
		resolve(new Response(JSON.stringify(preview)));
		await first;
		expect(client.preview.value?.id).toBe("two");
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
		resolve(new Response(JSON.stringify(preview)));
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
			.mockResolvedValueOnce(new Response(JSON.stringify(preview)));
		const client = createRadioClient(request);
		await client.open(source);
		expect(client.error.value).toBe("Radio is unavailable.");
		await client.open(source);
		expect(client.error.value).toBe("");
		expect(client.preview.value).toEqual(preview);
	});
});
