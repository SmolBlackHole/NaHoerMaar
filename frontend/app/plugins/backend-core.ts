// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import { createBackendCore } from "../core/bootstrap";

export default defineNuxtPlugin({
	name: "backend-core",
	dependsOn: ["pinia"],
	setup(app) {
		const core = createBackendCore({ fetch: (input, init) => fetch(input, init) });
		core.stores.useSessionStore(app.$pinia);
		return { provide: { backendCore: core } };
	},
});

declare module "#app" {
	interface NuxtApp {
		$backendCore: ReturnType<typeof createBackendCore>;
	}
}

declare module "vue" {
	interface ComponentCustomProperties {
		$backendCore: ReturnType<typeof createBackendCore>;
	}
}

export {};
