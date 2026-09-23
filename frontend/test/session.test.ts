import { afterEach, describe, expect, it, vi } from "vitest";
import { useProfileStore } from "../app/stores/profile";
import { usePlayerStore } from "../app/stores/player";
import { SessionLost } from "../app/repositories/transport";
import { repositoryFixture } from "./repository-fixture";
import type { ListenerSession } from "../shared/session";
import { defaultAppearance } from "../shared/appearance";

const account = (): ListenerSession => ({
	appearance: { ...defaultAppearance },
	profile: { id: "first", name: "Alice", avatar: "0002" },
	profile_complete: true,
	is_admin: false,
	role: "user",
	discord_id: "243718053362270208",
	csrf_token: "session-bound-csrf",
	expires_at: Date.now() / 1000 + 60,
});
const cleanup: (() => void)[] = [];
afterEach(() => {
	cleanup.splice(0).forEach((close) => close());
	vi.unstubAllGlobals();
	vi.useRealTimers();
});

function setup() {
	const fetcher = vi.fn<typeof fetch>();
	const fixture = repositoryFixture(fetcher, undefined, {
		current: () => client.credentials,
		lost: (code) => client.lost(code),
	});
	const client = useProfileStore(fixture.pinia);
	cleanup.push(client.dispose);
	return { fetcher, client, request: fixture.json };
}

describe("server sessions", () => {
	it("keeps account state isolated between two Vue apps", async () => {
		const first = setup();
		first.fetcher.mockResolvedValueOnce(Response.json(account()));
		await first.client.restore();
		const second = setup();
		expect(second.client.profile).toBeNull();
		second.fetcher.mockResolvedValueOnce(
			Response.json({
				...account(),
				profile: { ...account().profile, id: "second", name: "Bob" },
			}),
		);
		await second.client.restore();
		expect(first.client.profile?.name).toBe("Alice");
		expect(second.client.profile?.name).toBe("Bob");
		first.client.lost("signed_out");
		expect(second.client.status).toBe("authenticated");
	});
	it("refreshes on cross-tab messages and focus without duplicating listeners", async () => {
		const window = new EventTarget();
		const addListener = vi.spyOn(window, "addEventListener");
		const removeListener = vi.spyOn(window, "removeEventListener");
		const channels: Channel[] = [];
		class Channel {
			onmessage: (() => void) | null = null;
			postMessage = vi.fn();
			close = vi.fn();
			constructor() {
				channels.push(this);
			}
		}
		vi.stubGlobal("window", window);
		vi.stubGlobal("BroadcastChannel", Channel);
		const { client, fetcher } = setup();
		fetcher.mockImplementation(async () => Response.json(account()));
		await client.restore();
		await client.restore();
		expect(channels).toHaveLength(1);
		expect(addListener).toHaveBeenCalledTimes(1);
		channels[0]!.onmessage?.();
		await client.restore();
		expect(fetcher).toHaveBeenCalledTimes(3);
		window.dispatchEvent(new Event("focus"));
		await client.restore();
		expect(fetcher).toHaveBeenCalledTimes(4);
		await client.save("Alice", "0002");
		expect(channels[0]!.postMessage).toHaveBeenCalledWith("profile");
		client.$dispose();
		expect(channels[0]!.close).toHaveBeenCalledOnce();
		expect(removeListener).toHaveBeenCalledWith("focus", expect.any(Function));
	});
	it("gets identity from the server and adds CSRF only to mutations", async () => {
		const { client, fetcher, request } = setup();
		expect(client.status).toBe("checking");
		fetcher.mockResolvedValueOnce(Response.json(account()));
		await client.restore();
		expect(client.profile?.name).toBe("Alice");
		fetcher.mockImplementation(async () => Response.json({}));
		await request("/api/session");
		expect(new Headers(fetcher.mock.lastCall?.[1]?.headers).has("X-CSRF-Token")).toBe(false);
		await request("/api/queue", { method: "POST", body: "{}" });
		expect(new Headers(fetcher.mock.lastCall?.[1]?.headers).get("X-CSRF-Token")).toBe(
			"session-bound-csrf",
		);
	});
	it.each([
		[401, "signed_out", "signed_out"],
		[403, "access_denied", "forbidden"],
		[503, "access_unavailable", "unavailable"],
	])("separates a %s response from a transport failure", async (status, code, expected) => {
		const { client, fetcher, request } = setup();
		fetcher.mockResolvedValue(Response.json({ code }, { status: Number(status) }));
		await client.restore();
		expect(client.status).toBe(expected);
		expect(client.profile).toBeNull();
		await expect(request("/api/session")).rejects.toBeInstanceOf(SessionLost);
	});
	it("keeps network failure separate from denial and permits retry", async () => {
		const { client, fetcher, request } = setup();
		fetcher.mockRejectedValueOnce(new TypeError("offline"));
		await client.restore();
		expect(client.status).toBe("unavailable");
		fetcher.mockResolvedValueOnce(Response.json(account()));
		await client.restore();
		expect(client.status).toBe("authenticated");
	});
	it("keeps a known session through a temporary outage but still accepts revoked access", async () => {
		const { client, fetcher } = setup();
		fetcher.mockResolvedValueOnce(Response.json(account()));
		await client.restore();
		const generation = client.generation;
		fetcher.mockRejectedValueOnce(new TypeError("offline"));
		await client.restore();
		expect(client.status).toBe("authenticated");
		expect(client.profile?.name).toBe("Alice");
		expect(client.credentials.csrfToken).toBe("session-bound-csrf");
		expect(client.generation).toBe(generation);
		fetcher.mockResolvedValueOnce(Response.json({ code: "access_denied" }, { status: 403 }));
		await client.restore();
		expect(client.status).toBe("forbidden");
		expect(client.profile).toBeNull();
	});
	it("expires the local session without waiting for a user action", async () => {
		vi.useFakeTimers();
		const { client, fetcher, request } = setup();
		fetcher.mockResolvedValueOnce(Response.json(account()));
		await client.restore();
		vi.advanceTimersByTime(60_001);
		expect(client.status).toBe("signed_out");
		expect(client.profile).toBeNull();
	});
	it("does not restore a session from a response arriving after logout", async () => {
		const { client, fetcher, request } = setup();
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
		expect(client.status).toBe("signed_out");
	});
	it("does not report successful logout when the server cannot be reached", async () => {
		const { client, fetcher, request } = setup();
		fetcher.mockResolvedValueOnce(Response.json(account()));
		await client.restore();
		fetcher.mockRejectedValueOnce(new TypeError("offline"));
		expect(await client.signOut()).toBe(false);
		expect(client.status).toBe("authenticated");
		expect(client.error).toContain("Couldn't sign out");
		fetcher.mockResolvedValueOnce(new Response(null, { status: 204 }));
		expect(await client.signOut()).toBe(true);
		expect(client.status).toBe("signed_out");
	});
	it("blocks a late mutation response from a previous account", async () => {
		const { client, fetcher, request } = setup();
		fetcher.mockResolvedValueOnce(Response.json(account()));
		await client.restore();
		let resolve!: (value: Response) => void;
		fetcher.mockReturnValueOnce(
			new Promise((done) => {
				resolve = done;
			}),
		);
		const pending = request("/api/queue", { method: "POST" });
		const rejected = expect(pending).rejects.toBeInstanceOf(SessionLost);
		client.lost("signed_out");
		fetcher.mockResolvedValueOnce(
			Response.json({ ...account(), profile: { ...account().profile, id: "second" } }),
		);
		await client.restore();
		resolve(Response.json({ code: "ok" }));
		await rejected;
		expect(client.profile?.id).toBe("second");
	});
	it("closes SSE and clears private state when the server revokes access", () => {
		class Events extends EventTarget {
			close = vi.fn();
		}
		const events = new Events();
		const fixture = repositoryFixture(vi.fn(), () => events as unknown as EventSource);
		const profile = useProfileStore(fixture.pinia);
		const lost = vi.spyOn(profile, "lost");
		const client = usePlayerStore(fixture.pinia);
		client.connect();
		events.dispatchEvent(
			new MessageEvent("auth", { data: JSON.stringify({ code: "access_denied" }) }),
		);
		expect(events.close).toHaveBeenCalledOnce();
		expect(client.enabled).toBe(false);
		expect(client.snapshot).toBeNull();
		expect(client.uncertain).toBeNull();
		expect(lost).toHaveBeenCalledWith("access_denied");
	});
});
