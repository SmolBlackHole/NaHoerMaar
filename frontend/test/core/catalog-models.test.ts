// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import { describe, expect, it } from "vitest";
import {
	artistNames,
	formatDuration,
	musicSource,
	type Track,
} from "../../app/core/models/catalog";

const video = "https://www.youtube.com/watch?v=Pqp9fDRp1lw";
const playlist = "https://www.youtube.com/playlist?list=PL12345678901234";

describe("catalog UI models", () => {
	it("distinguishes searches, videos and playlists without legacy helpers", () => {
		expect(musicSource("  Zara Larsson  ")).toEqual({
			kind: "search",
			query: "Zara Larsson",
		});
		expect(musicSource(video + "&list=PL12345678901234")).toEqual({
			kind: "video",
			url: video,
			playlist,
		});
		expect(musicSource(playlist)).toEqual({ kind: "playlist", url: playlist });
	});

	it("rejects unsupported and ambiguous links", () => {
		for (const value of [
			"",
			"https://example.org/watch?v=Pqp9fDRp1lw",
			"https://youtube.com.evil.test/watch?v=Pqp9fDRp1lw",
			"javascript:alert(1)",
			playlist + "&list=PLduplicate123",
		])
			expect(musicSource(value)).toEqual({ kind: "invalid" });
	});

	it("formats the catalog details used by result rows", () => {
		const track = {
			artists: [
				{ id: "one", name: "First" },
				{ id: "two", name: "Second" },
			],
		} as Track;
		expect(artistNames(track)).toBe("First, Second");
		expect(formatDuration(185)).toBe("3:05");
		expect(formatDuration(null)).toBe("");
	});
});
