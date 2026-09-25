// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import { createPinia, disposePinia, setActivePinia, type Pinia } from "pinia";
import { nextTick } from "vue";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createBackendCore } from "../../app/core/bootstrap";
import type { MutationResult } from "../../app/core/models/player";
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
