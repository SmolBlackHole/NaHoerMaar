import { afterEach, describe, expect, it, vi } from "vitest";
import { createSessionClient, SessionLost } from "../app/auth/client";
import { createPlayerClient } from "../app/player/client";
import type { ListenerSession } from "../shared/session";
import { defaultAppearance } from "../shared/appearance";

const account = (): ListenerSession => ({
	appearance: { ...defaultAppearance },
	profile: { id: "first", name: "Alice", avatar: "0002" },
	profile_complete: true,
	csrf_token: "session-bound-csrf",
	expires_at: Date.now() / 1000 + 60,
});
const cleanup: (() => void)[] = [];
afterEach(() => {
	cleanup.splice(0).forEach((close) => close());
	vi.useRealTimers();
});

function setup() {
	const fetcher = vi.fn<typeof fetch>();
	const client = createSessionClient(fetcher);
	cleanup.push(client.dispose);
	return { fetcher, client };
}

describe("server sessions", () => {
	it("gets identity from the server and adds CSRF only to mutations", async () => {
		const { client, fetcher } = setup();
		expect(client.status.value).toBe("checking");
		fetcher.mockResolvedValueOnce(Response.json(account()));
		await client.restore();
		expect(client.profile.value?.name).toBe("Alice");
		fetcher.mockResolvedValue(Response.json({}));
		await client.request("/api/state");
		expect(new Headers(fetcher.mock.lastCall?.[1]?.headers).has("X-CSRF-Token")).toBe(false);
		await client.request("/api/queue", { method: "POST", body: "{}" });
		expect(new Headers(fetcher.mock.lastCall?.[1]?.headers).get("X-CSRF-Token")).toBe(
			"session-bound-csrf",
		);
	});
	it.each([
		[401, "signed_out", "signed_out"],
		[403, "access_denied", "forbidden"],
		[503, "access_unavailable", "unavailable"],
	])("separates a %s response from a transport failure", async (status, code, expected) => {
		const { client, fetcher } = setup();
		fetcher.mockResolvedValue(Response.json({ code }, { status: Number(status) }));
		await client.restore();
		expect(client.status.value).toBe(expected);
		expect(client.profile.value).toBeNull();
		await expect(client.request("/api/state")).rejects.toBeInstanceOf(SessionLost);
	});
	it("keeps network failure separate from denial and permits retry", async () => {
		const { client, fetcher } = setup();
		fetcher.mockRejectedValueOnce(new TypeError("offline"));
		await client.restore();
		expect(client.status.value).toBe("unavailable");
		fetcher.mockResolvedValueOnce(Response.json(account()));
		await client.restore();
		expect(client.status.value).toBe("authenticated");
	});
	it("expires the local session without waiting for a user action", async () => {
		vi.useFakeTimers();
		const { client, fetcher } = setup();
		fetcher.mockResolvedValueOnce(Response.json(account()));
		await client.restore();
		vi.advanceTimersByTime(60_001);
		expect(client.status.value).toBe("signed_out");
		expect(client.profile.value).toBeNull();
	});
	it("does not restore a session from a response arriving after logout", async () => {
		const { client, fetcher } = setup();
		fetcher.mockResolvedValueOnce(Response.json(account()));
		await client.restore();
		let resolve!: (value: Response) => void;
		fetcher.mockReturnValueOnce(
			new Promise((done) => {
				resolve = done;
			}),
		);
		const checking = client.restore();
		client.lost("signed_out");
		resolve(Response.json(account()));
		await checking;
		expect(client.status.value).toBe("signed_out");
	});
	it("does not report successful logout when the server cannot be reached", async () => {
		const { client, fetcher } = setup();
		fetcher.mockResolvedValueOnce(Response.json(account()));
		await client.restore();
		fetcher.mockRejectedValueOnce(new TypeError("offline"));
		expect(await client.signOut()).toBe(false);
		expect(client.status.value).toBe("authenticated");
		expect(client.error.value).toContain("Couldn't sign out");
		fetcher.mockResolvedValueOnce(new Response(null, { status: 204 }));
		expect(await client.signOut()).toBe(true);
		expect(client.status.value).toBe("signed_out");
	});
	it("blocks a late mutation response from a previous account", async () => {
		const { client, fetcher } = setup();
		fetcher.mockResolvedValueOnce(Response.json(account()));
		await client.restore();
		let resolve!: (value: Response) => void;
		fetcher.mockReturnValueOnce(
			new Promise((done) => {
				resolve = done;
			}),
		);
		const request = client.request("/api/queue", { method: "POST" });
		const rejected = expect(request).rejects.toBeInstanceOf(SessionLost);
		client.lost("signed_out");
		fetcher.mockResolvedValueOnce(
			Response.json({ ...account(), profile: { ...account().profile, id: "second" } }),
		);
		await client.restore();
		resolve(Response.json({ code: "ok" }));
		await rejected;
		expect(client.profile.value?.id).toBe("second");
	});
	it("closes SSE and clears private state when the server revokes access", () => {
		class Events extends EventTarget {
			close = vi.fn();
		}
		const events = new Events();
		const lost = vi.fn();
		const client = createPlayerClient(vi.fn(), () => events as unknown as EventSource, {
			lost,
			check: async () => {},
		});
		client.connect();
		events.dispatchEvent(
			new MessageEvent("auth", { data: JSON.stringify({ code: "access_denied" }) }),
		);
		expect(events.close).toHaveBeenCalledOnce();
		expect(client.enabled.value).toBe(false);
		expect(client.snapshot.value).toBeNull();
		expect(client.uncertain.value).toBeNull();
		expect(lost).toHaveBeenCalledWith("access_denied");
	});
});
