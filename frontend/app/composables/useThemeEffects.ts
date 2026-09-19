import { useIntervalFn } from "@vueuse/core";
import { useSettingsStore } from "~/stores/settings";

export function useThemeEffects() {
	const store = useSettingsStore();
	const appConfig = useAppConfig();
	const colorMode = useColorMode();

	function applyMode() {
		const hour = new Date().getHours();
		colorMode.preference =
			store.settings.mode === "time"
				? hour >= 7 && hour < 20
					? "light"
					: "dark"
				: store.settings.mode;
	}

	onMounted(() => {
		watch(() => store.settings.mode, applyMode, { immediate: true });
	});

	watch(
		() => store.settings.primaryColor,
		(value) => {
			appConfig.ui.colors.primary = value;
		},
		{ immediate: true },
	);
	watch(
		() => store.settings.neutralColor,
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
		style: [
			{
				key: "appearance",
				textContent: `:root { --font-sans: '${store.settings.fontFamily}', sans-serif; font-size: ${{ sm: "14px", md: "16px", lg: "18px" }[store.settings.textSize]}; }`,
			},
		],
	}));

	useIntervalFn(() => {
		if (store.settings.mode === "time") applyMode();
	}, 60_000);
}
