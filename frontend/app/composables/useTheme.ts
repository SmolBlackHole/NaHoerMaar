import { storeToRefs } from "pinia";
import { useSettingsStore } from "~/stores/settings";

export function useTheme() {
	const store = useSettingsStore();
	const { icons } = storeToRefs(store);
	return { settings: store.settings, icons };
}
