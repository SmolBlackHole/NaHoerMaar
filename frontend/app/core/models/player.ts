// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { components } from "../api/schema.generated";

type Schema = components["schemas"];

export type PlayerState = Schema["PlayerView"];
export type PlayerChange = Schema["ChangeView"];
export type MutationResult = Schema["MutationView"];
export type VoiceChannel = Schema["VoiceChannelView"];

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
