// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import { vi } from "vitest";
import { createBackendClient } from "../../app/core/client";
import type { EventStream } from "../../app/core/api/events";
import type { AuthBoundary, SessionCredentials } from "../../app/core/api/transport";
import type { PlayerState } from "../../app/core/models/player";

export class FakeEvents extends EventTarget implements EventStream {
	readyState = 1;
	close = vi.fn(() => {
		this.readyState = 2;
	});
	send(type: string, value: unknown) {
		this.dispatchEvent(new MessageEvent(type, { data: JSON.stringify(value) }));
	}
}

export function fixture() {
	const credentials: SessionCredentials = { generation: 0, csrf: "session-token" };
	const auth: AuthBoundary = {
		current: () => credentials,
		lost: vi.fn(() => {
			credentials.generation++;
			credentials.csrf = null;
		}),
	};
	const fetcher = vi.fn<typeof fetch>();
	const events = new FakeEvents();
	const openEvents = vi.fn(() => events);
	const client = createBackendClient({ fetch: fetcher, auth, openEvents });
	return { client, auth, credentials, fetcher, events, openEvents };
}

export const player = (revision = 1, sessionId = "listening-session"): PlayerState => ({
	session_id: sessionId,
	revision,
	queue_revision: 1,
	channel_id: "1550894913980465212",
	volume: 0.5,
	crossfade_seconds: 7,
	queue: [],
	radio: null,
	checkpoint: { intent: "stopped", request_id: null, position_seconds: 0 },
	runtime: {
		phase: "idle",
		current: null,
		playback_id: null,
		attempt_id: null,
		position_seconds: 0,
		position_updated_at: null,
		duration_seconds: null,
		last_error: null,
		voice: { phase: "connected", channel_id: "1550894913980465212", attempt: 0, error: null },
	},
});
