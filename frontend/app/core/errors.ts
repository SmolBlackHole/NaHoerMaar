// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { IconKey } from "../config/icons";
import { ApiFailure, InvalidResponse, SessionLost } from "./api/transport";

interface FailureCopy {
	title: string;
	description: string;
	icon: IconKey;
	retryable?: boolean;
}

export interface FailurePresentation extends FailureCopy {
	code: string;
	status: number | null;
	requestId: string | null;
	retryable: boolean;
}

const failures: Record<string, FailureCopy> = {
	access_denied: {
		title: "Nope.",
		description: "You are not on the guest list.",
		icon: "key",
	},
	authentication_required: {
		title: "Who are you again?",
		description: "Your session wandered off. Sign in with Discord again.",
		icon: "user",
	},
	access_unavailable: {
		title: "Access checks are taking a nap.",
		description: "NaHörMaar could not check who is allowed in. Try again in a moment.",
		icon: "shieldCheck",
		retryable: true,
	},
	actor_required: {
		title: "Who pressed that?",
		description: "This action needs a signed-in user.",
		icon: "user",
	},
	audio_source_not_found: {
		title: "The song exists. The audio does not.",
		description: "The metadata survived. The playable source, less so.",
		icon: "music",
	},
	avatar_unavailable: {
		title: "That avatar wandered off.",
		description: "Discord did not provide a usable profile picture.",
		icon: "user",
		retryable: true,
	},
	catalog_closed: {
		title: "The music catalog is offline.",
		description: "It is shutting down or still starting. Try again shortly.",
		icon: "music",
		retryable: true,
	},
	catalog_maintenance_partial: {
		title: "A few tracks need another pass.",
		description:
			"The run finished, but at least one provider lookup failed. Open the run for the exact track and reason.",
		icon: "warning",
		retryable: true,
	},
	conflict: {
		title: "Everyone touched the same thing at once.",
		description: "The state changed while this request was catching up. Try again.",
		icon: "reload",
		retryable: true,
	},
	csrf_failed: {
		title: "This page is living in the past.",
		description: "Reload the page before trying that action again.",
		icon: "reload",
		retryable: true,
	},
	event_stream_closed: {
		title: "The live connection checked out.",
		description: "Player controls return after the browser reconnects.",
		icon: "radio",
		retryable: true,
	},
	event_stream_invalid: {
		title: "The live update made no sense.",
		description: "Reload the page to synchronize the player again.",
		icon: "warning",
		retryable: true,
	},
	gateway_timeout: {
		title: "The bot took the scenic route.",
		description: "It did not answer in time. Try again before sending out a search party.",
		icon: "clock",
		retryable: true,
	},
	grant_not_owned: {
		title: "That access grant is not yours to remove.",
		description: "Only its owner or the server operator can change it.",
		icon: "key",
	},
	housekeeping_partial: {
		title: "Housekeeping left something behind.",
		description:
			"The run completed only part of its cleanup. Open the run to see which category needs attention.",
		icon: "warning",
		retryable: true,
	},
	idempotency_conflict: {
		title: "We've already had this conversation.",
		description: "This operation does not match the one that already used its ID.",
		icon: "copy",
	},
	internal_error: {
		title: "Well, fuck. We broke something.",
		description: "The problem is on our side. Try once more, then check the logs.",
		icon: "error",
		retryable: true,
	},
	invalid_command: {
		title: "That command failed the vibe check.",
		description: "The player cannot do that in its current state.",
		icon: "caution",
	},
	invalid_discord_id: {
		title: "That is not a Discord user ID.",
		description: "Copy the numeric user ID from Discord and try again.",
		icon: "user",
	},
	invalid_limit: {
		title: "That is an ambitious amount of music.",
		description: "Choose a smaller result limit and try again.",
		icon: "list",
	},
	invalid_query: {
		title: "That search had bad vibes.",
		description: "Enter a title, artist or supported link and try again.",
		icon: "search",
	},
	invalid_radio_seed: {
		title: "The radio has nowhere to start.",
		description: "Choose a track or playlist before starting radio.",
		icon: "radio",
	},
	invalid_request: {
		title: "That request had bad vibes.",
		description: "Something in it did not make sense. Check it and try again.",
		icon: "caution",
	},
	invalid_response: {
		title: "The backend spoke nonsense.",
		description: "The response was not valid NaHörMaar data. Check the logs before retrying.",
		icon: "warning",
		retryable: true,
	},
	login_busy: {
		title: "Easy there.",
		description: "A login attempt is already running. Give it a second.",
		icon: "clock",
		retryable: true,
	},
	login_cancelled: {
		title: "Login cancelled.",
		description: "Discord did not finish signing you in.",
		icon: "user",
	},
	login_expired: {
		title: "That login attempt fossilized.",
		description: "Start over before this one gets its own museum exhibit.",
		icon: "clock",
	},
	login_failed: {
		title: "Discord login fell over.",
		description: "Try signing in again. The logs have the technical reason.",
		icon: "user",
		retryable: true,
	},
	login_unavailable: {
		title: "Discord login is unavailable.",
		description: "NaHörMaar could not reach the login provider. Try again shortly.",
		icon: "user",
		retryable: true,
	},
	library_playlist_undo_unavailable: {
		title: "That undo window has closed.",
		description: "The playlist changed or the removed track can no longer be restored.",
		icon: "reload",
	},
	maintenance_busy: {
		title: "The maintenance job is already busy.",
		description: "Let the current run finish before starting another one.",
		icon: "settings",
		retryable: true,
	},
	method_not_allowed: {
		title: "Wrong door, wrong knock.",
		description: "That route exists. It does not accept this kind of request.",
		icon: "caution",
	},
	network_unavailable: {
		title: "The browser and backend aren't speaking.",
		description: "Check the connection and try again.",
		icon: "radio",
		retryable: true,
	},
	not_found: {
		title: "There is nothing there.",
		description: "The requested resource does not exist anymore, if it ever did.",
		icon: "search",
	},
	nothing_playing: {
		title: "There is literally nothing playing.",
		description: "You managed to pause, seek or skip silence. Impressive.",
		icon: "volume",
	},
	nothing_to_play: {
		title: "The queue is empty. Tragic.",
		description: "Add something before asking the bot to perform miracles.",
		icon: "list",
	},
	voice_channel_required: {
		title: "Join a voice channel first.",
		description: "Choose a Discord voice channel before starting playback.",
		icon: "headphones",
	},
	operator_access_managed_in_config: {
		title: "That role lives in the server config.",
		description: "Owner and admin access can only be changed by the bot operator.",
		icon: "shieldCheck",
	},
	origin_forbidden: {
		title: "This address is not invited.",
		description: "The browser origin does not match NaHörMaar's public URL configuration.",
		icon: "key",
	},
	playback_failed: {
		title: "That track refused to play.",
		description: "NaHörMaar returned it to the queue and moved on.",
		icon: "music",
		retryable: true,
	},
	player_busy: {
		title: "The player is busy.",
		description: "Another command is still being processed. Give it a moment.",
		icon: "clock",
		retryable: true,
	},
	profile_not_found: {
		title: "That profile does not exist.",
		description: "The user has not signed in to NaHörMaar yet.",
		icon: "user",
	},
	provider_failed: {
		title: "YouTube has chosen violence.",
		description: "The provider failed to return something usable. Try again in a moment.",
		icon: "radio",
		retryable: true,
	},
	queue_conflict: {
		title: "Everyone touched the queue at once.",
		description: "The queue changed while this action was catching up. Try again.",
		icon: "reload",
		retryable: true,
	},
	queue_entry_not_found: {
		title: "That track already escaped.",
		description: "The queue changed before you could catch it.",
		icon: "music",
	},
	rate_limited: {
		title: "Easy there, DJ.",
		description: "That is a lot of requests at once. Give the bot a second to breathe.",
		icon: "warning",
		retryable: true,
	},
	radio_conflict: {
		title: "The radio changed stations mid-sentence.",
		description: "Its state changed before this action finished. Try again.",
		icon: "radio",
		retryable: true,
	},
	radio_not_active: {
		title: "There is no radio to stop.",
		description: "The radio already ended or was replaced.",
		icon: "radio",
	},
	radio_provider_failed: {
		title: "YouTube has chosen violence.",
		description: "Radio could not find more tracks. Retry it when the provider behaves again.",
		icon: "radio",
		retryable: true,
	},
	request_timeout: {
		title: "This request wandered off.",
		description: "It took too long and missed its cue. Try it again.",
		icon: "clock",
		retryable: true,
	},
	response_lost: {
		title: "Response not received.",
		description: "The command may have completed. Check the player state before retrying it.",
		icon: "warning",
		retryable: true,
	},
	session_closed: {
		title: "The player session is closing.",
		description: "Wait for the player to come back before trying again.",
		icon: "radio",
		retryable: true,
	},
	service_unavailable: {
		title: "The party is temporarily on hold.",
		description: "NaHörMaar is starting, restarting or taking a very short nap.",
		icon: "radio",
		retryable: true,
	},
	signed_out: {
		title: "Who are you again?",
		description: "Your session wandered off. Sign in with Discord again.",
		icon: "user",
	},
	snapshot_not_found: {
		title: "Those cached results are gone.",
		description: "Run the search again to get a fresh result set.",
		icon: "search",
		retryable: true,
	},
	track_not_found: {
		title: "That track disappeared.",
		description: "Search for it again before adding it to the queue.",
		icon: "music",
	},
	undo_unavailable: {
		title: "That undo window has closed.",
		description: "The queue moved on before the change could be restored.",
		icon: "reload",
	},
	unknown_provider: {
		title: "That music provider is unknown.",
		description: "Choose one of the providers supported by NaHörMaar.",
		icon: "music",
	},
	unsupported_link: {
		title: "NaHörMaar cannot read that link.",
		description: "Use a supported YouTube or YouTube Music URL.",
		icon: "external",
	},
	upstream_failed: {
		title: "The browser and backend aren't speaking.",
		description: "One of them left the conversation. A retry usually gets them talking again.",
		icon: "radio",
		retryable: true,
	},
	validation_failed: {
		title: "Technically valid. Practically nonsense.",
		description: "The request reached us intact, then immediately failed the vibe check.",
		icon: "caution",
	},
	voice_connection_failed: {
		title: "Discord voice refused to cooperate.",
		description: "Check the channel permissions, then try joining again.",
		icon: "headphones",
		retryable: true,
	},
};

const statusFailures: Record<number, FailureCopy> = {
	400: failures.invalid_request!,
	401: failures.signed_out!,
	403: failures.access_denied!,
	404: failures.not_found!,
	405: failures.method_not_allowed!,
	408: failures.request_timeout!,
	409: failures.queue_conflict!,
	422: failures.validation_failed!,
	429: failures.rate_limited!,
	500: failures.internal_error!,
	502: failures.upstream_failed!,
	503: failures.service_unavailable!,
	504: failures.gateway_timeout!,
};

const unexpected: FailureCopy = {
	title: "This was not in the plan.",
	description: "Something unexpected happened. The logs probably know more than we do.",
	icon: "warning",
	retryable: true,
};

export function failureForCode(
	code: string,
	options: {
		status?: number | null;
		retryable?: boolean | null;
		requestId?: string | null;
	} = {},
): FailurePresentation {
	const status = options.status ?? null;
	const copy =
		failures[code] ?? (status === null ? undefined : statusFailures[status]) ?? unexpected;
	return {
		...copy,
		code,
		status,
		requestId: options.requestId ?? null,
		retryable: options.retryable ?? copy.retryable ?? (status !== null && status >= 500),
	};
}

export function presentFailure(failure: unknown): FailurePresentation {
	if (failure instanceof ApiFailure)
		return failureForCode(failure.error.code, {
			status: failure.status,
			retryable: failure.error.retryable,
			requestId: failure.requestId,
		});
	if (failure instanceof InvalidResponse) {
		return failureForCode("invalid_response", {
			retryable: true,
			requestId: failure.requestId,
		});
	}
	if (failure instanceof SessionLost) return failureForCode("signed_out");
	if (failure instanceof Error) {
		if (failure.name === "TimeoutError") return failureForCode("request_timeout");
		if (failure.name === "TypeError") return failureForCode("network_unavailable");
		if (failures[failure.message]) return failureForCode(failure.message);
	}
	return failureForCode("unexpected_error");
}

export function failureMessage(failure: unknown): string {
	return presentFailure(failure).description;
}
