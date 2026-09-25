// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import { createPlayerEvents, type OpenEvents } from "./api/events";
import { createTransport, type AuthBoundary } from "./api/transport";
import { createAccountRepository } from "./repositories/account";
import { createListeningRepository } from "./repositories/listening";
import { createPlayerRepository } from "./repositories/player";

export interface BackendDependencies {
	fetch: typeof fetch;
	auth: AuthBoundary;
	openEvents?: OpenEvents;
}

/** Per-app composition, deliberately not installed in the existing Nuxt plugin yet. */
export function createBackendClient(dependencies: BackendDependencies) {
	const request = createTransport(dependencies.fetch, dependencies.auth);
	return {
		account: createAccountRepository(request),
		listening: createListeningRepository(request),
		player: createPlayerRepository(
			request,
			createPlayerEvents(dependencies.auth, dependencies.openEvents),
		),
	};
}

export type BackendClient = ReturnType<typeof createBackendClient>;
