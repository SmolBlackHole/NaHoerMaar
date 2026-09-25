// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import { createPlayerEvents, type OpenEvents } from "./api/events";
import { createTransport, type AuthBoundary } from "./api/transport";
import { createAccountRepository } from "./repositories/account";
import { createAccessRepository } from "./repositories/access";
import { createCatalogRepository } from "./repositories/catalog";
import { createListeningRepository } from "./repositories/listening";
import { createLogsRepository } from "./repositories/logs";
import { createPlayerRepository } from "./repositories/player";
import { createStatisticsRepository } from "./repositories/statistics";

export interface BackendDependencies {
	fetch: typeof fetch;
	auth: AuthBoundary;
	openEvents?: OpenEvents;
}

/** Build the HTTP repositories used by one Nuxt application instance. */
export function createBackendClient(dependencies: BackendDependencies) {
	const request = createTransport(dependencies.fetch, dependencies.auth);
	return {
		access: createAccessRepository(request),
		account: createAccountRepository(request),
		catalog: createCatalogRepository(request),
		listening: createListeningRepository(request),
		logs: createLogsRepository(request),
		player: createPlayerRepository(
			request,
			createPlayerEvents(dependencies.auth, dependencies.openEvents),
		),
		statistics: createStatisticsRepository(request),
	};
}

export type BackendClient = ReturnType<typeof createBackendClient>;
