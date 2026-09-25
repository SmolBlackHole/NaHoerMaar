// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { SubscribePlayer } from "../api/events";
import type { Transport } from "../api/transport";
import type { MutationResult, PlayerCommands, PlayerState, VoiceChannel } from "../models/player";

export function createPlayerRepository(request: Transport, subscribe: SubscribePlayer) {
	function command(path: string, body: object, method = "POST") {
		return request<MutationResult>(`/api/player${path}`, {
			method,
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify(body),
		});
	}
	return {
		state: (signal?: AbortSignal) => request<PlayerState>("/api/player", { signal }),
		channels: (signal?: AbortSignal) =>
			request<VoiceChannel[]>("/api/player/voice/channels", { signal }),
		add: (body: PlayerCommands["add"]) => command("/queue", body),
		remove: (entryId: string, body: PlayerCommands["remove"]) =>
			command(`/queue/${encodeURIComponent(entryId)}`, body, "DELETE"),
		move: (entryId: string, body: PlayerCommands["move"]) =>
			command(`/queue/${encodeURIComponent(entryId)}`, body, "PUT"),
		clear: (body: PlayerCommands["clear"]) => command("/queue/clear", body),
		undo: (body: PlayerCommands["undo"]) => command("/queue/undo", body),
		play: (body: PlayerCommands["play"]) => command("/play", body),
		pause: (body: PlayerCommands["pause"]) => command("/pause", body),
		skip: (body: PlayerCommands["skip"]) => command("/skip", body),
		stop: (body: PlayerCommands["stop"]) => command("/stop", body),
		seek: (body: PlayerCommands["seek"]) => command("/seek", body),
		setVolume: (body: PlayerCommands["volume"]) => command("/volume", body, "PUT"),
		setCrossfade: (body: PlayerCommands["crossfade"]) => command("/crossfade", body, "PUT"),
		join: (body: PlayerCommands["join"]) => command("/voice/join", body),
		leave: (body: PlayerCommands["leave"]) => command("/voice/leave", body),
		startRadio: (body: PlayerCommands["startRadio"]) => command("/radio", body),
		stopRadio: (body: PlayerCommands["stopRadio"]) => command("/radio/stop", body),
		retryRadio: (body: PlayerCommands["retryRadio"]) => command("/radio/retry", body),
		subscribe,
	};
}

export type PlayerRepository = ReturnType<typeof createPlayerRepository>;
