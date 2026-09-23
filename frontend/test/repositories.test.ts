import { describe, expect, it, vi } from "vitest";
import { createHttpTransport, ApiFailure, SessionLost } from "../app/repositories/transport";
import { createAccountRepository } from "../app/repositories/account";
import { createSessionRepository } from "../app/repositories/session";
import { createCatalogRepository } from "../app/repositories/catalog";
import { createDiagnosticsRepository } from "../app/repositories/diagnostics";
import { createAccessRepository } from "../app/repositories/access";

function setup() {
	const fetcher = vi.fn<typeof fetch>();
	const credentials = { generation: 0, csrfToken: "csrf" as string | null };
	const lost = vi.fn();
	const json = createHttpTransport(fetcher, { current: () => ({ ...credentials }), lost });
	return {
		fetcher,
		credentials,
		lost,
		json,
		access: createAccessRepository(json),
		account: createAccountRepository(json),
		session: createSessionRepository(json),
		catalog: createCatalogRepository(json),
		diagnostics: createDiagnosticsRepository(json),
	};
}
describe("repositories and shared transport", () => {
	it("reports an empty JSON error as an HTTP failure", async () => {
		const { fetcher, session } = setup();
		fetcher.mockResolvedValueOnce(Response.json(null, { status: 400 }));
		await expect(session.channels()).rejects.toMatchObject({
			status: 400,
			data: { code: "http_error", retryable: false },
		});
	});
	it("keeps wire payloads, idempotency keys and CSRF at the boundary", async () => {
		const { fetcher, session, catalog } = setup();
		fetcher.mockImplementation(async () => Response.json({}));
		await session.moveTrack("request", "entry", {
			before_entry_id: null,
			expected_queue_revision: 7,
		});
		const [path, init] = fetcher.mock.calls[0]!;
		expect(path).toBe("/api/queue/entry/position");
		expect(init?.method).toBe("PUT");
		expect(JSON.parse(String(init?.body))).toEqual({
			before_entry_id: null,
			expected_queue_revision: 7,
		});
		expect(new Headers(init?.headers).get("Idempotency-Key")).toBe("request");
		expect(new Headers(init?.headers).get("X-CSRF-Token")).toBe("csrf");
		await catalog.search("a & b", "youtube_music");
		expect(fetcher.mock.lastCall?.[0]).toContain("q=a%20%26%20b");
		expect(new Headers(fetcher.mock.lastCall?.[1]?.headers).has("X-CSRF-Token")).toBe(false);
	});
	it("accepts empty logout responses and allows session restore before login", async () => {
		const { fetcher, account, credentials } = setup();
		fetcher.mockResolvedValueOnce(new Response(null, { status: 204 }));
		await expect(account.logout()).resolves.toBeUndefined();
		credentials.csrfToken = null;
		fetcher.mockResolvedValueOnce(Response.json({ profile: "restored" }));
		await expect(account.session()).resolves.toEqual({ profile: "restored" });
		await expect(account.logout()).rejects.toBeInstanceOf(SessionLost);
		expect(fetcher).toHaveBeenCalledTimes(2);
	});
	it("requests only log entries newer than the last received entry", async () => {
		const { fetcher, diagnostics } = setup();
		fetcher.mockResolvedValueOnce(Response.json({ entries: [] }));
		await diagnostics.logs(42);
		expect(fetcher).toHaveBeenCalledWith(
			"/api/diagnostics/logs?after=42",
			expect.objectContaining({ cache: "no-store" }),
		);
	});
	it("uses the admin access contract for grants and revocations", async () => {
		const { fetcher, access } = setup();
		fetcher.mockImplementation(async () => Response.json({ grants: [], history: [] }));
		await access.members();
		expect(fetcher.mock.calls[0]?.[0]).toBe("/api/admin/access/members");
		await access.grant("123");
		expect(fetcher.mock.calls[1]?.[0]).toBe("/api/admin/access/123");
		expect(fetcher.mock.calls[1]?.[1]?.method).toBe("PUT");
		await access.revoke("123");
		expect(fetcher.mock.calls[2]?.[1]?.method).toBe("DELETE");
	});
	it("preserves structured API errors and reports non-JSON gateway failures", async () => {
		const { fetcher, catalog } = setup();
		fetcher.mockResolvedValueOnce(
			Response.json({ code: "expired", message: "Expired" }, { status: 410 }),
		);
		await expect(catalog.snapshot("search", "old")).rejects.toMatchObject({
			status: 410,
			message: "Expired",
		});
		fetcher.mockResolvedValueOnce(new Response("<html>unavailable</html>", { status: 502 }));
		await expect(catalog.search("song", "youtube_music")).rejects.toBeInstanceOf(ApiFailure);
	});
	it("invalidates authentication and rejects responses from the previous account", async () => {
		const { fetcher, account, credentials, lost } = setup();
		fetcher.mockResolvedValueOnce(Response.json({ code: "access_denied" }, { status: 403 }));
		await expect(account.session()).rejects.toBeInstanceOf(SessionLost);
		expect(lost).toHaveBeenCalledWith("access_denied");
		let resolve!: (response: Response) => void;
		fetcher.mockReturnValueOnce(
			new Promise((done) => {
				resolve = done;
			}),
		);
		const pending = account.session();
		credentials.generation++;
		resolve(Response.json({}));
		await expect(pending).rejects.toBeInstanceOf(SessionLost);
	});
	it("passes cancellation through without changing the caller's signal", async () => {
		const { fetcher, catalog } = setup();
		const controller = new AbortController();
		fetcher.mockImplementation(async (_path, options) => {
			await new Promise<void>((_resolve, reject) =>
				options?.signal?.addEventListener("abort", () => reject(options.signal?.reason)),
			);
			return Response.json({});
		});
		const pending = catalog.resolveTrack("url", controller.signal);
		controller.abort();
		await expect(pending).rejects.toMatchObject({ name: "AbortError" });
	});
});
