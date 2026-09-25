// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { PlayerChange, PlayerEvent, PlayerState } from "../models/player";
import { SessionLost, type AuthBoundary } from "./transport";

/** Browser EventSource or an isolated test double. The browser owns reconnects. */
export interface EventStream {
	readonly readyState: number;
	addEventListener(type: string, listener: (event: Event) => void): void;
	close(): void;
}

export type OpenEvents = (url: string) => EventStream;

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null;
}

function isSnapshot(value: unknown): value is PlayerState {
	return (
		isRecord(value) &&
		typeof value.session_id === "string" &&
		Number.isInteger(value.revision) &&
		Number(value.revision) >= 0 &&
		Number.isInteger(value.queue_revision) &&
		Number(value.queue_revision) >= 0 &&
		Array.isArray(value.queue) &&
		isRecord(value.runtime)
	);
}

function decode(type: "state" | "change" | "auth", event: Event): PlayerEvent {
	const value: unknown = JSON.parse((event as MessageEvent<string>).data);
	if (type === "state" && isSnapshot(value)) return { type, state: value };
	if (
		type === "change" &&
		isRecord(value) &&
		isSnapshot(value.state) &&
		typeof value.operation_id === "string" &&
		typeof value.message_id === "string" &&
		typeof value.correlation_id === "string" &&
		typeof value.action === "string" &&
		isRecord(value.outcome)
	)
		return { type, change: value as PlayerChange };
	if (type === "auth" && isRecord(value) && typeof value.error === "string") {
		return { type, error: value.error };
	}
	throw new Error(`Invalid ${type} event from the backend.`);
}

export function createPlayerEvents(
	auth: AuthBoundary,
	open: OpenEvents = (url) => new EventSource(url),
) {
	return function subscribe(receive: (event: PlayerEvent) => void, signal?: AbortSignal) {
		const { generation, csrf } = auth.current();
		if (!csrf) throw new SessionLost();
		if (signal?.aborted) return () => {};
		const stream = open("/api/events");
		let closed = false;
		const close = () => {
			if (closed) return;
			closed = true;
			signal?.removeEventListener("abort", close);
			stream.close();
		};
		const current = () => {
			if (generation !== auth.current().generation) close();
			return !closed;
		};
		signal?.addEventListener("abort", close, { once: true });
		stream.addEventListener("open", () => {
			// An open socket is not a synchronized player; wait for its state event.
			if (current()) receive({ type: "connection", status: "connecting" });
		});
		stream.addEventListener("error", () => {
			if (!current()) return;
			const status = stream.readyState === 2 ? "closed" : "reconnecting";
			if (status === "closed") close();
			receive({ type: "connection", status });
		});
		for (const type of ["state", "change", "auth"] as const) {
			stream.addEventListener(type, (raw) => {
				if (!current()) return;
				let event: PlayerEvent;
				try {
					event = decode(type, raw);
				} catch (error) {
					close();
					receive({
						type: "invalid",
						error: error instanceof Error ? error : new Error(String(error)),
					});
					return;
				}
				if (event.type === "auth") {
					close();
					auth.lost(event.error);
				}
				// No replay or revision filtering here: reconnect sends a fresh snapshot.
				receive(event);
			});
		}
		return close;
	};
}

export type SubscribePlayer = ReturnType<typeof createPlayerEvents>;
