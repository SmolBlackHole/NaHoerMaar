import { afterEach, expect, it, vi } from "vitest";
import { useSettingsStore } from "../app/stores/settings";
import { useProfileStore } from "../app/stores/profile";
import { repositoryFixture } from "./repository-fixture";
import type { Appearance } from "../shared/appearance";
import { defaultAppearance } from "../shared/appearance";

const cleanup: (() => void)[] = [];
afterEach(() => {
	cleanup.splice(0).forEach((dispose) => dispose());
	vi.useRealTimers();
});

async function setup() {
	vi.useFakeTimers();
	const request = vi.fn<typeof fetch>().mockImplementation(async () => Response.json({}));
	const fixture = repositoryFixture(request);
	const profile = useProfileStore(fixture.pinia);
	// A new server response binds settings via the real profile store.
	const restore = async (id: string | null, appearance: Appearance | null) => {
		if (!id) {
			profile.lost("signed_out");
			return;
		}
		fixture.repositories.account.session = async () => ({
			profile: { id, name: id, avatar: "0001" },
			appearance: appearance ?? { ...defaultAppearance },
			profile_complete: true,
			csrf_token: "csrf",
			expires_at: Date.now() / 1000 + 3600,
		});
		await profile.restore();
	};
	await restore("first", { ...defaultAppearance });
	const sync = useSettingsStore(fixture.pinia);
	return { settings: sync.settings, request, sync, restore };
}

it("coalesces quick edits and serializes changes made during a save", async () => {
	const { settings, request, sync, restore } = await setup();
	let resolve!: (response: Response) => void;
	request.mockReturnValueOnce(
		new Promise((done) => {
			resolve = done;
		}),
	);
	settings.mode = "light";
	settings.primaryColor = "amber";
	await vi.advanceTimersByTimeAsync(400);
	expect(request).toHaveBeenCalledTimes(1);
	expect(JSON.parse(request.mock.calls[0]![1]!.body as string).primaryColor).toBe("amber");
	settings.primaryColor = "rose";
	await restore("first", { ...defaultAppearance, primaryColor: "blue" });
	expect(settings.primaryColor).toBe("rose");
	await vi.advanceTimersByTimeAsync(400);
	expect(request).toHaveBeenCalledTimes(1);
	resolve(Response.json({}));
	await vi.advanceTimersByTimeAsync(0);
	expect(request).toHaveBeenCalledTimes(2);
	expect(JSON.parse(request.mock.lastCall![1]!.body as string).primaryColor).toBe("rose");
});

it("keeps failed changes locally and saves on explicit retry", async () => {
	const { settings, request, sync, restore } = await setup();
	request.mockRejectedValueOnce(new TypeError("offline"));
	settings.mode = "light";
	await vi.advanceTimersByTimeAsync(400);
	expect(sync.error).toContain("Couldn't save");
	await vi.advanceTimersByTimeAsync(5_000);
	expect(request).toHaveBeenCalledTimes(1);
	await sync.retry();
	expect(request).toHaveBeenCalledTimes(2);
	expect(sync.error).toBe("");
	expect(settings.mode).toBe("light");
});

it("discards pending edits on logout and ignores a previous account's late save", async () => {
	const { settings, request, sync, restore } = await setup();
	settings.mode = "light";
	await restore(null, null);
	await vi.advanceTimersByTimeAsync(400);
	expect(request).not.toHaveBeenCalled();
	await restore("first", { ...defaultAppearance });
	let resolve!: (response: Response) => void;
	request.mockReturnValueOnce(
		new Promise((done) => {
			resolve = done;
		}),
	);
	settings.primaryColor = "rose";
	await vi.advanceTimersByTimeAsync(400);
	const signal = request.mock.lastCall![1]!.signal!;
	await restore("second", { ...defaultAppearance, primaryColor: "amber" });
	expect(signal.aborted).toBe(true);
	resolve(Response.json({}, { status: 500 }));
	await vi.advanceTimersByTimeAsync(400);
	expect(settings.primaryColor).toBe("amber");
	expect(sync.error).toBe("");
	expect(request).toHaveBeenCalledTimes(1);
});
