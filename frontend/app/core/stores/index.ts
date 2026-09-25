// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import { defineStore } from "pinia";
import { computed, onScopeDispose, ref, shallowRef, watch } from "vue";
import { ApiFailure, SessionLost, type SessionAuthority } from "../api/transport";
import type { BackendClient } from "../client";
import type { AccountSession } from "../models/account";
import type { MutationResult, PlayerChange, PlayerEvent, PlayerState } from "../models/player";

export type AccountStatus =
	| "checking"
	| "authenticated"
	| "signed_out"
	| "forbidden"
	| "unavailable";
export type PlayerConnection = "closed" | "connecting" | "live" | "reconnecting";

export interface SettledOperation {
	operationId: string;
	action: string;
	outcome: MutationResult["outcome"];
	replayed: boolean;
	own: boolean;
	source: "http" | "sse";
}

function statusFor(reason: string): AccountStatus {
	if (reason === "access_denied") return "forbidden";
	if (reason === "auth_unavailable" || reason === "access_unavailable") return "unavailable";
	return "signed_out";
}

export function createBackendStores(client: BackendClient, authority: SessionAuthority) {
	const useSessionStore = defineStore("backendSession", () => {
		const account = shallowRef<AccountSession | null>(null);
		const status = ref<AccountStatus>("checking");
		const generation = ref(authority.current().generation);
		const error = ref<string | null>(null);
		const busy = ref(false);
		let restoreController: AbortController | undefined;
		let restoring: Promise<boolean> | undefined;

		const unsubscribe = authority.subscribe(({ credentials, reason }) => {
			generation.value = credentials.generation;
			restoreController?.abort();
			restoreController = undefined;
			account.value = null;
			status.value = credentials.csrf ? "checking" : statusFor(reason);
			error.value = null;
		});

		function restore(force = false): Promise<boolean> {
			if (!force && account.value && status.value === "authenticated")
				return Promise.resolve(true);
			if (restoring) return restoring;
			const currentGeneration = authority.current().generation;
			const controller = new AbortController();
			restoreController = controller;
			status.value = "checking";
			error.value = null;
			let task: Promise<boolean>;
			task = (async () => {
				try {
					const value = await client.account.session(controller.signal);
					if (
						controller.signal.aborted ||
						currentGeneration !== authority.current().generation
					)
						return false;
					const previous = account.value;
					if (
						authority.current().csrf !== value.csrf ||
						(previous !== null && previous.user_id !== value.user_id)
					)
						authority.replace(value.csrf, "session_restored");
					account.value = value;
					status.value = "authenticated";
					return true;
				} catch (failure) {
					if (
						!controller.signal.aborted &&
						currentGeneration === authority.current().generation &&
						!(failure instanceof SessionLost)
					) {
						status.value =
							failure instanceof ApiFailure &&
							(failure.status === 403 || failure.error.error === "access_denied")
								? "forbidden"
								: "unavailable";
						error.value = failure instanceof Error ? failure.message : String(failure);
					}
					return false;
				} finally {
					if (restoreController === controller) restoreController = undefined;
					restoring = undefined;
				}
			})();
			restoring = task;
			return task;
		}

		async function logout(): Promise<boolean> {
			if (busy.value || status.value !== "authenticated") return false;
			busy.value = true;
			error.value = null;
			try {
				await client.account.logout();
				authority.lost("signed_out");
				return true;
			} catch (failure) {
				if (failure instanceof SessionLost) return true;
				error.value = failure instanceof Error ? failure.message : String(failure);
				return false;
			} finally {
				busy.value = false;
			}
		}

		onScopeDispose(() => {
			restoreController?.abort();
			unsubscribe();
		});
		return {
			account,
			status,
			generation,
			error,
			busy,
			loginUrl: client.account.loginUrl,
			restore,
			refresh: () => restore(true),
			logout,
		};
	});

	const usePlayerStore = defineStore("backendPlayer", () => {
		const session = useSessionStore();
		const state = shallowRef<PlayerState | null>(null);
		const connection = ref<PlayerConnection>("closed");
		const channels = shallowRef<Awaited<ReturnType<typeof client.player.channels>>>([]);
		const channelsLoading = ref(false);
		const channelError = ref<string | null>(null);
		const error = ref<string | null>(null);
		const pendingOperationIds = ref<string[]>([]);
		const uncertainOperation = shallowRef<{ operationId: string; action: string } | null>(null);
		const lastOperation = shallowRef<SettledOperation | null>(null);
		const canControl = computed(
			() =>
				session.status === "authenticated" &&
				connection.value === "live" &&
				state.value !== null,
		);
		const currentTrack = computed(() => state.value?.runtime.current ?? null);
		const outcomes = new Map<string, SettledOperation>();
		const commands = new Map<string, (operationId: string) => Promise<MutationResult>>();
		let activeGeneration: number | null = null;
		let disconnect: (() => void) | undefined;
		let streamController: AbortController | undefined;
		let channelController: AbortController | undefined;
		let awaitingSnapshot = true;

		function reset() {
			streamController?.abort();
			streamController = undefined;
			disconnect?.();
			disconnect = undefined;
			channelController?.abort();
			channelController = undefined;
			activeGeneration = null;
			awaitingSnapshot = true;
			state.value = null;
			channels.value = [];
			channelsLoading.value = false;
			channelError.value = null;
			connection.value = "closed";
			error.value = null;
			pendingOperationIds.value = [];
			uncertainOperation.value = null;
			lastOperation.value = null;
			outcomes.clear();
			commands.clear();
		}

		function accept(next: PlayerState, freshSnapshot = false) {
			const previous = state.value;
			if (
				previous?.session_id === next.session_id &&
				next.revision < previous.revision &&
				!freshSnapshot
			)
				return;
			if (previous && previous.session_id !== next.session_id) {
				pendingOperationIds.value = [];
				uncertainOperation.value = null;
				outcomes.clear();
				commands.clear();
			}
			state.value = next;
		}

		function settle(
			operationId: string,
			action: string,
			outcome: MutationResult["outcome"],
			replayed: boolean,
			own: boolean,
			source: SettledOperation["source"],
		) {
			if (outcomes.has(operationId)) return;
			const result = { operationId, action, outcome, replayed, own, source };
			outcomes.set(operationId, result);
			if (outcomes.size > 256) outcomes.delete(outcomes.keys().next().value!);
			pendingOperationIds.value = pendingOperationIds.value.filter(
				(id) => id !== operationId,
			);
			commands.delete(operationId);
			if (uncertainOperation.value?.operationId === operationId)
				uncertainOperation.value = null;
			lastOperation.value = result;
			error.value = null;
		}

		function receive(event: PlayerEvent) {
			if (activeGeneration !== session.generation || session.status !== "authenticated")
				return;
			if (event.type === "connection") {
				connection.value = event.status;
				awaitingSnapshot = event.status !== "closed";
				if (event.status === "closed") error.value = "The player event stream closed.";
				return;
			}
			if (event.type === "invalid") {
				connection.value = "closed";
				error.value = event.error.message;
				return;
			}
			if (event.type === "auth") return;
			if (event.type === "state") {
				accept(event.state, awaitingSnapshot || state.value === null);
				awaitingSnapshot = false;
				connection.value = "live";
				error.value = null;
				return;
			}
			const change: PlayerChange = event.change;
			accept(change.state);
			connection.value = "live";
			settle(
				change.operation_id,
				change.action,
				change.outcome,
				false,
				commands.has(change.operation_id),
				"sse",
			);
		}

		async function refreshChannels(generation: number) {
			channelController?.abort();
			const controller = new AbortController();
			channelController = controller;
			channelsLoading.value = true;
			channelError.value = null;
			try {
				const result = await client.player.channels(controller.signal);
				if (controller.signal.aborted || generation !== session.generation) return;
				channels.value = result;
			} catch (failure) {
				if (
					!controller.signal.aborted &&
					generation === session.generation &&
					!(failure instanceof SessionLost)
				)
					channelError.value =
						failure instanceof Error ? failure.message : String(failure);
			} finally {
				if (channelController === controller) {
					channelsLoading.value = false;
					channelController = undefined;
				}
			}
		}

		function connect(generation: number) {
			reset();
			activeGeneration = generation;
			connection.value = "connecting";
			streamController = new AbortController();
			disconnect = client.player.subscribe(receive, streamController.signal);
			void refreshChannels(generation);
		}

		const stopWatch = watch(
			() => [session.generation, session.status] as const,
			([generation, status]) => {
				if (status !== "authenticated") {
					if (activeGeneration !== null || state.value !== null) reset();
					return;
				}
				if (activeGeneration !== generation || !disconnect) connect(generation);
			},
			{ immediate: true },
		);

		async function submit(
			operationId: string,
			action: string,
			execute: (operationId: string) => Promise<MutationResult>,
			generation: number,
		): Promise<MutationResult | null> {
			if (!canControl.value || generation !== session.generation) return null;
			if (!pendingOperationIds.value.includes(operationId))
				pendingOperationIds.value = [...pendingOperationIds.value, operationId];
			error.value = null;
			try {
				const result = await execute(operationId);
				if (generation !== session.generation || session.status !== "authenticated")
					return null;
				accept(result.player);
				settle(
					operationId,
					result.outcome.action,
					result.outcome,
					result.replayed,
					true,
					"http",
				);
				return result;
			} catch (failure) {
				if (generation !== session.generation || failure instanceof SessionLost)
					return null;
				if (outcomes.has(operationId)) return null;
				pendingOperationIds.value = pendingOperationIds.value.filter(
					(id) => id !== operationId,
				);
				if (
					failure instanceof ApiFailure &&
					failure.status < 500 &&
					!failure.error.retryable
				) {
					commands.delete(operationId);
					error.value = failure.error.error;
					return null;
				}
				uncertainOperation.value = { operationId, action };
				error.value =
					"The response was lost. Check the player state before retrying this command.";
				return null;
			}
		}

		function run(
			action: string,
			execute: (operationId: string) => Promise<MutationResult>,
		): Promise<MutationResult | null> {
			if (!canControl.value) return Promise.resolve(null);
			const operationId = crypto.randomUUID();
			const generation = session.generation;
			commands.set(operationId, execute);
			return submit(operationId, action, execute, generation);
		}

		function retryUncertain(): Promise<MutationResult | null> {
			const uncertain = uncertainOperation.value;
			const execute = uncertain ? commands.get(uncertain.operationId) : undefined;
			if (!uncertain || !execute || !canControl.value) return Promise.resolve(null);
			return submit(uncertain.operationId, uncertain.action, execute, session.generation);
		}

		onScopeDispose(() => {
			stopWatch();
			reset();
		});
		return {
			state,
			connection,
			channels,
			channelsLoading,
			channelError,
			error,
			pendingOperationIds,
			uncertainOperation,
			lastOperation,
			canControl,
			currentTrack,
			run,
			retryUncertain,
			refreshChannels: () => refreshChannels(session.generation),
		};
	});

	return { useSessionStore, usePlayerStore };
}
