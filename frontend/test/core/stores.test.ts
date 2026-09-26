// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import { createPinia, disposePinia, setActivePinia, type Pinia } from "pinia";
import { nextTick } from "vue";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createBackendCore } from "../../app/core/bootstrap";
import { defaultAppearance, type UserProfile } from "../../app/core/models/account";
import type { MutationResult, QueueEntry } from "../../app/core/models/player";
import { FakeEvents, player } from "./fixture";

const piniaInstances: Pinia[] = [];
afterEach(() => piniaInstances.splice(0).forEach(disposePinia));

function playerFixture() {
	const pinia = createPinia();
	setActivePinia(pinia);
	piniaInstances.push(pinia);
	const events = new FakeEvents();
	const fetcher = vi.fn<typeof fetch>(async (input) => {
		switch (String(input)) {
			case "/api/auth/session":
				return Response.json({
					csrf: "session-token",
					discord_id: "discord-user",
					expires_at: "2026-09-26T00:00:00Z",
					profile_complete: true,
					role: "owner",
					user_id: "user-id",
				});
			case "/api/player/voice/channels":
				return Response.json([]);
			default:
				throw new Error(`Unexpected request: ${String(input)}`);
		}
	});
	const core = createBackendCore({ fetch: fetcher, openEvents: () => events });
	const session = core.stores.useSessionStore();
	const playerStore = core.stores.usePlayerStore();
	return { core, events, fetcher, session, playerStore };
}

async function authenticate(fixture: ReturnType<typeof playerFixture>): Promise<void> {
	expect(await fixture.session.restore()).toBe(true);
	await nextTick();
	expect(fixture.session.status).toBe("authenticated");
	expect(fixture.events.close).not.toHaveBeenCalled();
}

function changeEvent(operationId: string, current = player(11)) {
	return {
		message_id: "message-id",
		correlation_id: "correlation-id",
		causation_id: "cause-id",
		operation_id: operationId,
		action: "skip",
		state: current,
		outcome: {
			action: "skip",
			added_count: 0,
			removed_count: 1,
			restored_count: 0,
			skipped_count: 1,
			entry_ids: [],
			undo_id: null,
			undo_expires_at: null,
		},
	};
}

function mutationResult(current = player(11), action = "skip"): MutationResult {
	return {
		player: current,
		replayed: false,
		outcome: {
			action,
			added_count: 0,
			removed_count: 1,
			restored_count: 0,
			skipped_count: 1,
			entry_ids: [],
			undo_id: null,
			undo_expires_at: null,
		},
	};
}

function queueEntry(id: string, position: number, title: string): QueueEntry {
	return {
		id,
		position,
		request: {
			id: `request-${id}`,
			contributor: {
				user_id: "user-id",
				display_name: "Listener",
				pixabot: "12ab",
				discord_id: "discord-user-id",
				discord_username: "listener",
				discord_avatar_hash: null,
			},
			origin: "manual",
			radio_run_id: null,
			requested_at: "2026-09-26T00:00:00Z",
			requested_by: "user-id",
			source_id: `source-${id}`,
			track: {
				id: `track-${id}`,
				title,
				album_title: null,
				artwork_url: null,
				duration_seconds: 180,
				isrc: null,
				release_date: null,
				artists: [],
				sources: [
					{
						id: `source-${id}`,
						provider: "youtube_music",
						external_id: id,
						source_url: `https://music.youtube.com/watch?v=${id}`,
						availability: "available",
						checked_at: "2026-09-26T00:00:00Z",
					},
				],
			},
		},
	};
}

function userProfile(userId: string): UserProfile {
	return {
		id: userId,
		role: "user",
		created_at: "2026-09-26T00:00:00Z",
		updated_at: "2026-09-26T00:00:00Z",
		last_login_at: null,
		discord: {
			id: `discord-${userId}`,
			username: userId,
			display_name: userId,
			avatar_hash: null,
			avatar_url: null,
			synced_at: "2026-09-26T00:00:00Z",
		},
		profile: { complete: true, display_name: userId, pixabot: null },
		appearance: { ...defaultAppearance },
		recent_tracks: [],
		statistics: {
			user_id: userId,
			coverage: {
				period: "30d",
				started_at: "2026-08-28T00:00:00Z",
				ended_at: "2026-09-26T00:00:00Z",
				recorded_since: null,
				timezone: "Europe/Berlin",
				partial: false,
			},
			totals: {
				average_wait_seconds: null,
				completed: 0,
				completion_rate: null,
				failed: 0,
				listening_seconds: 0,
				manual_requests: 0,
				plays: 0,
				radio_requests: 0,
				requests: 0,
				skip_rate: null,
				skipped: 0,
				stopped: 0,
				unique_artists: 0,
				unique_tracks: 0,
			},
			daily_activity: [],
			top_artists: [],
			top_listeners: [],
			top_tracks: [],
		},
	};
}

describe("new backend Pinia stores", () => {
	it("restores auth, resynchronizes from an SSE snapshot, and clears player state on logout", async () => {
		const fixture = playerFixture();
		await authenticate(fixture);
		fixture.events.send("state", player(90));
		await nextTick();
		expect(fixture.playerStore.connection).toBe("live");
		expect(fixture.playerStore.state?.revision).toBe(90);

		fixture.events.readyState = 0;
		fixture.events.dispatchEvent(new Event("error"));
		await nextTick();
		expect(fixture.playerStore.connection).toBe("reconnecting");
		fixture.events.readyState = 1;
		fixture.events.dispatchEvent(new Event("open"));
		fixture.events.send("state", player(1, "reconnected-session"));
		await nextTick();
		expect(fixture.playerStore.state).toMatchObject({
			session_id: "reconnected-session",
			revision: 1,
		});

		fixture.core.authority.lost("signed_out");
		await nextTick();
		expect(fixture.playerStore.state).toBeNull();
		expect(fixture.playerStore.connection).toBe("closed");
		expect(fixture.events.close).toHaveBeenCalledOnce();
	});

	it("settles an operation once when its SSE change arrives before the HTTP response", async () => {
		const fixture = playerFixture();
		await authenticate(fixture);
		fixture.events.send("state", player(10));
		await nextTick();

		let finishHttp!: (result: MutationResult) => void;
		const httpResult = new Promise<MutationResult>((resolve) => (finishHttp = resolve));
		const execute = vi.fn((_operationId: string) => httpResult);
		const pending = fixture.playerStore.run("skip", execute);
		const operationId = execute.mock.calls[0]![0];
		expect(fixture.playerStore.pendingOperationIds).toEqual([operationId]);

		fixture.events.send("change", changeEvent(operationId));
		const eventResult = fixture.playerStore.lastOperation;
		expect(eventResult).toMatchObject({
			operationId,
			action: "skip",
			own: true,
			source: "sse",
		});
		expect(fixture.playerStore.pendingOperationIds).toEqual([]);

		finishHttp(mutationResult());
		await pending;
		expect(fixture.playerStore.lastOperation).toBe(eventResult);
		expect(fixture.playerStore.pendingOperationIds).toEqual([]);
	});

	it("keeps a newer shared queue when an older command response arrives later", async () => {
		const fixture = playerFixture();
		await authenticate(fixture);
		fixture.events.send("state", player(10));
		await nextTick();

		let finishHttp!: (result: MutationResult) => void;
		const httpResult = new Promise<MutationResult>((resolve) => (finishHttp = resolve));
		const pending = fixture.playerStore.run("queue.add", () => httpResult);
		const mine = queueEntry("mine", 0, "Mine");
		const theirs = queueEntry("theirs", 1, "Theirs");
		fixture.events.send(
			"change",
			changeEvent("another-operation", {
				...player(12),
				queue_revision: 2,
				queue: [mine, theirs],
			}),
		);

		finishHttp(
			mutationResult({
				...player(11),
				queue_revision: 1,
				queue: [mine],
			}),
		);
		await pending;

		expect(fixture.playerStore.state?.revision).toBe(12);
		expect(fixture.playerStore.state?.queue.map(({ id }) => id)).toEqual(["mine", "theirs"]);
	});

	it("does not automatically retry an uncertain mutation and reuses its operation ID on explicit retry", async () => {
		const fixture = playerFixture();
		await authenticate(fixture);
		fixture.events.send("state", player());
		await nextTick();

		const execute = vi
			.fn<(operationId: string) => Promise<MutationResult>>()
			.mockRejectedValueOnce(new TypeError("connection dropped"))
			.mockResolvedValueOnce(mutationResult(player(2), "pause"));
		await fixture.playerStore.run("pause", execute);
		const operationId = execute.mock.calls[0]![0];
		expect(execute).toHaveBeenCalledOnce();
		expect(fixture.playerStore.uncertainOperation).toEqual({ operationId, action: "pause" });

		await fixture.playerStore.retryUncertain();
		expect(execute.mock.calls.map(([id]) => id)).toEqual([operationId, operationId]);
		expect(fixture.playerStore.uncertainOperation).toBeNull();
		expect(fixture.playerStore.lastOperation).toMatchObject({
			operationId,
			action: "pause",
			source: "http",
			own: true,
		});
	});

	it("keeps queue order authoritative and sends the observed revision when moving an entry", async () => {
		const fixture = playerFixture();
		await authenticate(fixture);
		const first = queueEntry("queue-a", 0, "First");
		const second = queueEntry("queue-b", 1, "Second");
		fixture.events.send("state", {
			...player(20),
			queue_revision: 7,
			queue: [first, second],
		});
		await nextTick();

		let finishHttp!: (response: Response) => void;
		const response = new Promise<Response>((resolve) => (finishHttp = resolve));
		fixture.fetcher.mockClear();
		fixture.fetcher.mockImplementation(async (input) => {
			if (String(input) === "/api/player/queue/queue-b") return response;
			throw new Error(`Unexpected request: ${String(input)}`);
		});

		const pending = fixture.playerStore.move("queue-b", "queue-a");
		expect(fixture.playerStore.state?.queue.map(({ id }) => id)).toEqual([
			"queue-a",
			"queue-b",
		]);
		const [, init] = fixture.fetcher.mock.calls[0]!;
		expect(init?.method).toBe("PUT");
		expect(JSON.parse(String(init?.body))).toMatchObject({
			expected_queue_revision: 7,
			before_entry_id: "queue-a",
		});

		finishHttp(
			Response.json(
				mutationResult(
					{
						...player(21),
						queue_revision: 8,
						queue: [
							{ ...second, position: 0 },
							{ ...first, position: 1 },
						],
					},
					"queue.move",
				),
			),
		);
		await pending;
		expect(fixture.playerStore.state?.queue.map(({ id }) => id)).toEqual([
			"queue-b",
			"queue-a",
		]);
	});

	it("clears queue entries for the selected requester", async () => {
		const fixture = playerFixture();
		await authenticate(fixture);
		fixture.events.send("state", {
			...player(20),
			queue_revision: 7,
			queue: [queueEntry("queue-a", 0, "First")],
		});
		await nextTick();

		fixture.fetcher.mockClear();
		fixture.fetcher.mockImplementation(async (input) => {
			if (String(input) === "/api/player/queue/clear")
				return Response.json(
					mutationResult({ ...player(21), queue_revision: 8, queue: [] }, "queue.clear"),
				);
			throw new Error(`Unexpected request: ${String(input)}`);
		});

		await fixture.playerStore.clear("other-user");
		const [, init] = fixture.fetcher.mock.calls[0]!;
		expect(JSON.parse(String(init?.body))).toMatchObject({
			expected_queue_revision: 7,
			requested_by: "other-user",
		});
	});

	it("reloads the shared profile for a new account and clears it when authority is lost", async () => {
		const pinia = createPinia();
		setActivePinia(pinia);
		piniaInstances.push(pinia);
		let currentUser = "user-one";
		const fetcher = vi.fn<typeof fetch>(async (input) => {
			if (String(input) === "/api/auth/session")
				return Response.json({
					csrf: `csrf-${currentUser}`,
					discord_id: `discord-${currentUser}`,
					expires_at: "2026-09-27T00:00:00Z",
					profile_complete: true,
					role: "user",
					user_id: currentUser,
				});
			if (String(input) === "/api/users/me?period=30d")
				return Response.json(userProfile(currentUser));
			throw new Error(`Unexpected request: ${String(input)}`);
		});
		const core = createBackendCore({ fetch: fetcher });
		const session = core.stores.useSessionStore();
		const profile = core.stores.useProfileStore();

		expect(await session.restore()).toBe(true);
		await vi.waitFor(() => expect(profile.profile?.id).toBe("user-one"));
		currentUser = "user-two";
		expect(await session.refresh()).toBe(true);
		await vi.waitFor(() => expect(profile.profile?.id).toBe("user-two"));

		core.authority.lost("signed_out");
		await nextTick();
		expect(profile.profile).toBeNull();
	});

	it("refreshes the current session after profile setup changes completeness", async () => {
		const pinia = createPinia();
		setActivePinia(pinia);
		piniaInstances.push(pinia);
		const fetcher = vi.fn<typeof fetch>();
		fetcher
			.mockResolvedValueOnce(
				Response.json({
					csrf: "session-token",
					discord_id: "discord-user",
					expires_at: "2026-09-26T00:00:00Z",
					profile_complete: false,
					role: "owner",
					user_id: "user-id",
				}),
			)
			.mockResolvedValueOnce(
				Response.json({
					csrf: "session-token",
					discord_id: "discord-user",
					expires_at: "2026-09-26T00:00:00Z",
					profile_complete: true,
					role: "owner",
					user_id: "user-id",
				}),
			);
		const core = createBackendCore({ fetch: fetcher });
		const session = core.stores.useSessionStore();

		expect(await session.restore()).toBe(true);
		expect(session.account?.profile_complete).toBe(false);
		expect(await session.refresh()).toBe(true);
		expect(session.account?.profile_complete).toBe(true);
		expect(fetcher).toHaveBeenCalledTimes(2);
	});
});
