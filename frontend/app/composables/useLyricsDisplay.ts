export type LyricsMode = "synced" | "cinematic" | "edit" | "words" | "read";

export type LyricsEditVariant = "orbit" | "spiral" | "impact";

export const lyricsEditVariants: readonly LyricsEditVariant[] = ["orbit", "spiral", "impact"];

interface LyricsEditSelection {
	trackId: string | null;
	variant: LyricsEditVariant;
}

function randomVariant(except?: LyricsEditVariant): LyricsEditVariant {
	const choices = except
		? lyricsEditVariants.filter((variant) => variant !== except)
		: lyricsEditVariants;
	return choices[Math.floor(Math.random() * choices.length)] ?? "orbit";
}

export function useLyricsDisplay() {
	const mode = useState<LyricsMode>("player-lyrics-mode", () => "synced");
	const synchronized = useState<boolean | null>("player-lyrics-synchronized", () => null);
	const edit = useState<LyricsEditSelection>("player-lyrics-edit", () => ({
		trackId: null,
		variant: "orbit",
	}));
	if (!lyricsEditVariants.includes(edit.value.variant)) {
		edit.value = { ...edit.value, variant: "orbit" };
	}

	function selectTrack(trackId: string) {
		if (edit.value.trackId === trackId) return;
		synchronized.value = null;
		edit.value = {
			trackId,
			variant: randomVariant(edit.value.trackId ? edit.value.variant : undefined),
		};
	}

	function setSynchronized(value: boolean | null) {
		synchronized.value = value;
	}

	function selectEditVariant(trackId: string, variant: LyricsEditVariant) {
		edit.value = { trackId, variant };
	}

	function shuffleEditVariant(trackId: string) {
		edit.value = { trackId, variant: randomVariant(edit.value.variant) };
	}

	return {
		mode,
		synchronized,
		editVariant: computed(() => edit.value.variant),
		selectTrack,
		setSynchronized,
		selectEditVariant,
		shuffleEditVariant,
	};
}
