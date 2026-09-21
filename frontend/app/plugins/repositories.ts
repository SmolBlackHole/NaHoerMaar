import { useProfileStore } from "../stores/profile";
import { repositoriesKey } from "../repositories";
import { createHttpTransport } from "../repositories/transport";
import { createSessionRepository } from "../repositories/session";
import { createCatalogRepository } from "../repositories/catalog";
import { createAccountRepository } from "../repositories/account";

export default defineNuxtPlugin({
	name: "repositories",
	dependsOn: ["pinia"],
	setup(app) {
		// Auth is read at request time, after the app has provided the repositories.
		const profile = () => useProfileStore(app.$pinia);
		const json = createHttpTransport((...args) => fetch(...args), {
			current: () => profile().credentials,
			lost: (code) => profile().lost(code),
		});
		app.vueApp.provide(repositoriesKey, {
			session: createSessionRepository(json),
			catalog: createCatalogRepository(json),
			account: createAccountRepository(json),
		});
	},
});
