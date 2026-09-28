// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { components } from "../api/schema.generated";

type Schema = components["schemas"];

export type Lyrics = Schema["LyricsView"];
export type LyricLine = Schema["LyricLineView"];

const MIN_PRESENTATION_SECONDS = 2.4;
const MAX_PRESENTATION_SECONDS = 8.5;
const CONTINUOUS_LINE_GRACE_SECONDS = 1.25;

export function lyricPresentationEnd(
	line: LyricLine,
	nextLineStartSeconds: number | null,
): number | null {
	if (line.start_seconds === null) return null;
	const words = line.text.trim().split(/\s+/).filter(Boolean);
	const estimatedDuration = Math.min(
		MAX_PRESENTATION_SECONDS,
		Math.max(
			MIN_PRESENTATION_SECONDS,
			1.4 + words.length * 0.44 + line.text.trim().length * 0.018,
		),
	);
	const timelineEnd = line.end_seconds ?? nextLineStartSeconds;
	if (
		timelineEnd !== null &&
		timelineEnd - line.start_seconds <= estimatedDuration + CONTINUOUS_LINE_GRACE_SECONDS
	) {
		return timelineEnd;
	}
	return line.start_seconds + estimatedDuration;
}

export function activeLyricLine(lines: LyricLine[], positionSeconds: number): number | null {
	let active: number | null = null;
	for (const [index, line] of lines.entries()) {
		if (line.start_seconds === null || line.start_seconds > positionSeconds) continue;
		const nextLineStart = lines[index + 1]?.start_seconds ?? null;
		const presentationEnd = lyricPresentationEnd(line, nextLineStart);
		active = presentationEnd !== null && positionSeconds < presentationEnd ? index : null;
	}
	return active;
}

export function activeLyricWord(
	line: LyricLine,
	positionSeconds: number,
	nextLineStartSeconds: number | null,
): number | null {
	if (line.start_seconds === null || positionSeconds < line.start_seconds) return null;
	const words = line.text.trim().split(/\s+/).filter(Boolean);
	if (words.length === 0) return null;

	const estimatedEnd = lyricPresentationEnd(line, nextLineStartSeconds) ?? line.start_seconds;
	const duration = Math.max(estimatedEnd - line.start_seconds, words.length * 0.12);
	const progress = Math.min(
		0.999_999,
		Math.max(0, (positionSeconds - line.start_seconds) / duration),
	);
	return Math.min(words.length - 1, Math.floor(progress * words.length));
}
