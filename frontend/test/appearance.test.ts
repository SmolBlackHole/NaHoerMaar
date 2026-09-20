import { reactive } from "vue";
import { afterEach, expect, it, vi } from "vitest";
import { createAppearanceSync } from "../app/auth/appearance";
import { defaultAppearance } from "../shared/appearance";

const cleanup: (() => void)[] = [];
afterEach(() => {
	cleanup.splice(0).forEach((dispose) => dispose());
	vi.useRealTimers();
});

function setup() {
	vi.useFakeTimers();
	const settings = reactive({ ...defaultAppearance });
	const request = vi.fn<typeof fetch>().mockResolvedValue(Response.json({}));
	const sync = createAppearanceSync(settings, request);
	cleanup.push(sync.dispose);
	sync.bind("first", { ...defaultAppearance });
	return { settings, request, sync };
}

it("coalesces quick edits and serializes changes made during a save", async () => {
	const { settings, request, sync } = setup();
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
	sync.bind("first", { ...defaultAppearance, primaryColor: "blue" });
	expect(settings.primaryColor).toBe("rose");
	await vi.advanceTimersByTimeAsync(400);
	expect(request).toHaveBeenCalledTimes(1);
	resolve(Response.json({}));
	await vi.advanceTimersByTimeAsync(0);
	expect(request).toHaveBeenCalledTimes(2);
	expect(JSON.parse(request.mock.lastCall![1]!.body as string).primaryColor).toBe("rose");
});

it("keeps failed changes locally and saves on explicit retry", async () => {
	const { settings, request, sync } = setup();
	request.mockRejectedValueOnce(new TypeError("offline"));
	settings.mode = "light";
	await vi.advanceTimersByTimeAsync(400);
	expect(sync.error.value).toContain("Couldn't save");
	await vi.advanceTimersByTimeAsync(5_000);
	expect(request).toHaveBeenCalledTimes(1);
	await sync.retry();
	expect(request).toHaveBeenCalledTimes(2);
	expect(sync.error.value).toBe("");
	expect(settings.mode).toBe("light");
});

it("discards pending edits on logout and ignores a previous account's late save", async () => {
	const { settings, request, sync } = setup();
	settings.mode = "light";
	sync.bind(null, null);
	await vi.advanceTimersByTimeAsync(400);
	expect(request).not.toHaveBeenCalled();
	sync.bind("first", { ...defaultAppearance });
	let resolve!: (response: Response) => void;
	request.mockReturnValueOnce(
		new Promise((done) => {
			resolve = done;
		}),
	);
	settings.primaryColor = "rose";
	await vi.advanceTimersByTimeAsync(400);
	const signal = request.mock.lastCall![1]!.signal!;
	sync.bind("second", { ...defaultAppearance, primaryColor: "amber" });
	expect(signal.aborted).toBe(true);
	resolve(Response.json({}, { status: 500 }));
	await vi.advanceTimersByTimeAsync(400);
	expect(settings.primaryColor).toBe("amber");
	expect(sync.error.value).toBe("");
	expect(request).toHaveBeenCalledTimes(1);
});
