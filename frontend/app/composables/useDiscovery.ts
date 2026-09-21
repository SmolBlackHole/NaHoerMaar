import {
	musicSource,
	selectedTrackIds,
	reconcileSelection,
	importCounts,
	type CatalogTrack,
} from "../../shared/catalog";
import { computed, ref, watch } from "vue";
import { useCatalog } from "./useCatalog";
import { useRadioPreviewStore } from "../stores/radioPreview";
import { usePlayerStore } from "../stores/player";
import { useProfileStore } from "../stores/profile";

/** Discovery workflow; focus and scroll belong to the panel component. */
export function useDiscovery() {
	const player = usePlayerStore();
	const radio = useRadioPreviewStore();
	const profile = useProfileStore();
	const library = useCatalog();
	watch(
		() => [profile.status, profile.profile?.id ?? null] as const,
		([status, id], [, previousId]) => {
			if (status !== "authenticated" || id !== previousId) {
				library.dispose();
				panelOpen.value = false;
				selected.value = new Set();
				source.value = inputError.value = "";
			}
		},
		{ flush: "sync" },
	);
	const {
		results,
		query,
		searchSource,
		searching,
		loadingMore,
		nextOffset,
		searchError,
		preview,
		previewUrl,
		previewError,
		previewPending,
		searchUpdate,
		previewUpdate,
		refreshing,
		refreshError,
		searchExpired,
	} = library;
	const source = ref("");
	const inputError = ref("");
	const toast = useToast();
	const panelOpen = ref(false);
	const view = ref("search");
	const selected = ref(new Set<number>());
	const importing = ref(false);
	const skipDuplicates = ref(false);
	const parsed = computed(() => musicSource(source.value));
	const choices = computed(
		() => preview.value?.entries.filter((item) => item.source_url && !item.unavailable) ?? [],
	);
	const selection = computed(() =>
		selectedTrackIds(preview.value?.entries ?? [], selected.value),
	);
	const counts = computed(() =>
		importCounts(selection.value, player.snapshot, skipDuplicates.value),
	);
	const submitting = computed(
		() =>
			searching.value ||
			previewPending.value ||
			(parsed.value.kind === "video" && player.isAdding(parsed.value.url)),
	);
	const loadingPlaylist = computed(() => !previewError.value && previewPending.value);
	watch(
		() => radio.version,
		() => {
			if (!radio.source) return;
			view.value = "radio";
			panelOpen.value = true;
		},
	);
	async function startRadio() {
		if (!radio.preview) return;
		if (await player.startRadio(radio.preview.seed, player.snapshot?.radio.generation ?? null))
			panelOpen.value = false;
	}

	watch(searchSource, () => {
		if (query.value) {
			view.value = "search";
			panelOpen.value = true;
			void library.search(query.value);
		}
	});

	watch(source, () => {
		inputError.value = "";
	});
	watch(
		() => preview.value,
		(value, previous) => {
			if (value && !previous)
				selected.value = new Set(choices.value.map((item) => item.index));
		},
	);

	async function openPlaylist(url: string) {
		panelOpen.value = true;
		const preserve = previewUrl.value === url && !!preview.value;
		if (!preserve) {
			selected.value = new Set();
		}
		view.value = "playlist";
		library.clearSearch();
		await library.openPreview(url, preserve);
	}

	async function submit() {
		const value = parsed.value;
		inputError.value = "";
		if (value.kind === "invalid") {
			inputError.value =
				"Enter a YouTube link or search for a title or artist (up to 200 characters).";
			return;
		}
		if (value.kind === "playlist") {
			await openPlaylist(value.url);
			return;
		}
		if (value.kind === "search") {
			view.value = "search";
			panelOpen.value = true;
			library.closePreview();
			await library.search(value.query);
			return;
		}
		const submitted = source.value;
		if (await player.add(value.url)) {
			if (source.value === submitted) source.value = "";
		}
	}

	async function addResult(item: CatalogTrack) {
		if (!item.track_id || item.unavailable) return;
		await player.addMany([item.track_id]);
	}

	function toggle(index: number) {
		const next = new Set(selected.value);
		if (next.has(index)) next.delete(index);
		else next.add(index);
		selected.value = next;
	}

	function selectAll() {
		selected.value =
			selection.value.length === choices.value.length
				? new Set()
				: new Set(choices.value.map((item) => item.index));
	}

	async function importSelection() {
		if (importing.value || !preview.value || !selection.value.length) return;
		importing.value = true;
		try {
			const trackIds = selectedTrackIds(preview.value.entries, selected.value);
			const importedPreview = preview.value.version;
			const importedSelection = selected.value;
			const skip = skipDuplicates.value;
			if (await player.addMany(trackIds, skip)) {
				if (
					preview.value?.version === importedPreview &&
					selected.value === importedSelection
				)
					selected.value = new Set();
				if (preview.value?.version === importedPreview) panelOpen.value = false;
			}
		} finally {
			importing.value = false;
		}
	}

	function applyUpdate() {
		if (view.value === "playlist" && previewUpdate.value) {
			selected.value = reconcileSelection(
				preview.value?.entries ?? [],
				previewUpdate.value.entries,
				selected.value,
			);
			library.applyPreviewUpdate();
			toast.add({
				title: "Playlist updated",
				description: "New or unmatched tracks are left unselected.",
			});
		} else library.applySearchUpdate();
	}
	watch(
		[panelOpen, view],
		([open, currentView]) => {
			library.setActive(open && currentView !== "radio");
			if (!open && radio.loading) radio.dispose();
		},
		{ flush: "sync" },
	);
	return {
		player,
		radio,
		library,
		results,
		query,
		searchSource,
		searching,
		loadingMore,
		nextOffset,
		searchError,
		preview,
		previewUrl,
		previewError,
		previewPending,
		searchUpdate,
		previewUpdate,
		refreshing,
		refreshError,
		searchExpired,
		source,
		inputError,
		panelOpen,
		view,
		selected,
		importing,
		skipDuplicates,
		parsed,
		choices,
		selection,
		counts,
		submitting,
		loadingPlaylist,
		startRadio,
		openPlaylist,
		submit,
		addResult,
		toggle,
		selectAll,
		importSelection,
		applyUpdate,
	};
}
