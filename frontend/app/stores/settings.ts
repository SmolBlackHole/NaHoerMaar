import { computed, onScopeDispose, reactive, ref, watch } from "vue";
import { defineStore } from "pinia";
import { defaultAppearance, type Appearance } from "../core/models/account";
import { iconMaps } from "../config/icons";

export type ColorModePreference = Appearance["mode"];
export type TextSize = Appearance["text_size"];

export const useSettingsStore = defineStore("settings", () => {
	const settings = reactive<Appearance>({ ...defaultAppearance });
	const profile = useNuxtApp().$backendCore.stores.useProfileStore();
	const error = ref("");
	let account: string | null = null;
	let applying = false;
	let dirty = false;
	let remote = "";
	let timer: ReturnType<typeof setTimeout> | undefined;
	let saving = false;

	function apply(value: Appearance) {
		applying = true;
		Object.assign(settings, value);
		applying = false;
	}
	function bind(id: string | null, value: Appearance | null) {
		const next = JSON.stringify(value ?? defaultAppearance);
		if (id !== account) {
			clearTimeout(timer);
			dirty = false;
			error.value = "";
			account = id;
			apply(value ?? defaultAppearance);
		} else if (next !== remote && !dirty && !saving) {
			apply(value ?? defaultAppearance);
		}
		remote = next;
	}
	async function save() {
		clearTimeout(timer);
		if (!account || saving || !dirty) return;
		const body = { ...settings };
		saving = true;
		error.value = "";
		const updated = await profile.updateAppearance(body);
		saving = false;
		if (!updated) {
			error.value =
				"Couldn't save your appearance. Your changes only apply here until you retry.";
			return;
		}
		remote = JSON.stringify(updated.appearance);
		dirty = JSON.stringify(settings) !== JSON.stringify(body);
		if (dirty) void save();
	}
	const stop = watch(
		settings,
		() => {
			if (applying || !account) return;
			dirty = true;
			clearTimeout(timer);
			timer = setTimeout(() => void save(), 400);
		},
		{ deep: true, flush: "sync" },
	);
	function dispose() {
		clearTimeout(timer);
		stop();
	}

	watch(
		() => [profile.profile?.id ?? null, profile.profile?.appearance ?? null] as const,
		([id, appearance]) => bind(id, appearance),
		{ immediate: true, flush: "sync" },
	);
	onScopeDispose(dispose);
	const icons = computed(() => iconMaps[settings.icon_set]);
	return { settings, icons, error, retry: save };
});
