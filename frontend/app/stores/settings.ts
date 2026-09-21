import { computed, reactive, ref, watch, onScopeDispose } from "vue";
import { defineStore } from "pinia";
import { iconMaps } from "../config/icons";
import { useProfileStore } from "./profile";
import { useRepositories } from "../repositories";
import { defaultAppearance, type Appearance } from "../../shared/appearance";

export type ColorModePreference = Appearance["mode"];
export type TextSize = Appearance["textSize"];

export const useSettingsStore = defineStore("settings", () => {
	const settings = reactive<Appearance>({ ...defaultAppearance });
	const profile = useProfileStore();
	const repository = useRepositories().account;
	const error = ref("");
	let account: string | null = null;
	let generation = 0;
	let applying = false;
	let dirty = false;
	let remote = "";
	let timer: ReturnType<typeof setTimeout> | undefined;
	let active: AbortController | undefined;

	function apply(value: Appearance) {
		applying = true;
		Object.assign(settings, value);
		applying = false;
	}
	function bind(id: string | null, value: Appearance | null) {
		const next = JSON.stringify(value ?? defaultAppearance);
		if (id !== account) {
			generation++;
			clearTimeout(timer);
			active?.abort();
			active = undefined;
			dirty = false;
			error.value = "";
			account = id;
			apply(value ?? defaultAppearance);
		} else if (next !== remote && !dirty && !active) {
			apply(value ?? defaultAppearance);
		}
		remote = next;
	}
	async function save() {
		clearTimeout(timer);
		if (!account || active || !dirty) return;
		const version = generation;
		const body = JSON.stringify(settings);
		const controller = new AbortController();
		active = controller;
		error.value = "";
		try {
			await repository.saveAppearance(JSON.parse(body) as Appearance, controller.signal);
			if (version !== generation) return;
			dirty = JSON.stringify(settings) !== body;
		} catch {
			if (version === generation && !controller.signal.aborted)
				error.value =
					"Couldn't save your appearance. Your changes only apply here until you retry.";
		} finally {
			if (version === generation) {
				active = undefined;
				if (dirty && !error.value) void save();
			}
		}
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
		generation++;
		clearTimeout(timer);
		active?.abort();
		stop();
	}

	watch(
		() => [profile.profile?.id ?? null, profile.appearance] as const,
		([id, appearance]) => bind(id, appearance),
		{ immediate: true, flush: "sync" },
	);
	onScopeDispose(dispose);
	const icons = computed(() => iconMaps[settings.iconSet]);
	return { settings, icons, error, retry: save };
});
