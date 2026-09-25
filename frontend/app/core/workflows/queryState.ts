// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import { ref, shallowRef, type Ref, type ShallowRef } from "vue";
import { SessionLost, type SessionAuthority } from "../api/transport";

export interface QueryState<T> {
	data: ShallowRef<T | null>;
	loading: Ref<boolean>;
	error: Ref<string | null>;
	load(read: (signal: AbortSignal) => Promise<T>): Promise<T | null>;
	set(value: T | null): void;
	dispose(): void;
}

/** A page-owned read model: latest request wins and account changes clear it. */
export function createQueryState<T>(authority: SessionAuthority): QueryState<T> {
	const data = shallowRef<T | null>(null);
	const loading = ref(false);
	const error = ref<string | null>(null);
	let requestId = 0;
	let controller: AbortController | undefined;
	const unsubscribe = authority.subscribe(() => {
		requestId++;
		controller?.abort();
		controller = undefined;
		data.value = null;
		loading.value = false;
		error.value = null;
	});

	async function load(read: (signal: AbortSignal) => Promise<T>): Promise<T | null> {
		const currentId = ++requestId;
		controller?.abort();
		const currentController = new AbortController();
		controller = currentController;
		const generation = authority.current().generation;
		loading.value = true;
		error.value = null;
		try {
			const result = await read(currentController.signal);
			if (
				currentController.signal.aborted ||
				currentId !== requestId ||
				generation !== authority.current().generation
			)
				return null;
			data.value = result;
			return result;
		} catch (failure) {
			if (
				!currentController.signal.aborted &&
				currentId === requestId &&
				generation === authority.current().generation &&
				!(failure instanceof SessionLost)
			)
				error.value = failure instanceof Error ? failure.message : String(failure);
			return null;
		} finally {
			if (currentId === requestId) {
				loading.value = false;
				controller = undefined;
			}
		}
	}

	function dispose() {
		requestId++;
		controller?.abort();
		controller = undefined;
		unsubscribe();
	}

	return { data, loading, error, load, set: (value) => (data.value = value), dispose };
}
