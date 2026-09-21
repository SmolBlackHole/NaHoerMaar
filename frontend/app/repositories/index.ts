import { inject, type InjectionKey } from "vue";
import type { AccountRepository } from "./account";
import type { CatalogRepository } from "./catalog";
import type { SessionRepository } from "./session";

export interface Repositories {
	account: AccountRepository;
	catalog: CatalogRepository;
	session: SessionRepository;
}
export const repositoriesKey: InjectionKey<Repositories> = Symbol("repositories");

export function useRepositories(): Repositories {
	const repositories = inject(repositoriesKey);
	if (!repositories) throw new Error("Repositories have not been provided to this app.");
	return repositories;
}
