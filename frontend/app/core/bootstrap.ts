// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import { createBackendClient, type BackendDependencies } from "./client";
import { createSessionAuthority } from "./api/transport";
import { createBackendStores } from "./stores";
import { createBackendWorkflows } from "./workflows";

export type BackendCoreDependencies = Omit<BackendDependencies, "auth">;

/** Per-Nuxt-app composition root for the new backend client and its workflows. */
export function createBackendCore(dependencies: BackendCoreDependencies) {
	const authority = createSessionAuthority();
	const client = createBackendClient({ ...dependencies, auth: authority });
	return {
		authority,
		client,
		stores: createBackendStores(client, authority),
		workflows: createBackendWorkflows(client, authority),
	};
}
