// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { components } from "../api/schema.generated";

type Schema = components["schemas"];

export type PlayerState = Schema["PlayerView"];
export type PlayerChange = Schema["ChangeView"];
export type MutationResult = Schema["MutationView"];
export type VoiceChannel = Schema["VoiceChannelView"];
export type Track = Schema["TrackView"];
export type TrackSource = Schema["TrackSourceView"];
export type QueueEntry = Schema["QueueEntryView"];
export type TrackRequest = Schema["RequestView"];
export type Contributor = NonNullable<TrackRequest["contributor"]>;
export type PlaybackPhase = PlayerState["runtime"]["phase"];

export interface PlayerCommands {
	add: Schema["AddQueueInput"];
	remove: Schema["RevisionInput"];
	move: Schema["MoveQueueInput"];
	clear: Schema["ClearQueueInput"];
	undo: Schema["UndoQueueInput"];
	play: Schema["OperationInput"];
	pause: Schema["OperationInput"];
	skip: Schema["OperationInput"];
	stop: Schema["OperationInput"];
	seek: Schema["SeekInput"];
	volume: Schema["VolumeInput"];
	crossfade: Schema["CrossfadeInput"];
	join: Schema["JoinVoiceInput"];
	leave: Schema["OperationInput"];
	startRadio: Schema["StartRadioInput"];
	stopRadio: Schema["RadioMutationInput"];
	retryRadio: Schema["RadioMutationInput"];
}

export type PlayerEvent =
	| { type: "state"; state: PlayerState }
	| { type: "change"; change: PlayerChange }
	| { type: "auth"; error: string }
	| { type: "connection"; status: "connecting" | "reconnecting" | "closed" }
	| { type: "invalid"; error: Error };

export function artistNames(track: Track): string {
	return track.artists.map(({ name }) => name).join(", ") || "Unknown artist";
}

export function trackSource(request: TrackRequest): TrackSource | undefined {
	return (
		request.track.sources.find(({ id }) => id === request.source_id) ?? request.track.sources[0]
	);
}

export function youtubeVideoId(request: TrackRequest): string | null {
	const source = trackSource(request);
	if (!source || !["youtube", "youtube_music"].includes(source.provider)) return null;
	try {
		const url = new URL(source.source_url);
		return (
			url.searchParams.get("v") ??
			(url.hostname === "youtu.be" ? url.pathname.slice(1) : null)
		);
	} catch {
		return source.external_id || null;
	}
}

export function formatTime(value: number | null | undefined): string {
	if (value === null || value === undefined || !Number.isFinite(value)) return "0:00";
	const seconds = Math.max(0, Math.floor(value));
	return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}
