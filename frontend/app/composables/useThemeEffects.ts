import { useIntervalFn, usePreferredDark } from "@vueuse/core";
import { useSettingsStore } from "~/stores/settings";

export function useThemeEffects() {
	const store = useSettingsStore();
	const appConfig = useAppConfig();
	const systemDark = usePreferredDark();
	const hour = ref(new Date().getHours());
	const artworkPalette = useArtworkPalette();
	const dark = computed(() =>
		store.settings.mode === "system"
			? systemDark.value
			: store.settings.mode === "time"
				? hour.value < 7 || hour.value >= 20
				: store.settings.mode === "dark",
	);

	watch(
		() => artworkPalette.value?.primary ?? store.settings.primary_color,
		(value) => {
			appConfig.ui.colors.primary = value;
		},
		{ immediate: true },
	);
	watch(
		() => artworkPalette.value?.neutral ?? store.settings.neutral_color,
		(value) => {
			appConfig.ui.colors.neutral = value;
		},
		{ immediate: true },
	);
	watch(
		() => store.icons,
		(icons) => {
			Object.assign(appConfig.ui.icons, icons);
		},
		{ immediate: true },
	);

	useHead(() => ({
		htmlAttrs: {
			class: dark.value ? "dark" : "light",
			style: `color-scheme: ${dark.value ? "dark" : "light"}`,
		},
		style: [
			{
				key: "appearance",
				textContent: `:root { --font-sans: '${store.settings.font_family}', sans-serif; font-size: ${{ sm: "14px", md: "16px", lg: "18px" }[store.settings.text_size]}; }`,
			},
		],
	}));

	useIntervalFn(() => {
		hour.value = new Date().getHours();
	}, 60_000);
}
