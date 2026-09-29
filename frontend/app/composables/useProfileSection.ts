// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

export type ProfileSection = "overview" | "liked" | "disliked" | "playlists";

const sections = new Set<ProfileSection>(["overview", "liked", "disliked", "playlists"]);

export function useProfileSection() {
	const route = useRoute();
	const router = useRouter();
	return computed<ProfileSection>({
		get() {
			const raw = Array.isArray(route.query.view) ? route.query.view[0] : route.query.view;
			return typeof raw === "string" && sections.has(raw as ProfileSection)
				? (raw as ProfileSection)
				: "overview";
		},
		set(value) {
			void router.push({
				query: {
					...route.query,
					view: value === "overview" ? undefined : value,
					q: undefined,
					page: undefined,
				},
			});
		},
	});
}
