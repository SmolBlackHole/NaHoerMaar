import { ref, watch } from "vue";
import { defaultAppearance, type Appearance } from "../../shared/appearance";

export function createAppearanceSync(settings: Appearance, request: typeof fetch) {
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
			const response = await request("/api/profile/appearance", {
				method: "PUT",
				headers: { "Content-Type": "application/json" },
				body,
				signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15_000)]),
			});
			if (version !== generation) return;
			if (!response.ok) throw new Error("save failed");
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
	return { bind, retry: save, error, dispose };
}
