// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import { describe, expect, it, vi } from "vitest";
import {
	ApiFailure,
	InvalidResponse,
	SessionLost,
	createTransport,
} from "../../app/core/api/transport";
import { fixture } from "./fixture";

describe("new backend HTTP boundary", () => {
	it.each(["POST", "PUT", "PATCH", "DELETE"])("adds session CSRF to %s", async (method) => {
		const { auth, fetcher } = fixture();
		fetcher.mockResolvedValue(new Response(null, { status: 204 }));
		await createTransport(fetcher, auth)("/api/example", { method });
		const options = fetcher.mock.calls[0]![1]!;
		expect(new Headers(options.headers).get("X-CSRF-Token")).toBe("session-token");
		expect(options.credentials).toBe("same-origin");
		expect(options.cache).toBe("no-store");
	});

	it("discovers the cookie session before local CSRF is known", async () => {
		const { client, credentials, fetcher } = fixture();
		credentials.csrf = null;
		fetcher.mockResolvedValue(Response.json({ csrf: "restored" }));
		expect(await client.account.session()).toMatchObject({ csrf: "restored" });
		expect(new Headers(fetcher.mock.calls[0]![1]?.headers).has("X-CSRF-Token")).toBe(false);
		await expect(client.player.play({ operation_id: "op" })).rejects.toBeInstanceOf(
			SessionLost,
		);
		expect(fetcher).toHaveBeenCalledOnce();
	});

	it("rejects an old response after an account switch", async () => {
		const { client, credentials, fetcher, auth } = fixture();
		fetcher.mockImplementation(async () => {
			credentials.generation++;
			return Response.json({ error: "signed_out" }, { status: 401 });
		});
		await expect(client.account.session()).rejects.toBeInstanceOf(SessionLost);
		expect(auth.lost).not.toHaveBeenCalled();
	});

	it("also checks the account after asynchronous JSON decoding", async () => {
		const { client, credentials, fetcher, auth } = fixture();
		const response = Response.json({}, { status: 401 });
		vi.spyOn(response, "json").mockImplementation(async () => {
			credentials.generation++;
			return { error: "signed_out" };
		});
		fetcher.mockResolvedValue(response);
		await expect(client.account.profile()).rejects.toBeInstanceOf(SessionLost);
		expect(auth.lost).not.toHaveBeenCalled();
	});

	it("invalidates authentication but preserves ordinary forbidden errors", async () => {
		const { client, fetcher, auth } = fixture();
		fetcher.mockResolvedValueOnce(
			Response.json(
				{ error: "csrf_failed" },
				{
					status: 403,
					headers: { "x-request-id": "diagnostic-id" },
				},
			),
		);
		await expect(client.account.profile()).rejects.toMatchObject({
			status: 403,
			requestId: "diagnostic-id",
			error: { error: "csrf_failed" },
		});
		expect(auth.lost).not.toHaveBeenCalled();
		fetcher.mockResolvedValueOnce(Response.json({ error: "access_denied" }, { status: 403 }));
		await expect(client.account.profile()).rejects.toBeInstanceOf(SessionLost);
		expect(auth.lost).toHaveBeenCalledWith("access_denied");
	});

	it("does not turn HTML success into data or retry a failed mutation", async () => {
		const { client, fetcher } = fixture();
		fetcher.mockResolvedValueOnce(new Response("<html>proxy</html>"));
		await expect(client.account.profile()).rejects.toBeInstanceOf(InvalidResponse);
		fetcher.mockResolvedValueOnce(new Response("<html>unavailable</html>", { status: 502 }));
		await expect(client.player.skip({ operation_id: "op" })).rejects.toBeInstanceOf(ApiFailure);
		expect(fetcher).toHaveBeenCalledTimes(2);
	});

	it("passes cancellation to fetch", async () => {
		const { client, fetcher } = fixture();
		const controller = new AbortController();
		fetcher.mockImplementation(async (_url, options) => {
			controller.abort();
			options?.signal?.throwIfAborted();
			return Response.json([]);
		});
		await expect(client.listening.recent(5, controller.signal)).rejects.toMatchObject({
			name: "AbortError",
		});
	});
});
