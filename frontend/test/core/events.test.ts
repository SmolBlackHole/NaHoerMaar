// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import { describe, expect, it, vi } from "vitest";
import { fixture, player } from "./fixture";

describe("new backend player SSE", () => {
	it("accepts a fresh snapshot after reconnect, even with a lower revision", () => {
		const { client, events, openEvents } = fixture();
		const receive = vi.fn();
		const close = client.player.subscribe(receive);
		expect(openEvents).toHaveBeenCalledWith("/api/events");
		events.dispatchEvent(new Event("open"));
		expect(receive).toHaveBeenLastCalledWith({ type: "connection", status: "connecting" });
		events.send("state", player(90));
		events.readyState = 0;
		events.dispatchEvent(new Event("error"));
		expect(receive).toHaveBeenLastCalledWith({ type: "connection", status: "reconnecting" });
		events.readyState = 1;
		events.dispatchEvent(new Event("open"));
		events.send("state", player(1, "new-session"));
		expect(receive).toHaveBeenLastCalledWith({
			type: "state",
			state: player(1, "new-session"),
		});
		expect(openEvents).toHaveBeenCalledOnce();
		close();
	});

	it("keeps command causation and operation IDs in change events", () => {
		const { client, events } = fixture();
		const receive = vi.fn();
		const close = client.player.subscribe(receive);
		const change = {
			message_id: "message",
			correlation_id: "correlation",
			causation_id: "cause",
			operation_id: "operation",
			action: "queue_added",
			state: player(2),
			outcome: {
				action: "queue_added",
				added_count: 1,
				removed_count: 0,
				restored_count: 0,
				skipped_count: 0,
				entry_ids: ["entry"],
				undo_id: null,
				undo_expires_at: null,
			},
		};
		events.send("change", change);
		expect(receive).toHaveBeenLastCalledWith({ type: "change", change });
		close();
	});

	it("closes on auth.error and rejects late events", () => {
		const { client, events, auth } = fixture();
		const receive = vi.fn();
		client.player.subscribe(receive);
		events.send("auth", { error: "access_denied" });
		expect(events.close).toHaveBeenCalledOnce();
		expect(auth.lost).toHaveBeenCalledWith("access_denied");
		expect(receive).toHaveBeenCalledWith({ type: "auth", error: "access_denied" });
		events.send("state", player());
		expect(receive).toHaveBeenCalledOnce();
	});

	it("does not publish events from the previous account", () => {
		const { client, events, credentials, auth } = fixture();
		const receive = vi.fn();
		client.player.subscribe(receive);
		credentials.generation++;
		events.send("auth", { error: "signed_out" });
		expect(receive).not.toHaveBeenCalled();
		expect(auth.lost).not.toHaveBeenCalled();
		expect(events.close).toHaveBeenCalledOnce();
	});

	it("supports teardown and an already-aborted owner", () => {
		const { client, events, openEvents } = fixture();
		const receive = vi.fn();
		const controller = new AbortController();
		const close = client.player.subscribe(receive, controller.signal);
		controller.abort();
		close();
		events.send("state", player());
		expect(events.close).toHaveBeenCalledOnce();
		expect(receive).not.toHaveBeenCalled();
		client.player.subscribe(receive, controller.signal);
		expect(openEvents).toHaveBeenCalledOnce();
	});

	it.each(["invalid-json", '{"revision":1}', '{"error":5}'])(
		"reports malformed payload %s",
		(data) => {
			const { client, events } = fixture();
			const receive = vi.fn();
			client.player.subscribe(receive);
			events.dispatchEvent(new MessageEvent("state", { data }));
			expect(receive).toHaveBeenCalledWith({ type: "invalid", error: expect.any(Error) });
			expect(events.close).toHaveBeenCalledOnce();
		},
	);
});
