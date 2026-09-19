import { defineStore } from "pinia";
import { iconMaps, type IconSet } from "~/config/icons";

export type ColorModePreference = "light" | "dark" | "system" | "time";
export type TextSize = "sm" | "md" | "lg";

export const useSettingsStore = defineStore("settings", () => {
	const settings = reactive({
		mode: "dark" as ColorModePreference,
		artworkColors: true,
		primaryColor: "teal",
		neutralColor: "zinc",
		fontFamily: "Geist",
		iconSet: "lucide" as IconSet,
		textSize: "md" as TextSize,
	});
	const icons = computed(() => iconMaps[settings.iconSet]);

	return { settings, icons };
});
