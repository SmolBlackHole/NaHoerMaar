// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

function youtubeMusicSearchUrl(query: string): string | null {
	const normalized = query.trim();
	return normalized
		? `https://music.youtube.com/search?q=${encodeURIComponent(normalized)}`
		: null;
}

export function youtubeMusicTrackUrl(
	title: string | null | undefined,
	artistNames: readonly string[] = [],
): string | null {
	return youtubeMusicSearchUrl([title, ...artistNames].filter(Boolean).join(" "));
}

export function youtubeMusicArtistUrl(artistNames: readonly string[] = []): string | null {
	return youtubeMusicSearchUrl(artistNames.join(", "));
}
