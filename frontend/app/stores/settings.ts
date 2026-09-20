import { defineStore } from "pinia";
import { iconMaps } from "~/config/icons";
import { defaultAppearance, type Appearance } from "#shared/appearance";
import { createAppearanceSync } from "~/auth/appearance";
import { useProfileStore } from "~/stores/profile";

export type ColorModePreference = "light" | "dark" | "system" | "time";
export type TextSize = "sm" | "md" | "lg";

export const useSettingsStore = defineStore("settings", () => {
	const settings = reactive<Appearance>({ ...defaultAppearance });
	const profile = useProfileStore();
	const sync = createAppearanceSync(settings, profile.request);
	watch(
		() => [profile.profile?.id ?? null, profile.appearance] as const,
		([id, appearance]) => sync.bind(id, appearance),
		{ immediate: true, flush: "sync" },
	);
	onScopeDispose(sync.dispose);
	const icons = computed(() => iconMaps[settings.iconSet]);

	return { settings, icons, error: sync.error, retry: sync.retry };
});
