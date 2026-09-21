import { effectScope, nextTick, reactive, ref } from "vue";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PendingRequest, PlayerActivity } from "../app/stores/player";
import { usePlayerNotifications } from "../app/composables/usePlayerNotifications";
import type { MutationResult, PlayerState, QueueEntry } from "../shared/player";
import type { Toast } from "@nuxt/ui/composables/useToast";

const getPlayer = vi.hoisted(() => vi.fn());
vi.mock("../app/stores/player", () => ({ usePlayerStore: getPlayer }));

const track: QueueEntry = {
	track_id: "track",
	reference: null,
	id: "entry",
	source_url: "https://youtu.be/Pqp9fDRp1lw",
	video_id: "Pqp9fDRp1lw",
	title: "A track",
	uploader: null,
	duration_seconds: 100,
	thumbnail_url: null,
	artist: null,
	uploader_url: null,
	added_by: null,
	origin: "manual",
};
const state: PlayerState = {
	session_id: "session",
	attempt_id: null,
	revision: 1,
	queue_revision: 1,
	state: "idle",
	current: null,
	upcoming: [track],
	recently_played: [],
	voice_state: "connected",
	channel_id: "123",
	playback_id: null,
	volume: 1,
	crossfade_seconds: 0,
	position_seconds: 0,
	position_updated_at: null,
	last_issue: null,
	radio: {
		state: "off",
		generation: null,
		title: null,
		seed: null,
		initiator: null,
		error: null,
	},
};
const scopes: ReturnType<typeof effectScope>[] = [];
afterEach(() => {
	scopes.splice(0).forEach((scope) => scope.stop());
	vi.unstubAllGlobals();
	vi.useRealTimers();
});

function setup() {
	const player = reactive({
		activity: null as PlayerActivity | null,
		uncertain: null as PendingRequest | null,
		error: null as string | null,
		connection: "live" as "live" | "connecting" | "offline",
		pending: false,
		enabled: true,
		retry: vi.fn(),
		undo: vi.fn(),
		dispose: () => {},
		snapshot: { ...state } as PlayerState | null,
		addMany: vi.fn().mockResolvedValue(true),
	});
	player.dispose = () => {
		player.snapshot = null;
		player.uncertain = null;
		player.error = null;
	};
	player.snapshot = { ...state };
	player.connection = "live";
	getPlayer.mockReturnValue(player);
	const toasts = ref<Toast[]>([]);
	const toast = {
		toasts,
		add: vi.fn((value: Partial<Toast>) => {
			const item = { id: crypto.randomUUID(), open: true, ...value } as Toast;
			toasts.value.push(item);
			return item;
		}),
		update: vi.fn((id: string, value: Partial<Toast>) => {
			const item = toasts.value.find((item) => item.id === id);
			if (item) Object.assign(item, value, { open: true });
		}),
		remove: vi.fn((id: string) => {
			const item = toasts.value.find((item) => item.id === id);
			if (item) item.open = false;
		}),
	};
	vi.stubGlobal("useToast", () => toast);
	const scope = effectScope();
	scopes.push(scope);
	scope.run(usePlayerNotifications);
	return { player, toast };
}

describe("player notifications", () => {
	it("does not confuse a seek with a queue reorder", async () => {
		const { player, toast } = setup();
		const result: MutationResult = {
			code: "ok",
			replayed: false,
			added_count: 0,
			removed_count: 0,
			restored_count: 0,
			skipped_count: 0,
			entries: [],
			actor: null,
			undo_id: null,
			undo_expires_at: null,
		};
		player.activity = { id: "seek", action: "playback.seek", result, own: true };
		await nextTick();
		expect(toast.add).not.toHaveBeenCalled();
		player.activity = { id: "move", action: "queue.reordered", result, own: true };
		await nextTick();
		expect(toast.toasts.value[0]?.title).toBe("Queue order updated");
	});
	it("announces remote edits once, without duplicating own HTTP notices or radio refills", async () => {
		const { player, toast } = setup();
		const result: MutationResult = {
			code: "ok",
			replayed: false,
			added_count: 1,
			removed_count: 0,
			restored_count: 0,
			skipped_count: 0,
			entries: [track],
			actor: { id: "kai", name: "Kai", avatar: "0001" },
			undo_id: null,
			undo_expires_at: null,
		};
		player.activity = { id: "session:2", action: "queue.added", result, own: false };
		await nextTick();
		expect(toast.toasts.value[0]?.title).toBe("Kai added a track");
		expect(toast.toasts.value[0]?.description).toBe("A track");
		player.activity = { ...player.activity };
		await nextTick();
		player.activity = { id: "session:2", action: "queue.added", result, own: true };
		await nextTick();
		player.activity = {
			id: "session:4",
			action: "session.updated",
			result: { ...result, actor: null },
			own: false,
		};
		await nextTick();
		expect(toast.add).toHaveBeenCalledOnce();
	});
	it("announces each playback issue once across new snapshots and reconnects", async () => {
		const { player, toast } = setup();
		const issue: NonNullable<PlayerState["last_issue"]> = {
			entry_id: track.id,
			id: "failure-1",
			entry: { ...track, title: "You’re here that’s the thing" },
			reason: "source_unavailable",
		};
		player.snapshot = { ...state, last_issue: issue };
		await nextTick();
		expect(toast.add).toHaveBeenCalledOnce();
		expect(toast.toasts.value[0]?.title).toBe("Track skipped");
		expect(toast.toasts.value[0]?.description).toBe(
			"You’re here that’s the thing\nThe audio source couldn't be opened.",
		);
		await toast.toasts.value[0]?.actions?.[0]?.onClick?.(new Event("click") as MouseEvent);
		expect(player.addMany).toHaveBeenCalledWith([track.track_id]);
		player.connection = "connecting";
		await nextTick();
		player.snapshot = { ...state, revision: 2, last_issue: { ...issue } };
		player.connection = "live";
		await nextTick();
		expect(toast.add).toHaveBeenCalledOnce();
		player.snapshot = { ...state, last_issue: { ...issue, id: "failure-2" } };
		await nextTick();
		expect(toast.add).toHaveBeenCalledTimes(2);
	});

	it("expires undo against its server deadline and does not recreate a replayed removal", async () => {
		vi.useFakeTimers();
		const { player, toast } = setup();
		const result: MutationResult = {
			code: "ok",
			replayed: false,
			added_count: 0,
			skipped_count: 0,
			removed_count: 1,
			restored_count: 0,
			undo_id: "undo",
			undo_expires_at: new Date(Date.now() + 12000).toISOString(),
			actor: { id: "remover", name: "Kai", avatar: "0002" },
			entries: [{ ...track, added_by: { id: "author", name: "Andrey", avatar: "0001" } }],
		};
		player.activity = {
			id: "remove",
			action: "queue.removed",
			own: true,
			result,
		};
		await nextTick();
		expect(toast.toasts.value[0]?.actions?.[0]?.label).toBe("Undo");
		expect(toast.toasts.value[0]?.title).toBe("Kai removed a track");
		expect(toast.toasts.value[0]?.description).toBe("A track");
		player.activity = { ...player.activity, result: { ...result, replayed: true } };
		await nextTick();
		expect(toast.add).toHaveBeenCalledOnce();
		await vi.advanceTimersByTimeAsync(11999);
		expect(toast.toasts.value[0]?.open).toBe(true);
		await vi.advanceTimersByTimeAsync(2);
		expect(toast.toasts.value[0]?.open).toBe(false);
	});

	it("does not reopen dismissed recovery toasts when pending or connection changes", async () => {
		const { player, toast } = setup();
		player.uncertain = { id: "lost", action: "queue.added", execute: vi.fn() };
		player.error = "Response lost";
		await nextTick();
		const notification = toast.toasts.value[0]!;
		expect(notification.actions?.[0]?.label).toBe("Check result");
		notification.open = false;
		player.connection = "connecting";
		await nextTick();
		player.connection = "live";
		await nextTick();
		expect(notification.open).toBe(false);
		expect(player.uncertain?.id).toBe("lost");
	});

	it("retires undo when a lost restore response is recovered with Check result", async () => {
		vi.useFakeTimers();
		const { player, toast } = setup();
		const result: MutationResult = {
			code: "ok",
			replayed: false,
			added_count: 0,
			skipped_count: 0,
			removed_count: 1,
			restored_count: 0,
			undo_id: "undo",
			undo_expires_at: new Date(Date.now() + 12000).toISOString(),
			actor: { id: "remover", name: "Kai", avatar: "0002" },
			entries: [track],
		};
		player.activity = {
			id: "remove",
			action: "queue.removed",
			own: true,
			result,
		};
		await nextTick();
		const undoToast = toast.toasts.value[0]!;
		player.activity = {
			id: "restore",
			action: "queue.restored",
			own: true,
			target: "undo",
			result: {
				...result,
				replayed: true,
				undo_id: null,
				undo_expires_at: null,
				removed_count: 0,
				restored_count: 1,
			},
		};
		await nextTick();
		expect(undoToast.open).toBe(false);
		expect(toast.toasts.value[1]?.title).toBe("Kai restored a track");
		expect(toast.toasts.value[1]?.description).toBe("A track");
	});

	it("reports actual import counts and clears recovery actions at sign-out", async () => {
		const { player, toast } = setup();
		player.activity = {
			id: "batch",
			action: "queue.added",
			own: true,
			result: {
				code: "ok",
				replayed: false,
				added_count: 2,
				skipped_count: 3,
				removed_count: 0,
				restored_count: 0,
				undo_id: null,
				undo_expires_at: null,
				actor: { id: "author", name: "Andrey", avatar: "0001" },
				entries: [track, { ...track, id: "second", title: "Another track" }],
			},
		};
		await nextTick();
		expect(toast.toasts.value[0]?.title).toBe("Andrey added 2 tracks");
		expect(toast.toasts.value[0]?.description).toBe(
			"A track · Another track · 3 duplicates skipped",
		);
		player.uncertain = { id: "lost", action: "queue.added", execute: vi.fn() };
		player.error = "Response lost";
		await nextTick();
		player.dispose();
		await nextTick();
		expect(toast.toasts.value.filter((item) => item.open && item.actions?.length)).toHaveLength(
			0,
		);
	});

	it("reports missing titles without showing source IDs", async () => {
		const { player, toast } = setup();
		const unknown = { ...track, title: null };
		player.snapshot = { ...state, upcoming: [unknown] };
		const result: MutationResult = {
			code: "ok",
			replayed: false,
			added_count: 1,
			skipped_count: 0,
			removed_count: 0,
			restored_count: 0,
			undo_id: null,
			undo_expires_at: null,
			actor: { id: "author", name: "Andrey", avatar: "0001" },
			entries: [unknown],
		};
		player.activity = { id: "add", action: "queue.added", own: true, result };
		await nextTick();
		expect(toast.toasts.value[0]?.title).toBe("Andrey added a track");
		expect(toast.toasts.value[0]?.description).toBe("Title unavailable");
		player.snapshot = { ...state, revision: 2 };
		await nextTick();
		expect(toast.add).toHaveBeenCalledOnce();
		expect(toast.toasts.value[0]?.description).toBe("Title unavailable");
		player.snapshot = { ...state, upcoming: [unknown] };
		player.activity = {
			id: "second",
			action: "queue.added",
			own: true,
			result,
		};
		await nextTick();
		toast.toasts.value[1]!.open = false;
		player.snapshot = { ...state, revision: 3 };
		await nextTick();
		expect(toast.toasts.value[1]?.open).toBe(false);
		expect(toast.add).toHaveBeenCalledTimes(2);
	});
});
