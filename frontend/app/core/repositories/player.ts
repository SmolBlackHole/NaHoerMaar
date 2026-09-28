// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { SubscribePlayer } from "../api/events";
import type { Transport } from "../api/transport";
import type { PlayerCommands } from "../models/player";

export function createPlayerRepository(request: Transport, subscribe: SubscribePlayer) {
	const header = (operationId: string) => ({ "Idempotency-Key": operationId });
	return {
		state: (signal?: AbortSignal) => request((api) => api.GET("/api/player", { signal })),
		channels: (signal?: AbortSignal) =>
			request((api) => api.GET("/api/player/voice/channels", { signal })),
		add: (operationId: string, body: PlayerCommands["add"]) =>
			request((api) =>
				api.POST("/api/player/queue", {
					params: { header: header(operationId) },
					body,
				}),
			),
		remove: (operationId: string, entryId: string, body: PlayerCommands["remove"]) =>
			request((api) =>
				api.DELETE("/api/player/queue/{entry_id}", {
					params: { path: { entry_id: entryId }, header: header(operationId) },
					body,
				}),
			),
		move: (operationId: string, entryId: string, body: PlayerCommands["move"]) =>
			request((api) =>
				api.PATCH("/api/player/queue/{entry_id}", {
					params: { path: { entry_id: entryId }, header: header(operationId) },
					body,
				}),
			),
		clear: (operationId: string, body: PlayerCommands["clear"]) =>
			request((api) =>
				api.POST("/api/player/queue/clear", {
					params: { header: header(operationId) },
					body,
				}),
			),
		undo: (operationId: string, body: PlayerCommands["undo"]) =>
			request((api) =>
				api.POST("/api/player/queue/undo", {
					params: { header: header(operationId) },
					body,
				}),
			),
		control: (operationId: string, body: PlayerCommands["control"]) =>
			request((api) =>
				api.POST("/api/player/control", {
					params: { header: header(operationId) },
					body,
				}),
			),
		update: (operationId: string, body: PlayerCommands["settings"]) =>
			request((api) =>
				api.PATCH("/api/player", {
					params: { header: header(operationId) },
					body,
				}),
			),
		setSleepTimer: (operationId: string, body: PlayerCommands["sleepTimer"]) =>
			request((api) =>
				api.PUT("/api/player/sleep-timer", {
					params: { header: header(operationId) },
					body,
				}),
			),
		cancelSleepTimer: (operationId: string) =>
			request((api) =>
				api.DELETE("/api/player/sleep-timer", {
					params: { header: header(operationId) },
				}),
			),
		join: (operationId: string, body: PlayerCommands["voice"]) =>
			request((api) =>
				api.PUT("/api/player/voice", {
					params: { header: header(operationId) },
					body,
				}),
			),
		leave: (operationId: string) =>
			request((api) =>
				api.DELETE("/api/player/voice", {
					params: { header: header(operationId) },
				}),
			),
		startRadio: (operationId: string, body: PlayerCommands["startRadio"]) =>
			request((api) =>
				api.PUT("/api/player/radio", {
					params: { header: header(operationId) },
					body,
				}),
			),
		stopRadio: (operationId: string, body: PlayerCommands["stopRadio"]) =>
			request((api) =>
				api.DELETE("/api/player/radio", {
					params: { header: header(operationId) },
					body,
				}),
			),
		retryRadio: (operationId: string, body: PlayerCommands["retryRadio"]) =>
			request((api) =>
				api.POST("/api/player/radio/retry", {
					params: { header: header(operationId) },
					body,
				}),
			),
		subscribe,
	};
}

export type PlayerRepository = ReturnType<typeof createPlayerRepository>;
