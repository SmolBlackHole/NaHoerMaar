import { effectScope, nextTick, reactive, ref } from "vue";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createPlayerClient } from "../app/player/client";
import { usePlayerNotifications } from "../app/composables/usePlayerNotifications";
import type { MutationResult, PlayerState, QueueEntry } from "../shared/player";
import type { Toast } from "@nuxt/ui/composables/useToast";

const getPlayer = vi.hoisted(() => vi.fn());
vi.mock("../app/stores/player", () => ({ usePlayerStore: getPlayer }));

const track: QueueEntry = {
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
		session_id: null,
		seed: null,
		initiator: null,
		error: null,
		event_id: null,
		action: null,
		actor: null,
	},
};
const scopes: ReturnType<typeof effectScope>[] = [];
afterEach(() => {
	scopes.splice(0).forEach((scope) => scope.stop());
	vi.unstubAllGlobals();
	vi.useRealTimers();
});

function setup() {
	const player = reactive({ ...createPlayerClient(), add: vi.fn().mockResolvedValue(true) });
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
	it("announces each playback issue once across new snapshots and reconnects", async () => {
		const { player, toast } = setup();
		const issue: NonNullable<PlayerState["last_issue"]> = {
			id: "failure-1",
			entry_id: track.id,
			entry: { ...track, title: "You’re here that’s the thing" },
			fatal: false,
			code: "playback_failed",
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
		expect(player.add).toHaveBeenCalledWith(track.source_url);
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
			request_id: "remove",
			code: "ok",
			entry_id: null,
			replayed: false,
			snapshot: state,
			added_count: 0,
			skipped_count: 0,
			removed_count: 1,
			restored_count: 0,
			undo_id: "undo",
			undo_expires_at: new Date(Date.now() + 12000).toISOString(),
			actor: { id: "remover", name: "Kai", avatar: "0002" },
			entries: [{ ...track, added_by: { id: "author", name: "Andrey", avatar: "0001" } }],
		};
		player.completed = {
			request: { id: "remove", path: "/api/queue/entry", method: "DELETE" },
			result,
		};
		await nextTick();
		expect(toast.toasts.value[0]?.actions?.[0]?.label).toBe("Undo");
		expect(toast.toasts.value[0]?.title).toBe("Kai removed a track");
		expect(toast.toasts.value[0]?.description).toBe("A track");
		player.completed = { ...player.completed, result: { ...result, replayed: true } };
		await nextTick();
		expect(toast.add).toHaveBeenCalledOnce();
		await vi.advanceTimersByTimeAsync(11999);
		expect(toast.toasts.value[0]?.open).toBe(true);
		await vi.advanceTimersByTimeAsync(2);
		expect(toast.toasts.value[0]?.open).toBe(false);
	});

	it("does not reopen dismissed recovery toasts when pending or connection changes", async () => {
		const { player, toast } = setup();
		player.uncertain = { id: "lost", path: "/api/queue", method: "POST" };
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
			request_id: "remove",
			code: "ok",
			entry_id: null,
			replayed: false,
			snapshot: state,
			added_count: 0,
			skipped_count: 0,
			removed_count: 1,
			restored_count: 0,
			undo_id: "undo",
			undo_expires_at: new Date(Date.now() + 12000).toISOString(),
			actor: { id: "remover", name: "Kai", avatar: "0002" },
			entries: [track],
		};
		player.completed = {
			request: { id: "remove", path: "/api/queue/entry", method: "DELETE" },
			result,
		};
		await nextTick();
		const undoToast = toast.toasts.value[0]!;
		player.completed = {
			request: {
				id: "restore",
				path: "/api/queue/undo",
				method: "POST",
				body: JSON.stringify({ undo_id: "undo" }),
			},
			result: {
				...result,
				request_id: "restore",
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
		player.completed = {
			request: { id: "batch", path: "/api/queue/batch", method: "POST" },
			result: {
				request_id: "batch",
				code: "ok",
				entry_id: null,
				replayed: false,
				snapshot: state,
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
		player.uncertain = { id: "lost", path: "/api/queue", method: "POST" };
		player.error = "Response lost";
		await nextTick();
		player.dispose();
		await nextTick();
		expect(toast.toasts.value.filter((item) => item.open && item.actions?.length)).toHaveLength(
			0,
		);
	});

	it("replaces a pending title without showing video IDs or reopening dismissed toasts", async () => {
		const { player, toast } = setup();
		const unknown = { ...track, title: null };
		player.snapshot = { ...state, upcoming: [unknown] };
		const result: MutationResult = {
			request_id: "add",
			code: "ok",
			entry_id: track.id,
			replayed: false,
			snapshot: player.snapshot,
			added_count: 1,
			skipped_count: 0,
			removed_count: 0,
			restored_count: 0,
			undo_id: null,
			undo_expires_at: null,
			actor: { id: "author", name: "Andrey", avatar: "0001" },
			entries: [unknown],
		};
		player.completed = { request: { id: "add", path: "/api/queue", method: "POST" }, result };
		await nextTick();
		expect(toast.toasts.value[0]?.title).toBe("Andrey added a track");
		expect(toast.toasts.value[0]?.description).toBe("Title is loading…");
		player.snapshot = { ...state, revision: 2 };
		await nextTick();
		expect(toast.add).toHaveBeenCalledOnce();
		expect(toast.toasts.value[0]?.description).toBe("A track");
		player.snapshot = { ...state, upcoming: [unknown] };
		player.completed = {
			request: { id: "second", path: "/api/queue", method: "POST" },
			result: { ...result, request_id: "second" },
		};
		await nextTick();
		toast.toasts.value[1]!.open = false;
		player.snapshot = { ...state, revision: 3 };
		await nextTick();
		expect(toast.toasts.value[1]?.open).toBe(false);
		expect(toast.add).toHaveBeenCalledTimes(2);
	});
});
