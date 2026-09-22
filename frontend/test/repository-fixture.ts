import { createApp, effectScope } from "vue";
import { createPinia, disposePinia, setActivePinia } from "pinia";
import { afterEach } from "vitest";
import { repositoriesKey } from "../app/repositories";
import { createHttpTransport, type AuthHooks } from "../app/repositories/transport";
import { createAccountRepository } from "../app/repositories/account";
import { createCatalogRepository } from "../app/repositories/catalog";
import { createSessionRepository } from "../app/repositories/session";
import { createDiagnosticsRepository } from "../app/repositories/diagnostics";

const cleanups: (() => void)[] = [];
afterEach(() => cleanups.splice(0).forEach((close) => close()));

/** A fresh Vue app and real Pinia per test; network and event source are injected. */
export function repositoryFixture(
	request: typeof fetch,
	openEvents?: (url: string) => EventSource,
	auth: AuthHooks = {
		current: () => ({ generation: 0, csrfToken: "test-csrf" }),
		lost: () => {},
	},
) {
	const app = createApp({});
	const pinia = createPinia();
	app.use(pinia);
	setActivePinia(pinia);
	const json = createHttpTransport(request, auth);
	const repositories = {
		account: createAccountRepository(json),
		catalog: createCatalogRepository(json),
		session: createSessionRepository(json, openEvents),
		diagnostics: createDiagnosticsRepository(json),
	};
	app.provide(repositoriesKey, repositories);
	const scope = effectScope();
	cleanups.push(() => {
		scope.stop();
		disposePinia(pinia);
	});
	return { app, pinia, repositories, scope, json };
}
