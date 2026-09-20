import { describe, expect, it } from "vitest";
import { parseProfile, randomAvatar } from "../shared/profile";
import avatars from "../../backend/src/nahormaar_backend/avatars.json";
import {
	groupHistory,
	listeningStats,
	trackArtistUrl,
	trackArtwork,
	type QueueEntry,
} from "../shared/player";

const entry: QueueEntry = {
	id: "3aaf410e-77dd-4719-9299-a4cedbbce459",
	source_url: "https://music.youtube.com/watch?v=Pqp9fDRp1lw",
	video_id: "Pqp9fDRp1lw",
	title: "Song",
	artist: "Artist",
	uploader: "Artist - Topic",
	uploader_url: "https://www.youtube.com/channel/example",
	duration_seconds: 180,
	thumbnail_url: null,
	added_by: null,
	origin: "manual",
};

describe("browser profile", () => {
	it("restores a valid profile and ignores malformed stored data", () => {
		const profile = { id: entry.id, name: "  Alice  ", avatar: avatars[0]! };
		expect(parseProfile(JSON.stringify(profile))).toEqual({ ...profile, name: "Alice" });
		for (const raw of [
			null,
			"broken",
			"null",
			"{}",
			JSON.stringify({ ...profile, avatar: "../outside" }),
			JSON.stringify({ ...profile, name: " " }),
			JSON.stringify({ ...profile, name: "x".repeat(33) }),
		])
			expect(parseProfile(raw)).toBeNull();
	});
	it("chooses a shipped avatar different from the current one", () => {
		const next = randomAvatar(avatars[0]);
		expect(avatars).toContain(next);
		expect(next).not.toBe(avatars[0]);
	});
});

describe("track metadata", () => {
	it("links matching artists to the channel, and other artists to music search", () => {
		expect(trackArtistUrl(entry)).toBe(entry.uploader_url);
		expect(trackArtistUrl({ ...entry, artist: "Someone & Else" })).toBe(
			"https://music.youtube.com/search?q=Someone%20%26%20Else",
		);
		expect(trackArtistUrl({ ...entry, uploader_url: "javascript:alert(1)" })).toBe(
			"https://music.youtube.com/search?q=Artist",
		);
		expect(
			trackArtistUrl({ ...entry, artist: null, uploader: null, uploader_url: null }),
		).toBeNull();
	});
	it("falls back to a video thumbnail for missing or invalid artwork URLs", () => {
		expect(trackArtwork(entry)).toBe("https://i.ytimg.com/vi/Pqp9fDRp1lw/hqdefault.jpg");
		expect(trackArtwork({ ...entry, thumbnail_url: "javascript:alert(1)" })).toBe(
			trackArtwork(entry),
		);
		expect(trackArtwork(null)).toBeNull();
	});
});

describe("overview", () => {
	it("counts starts separately from songs and restricts them to the selected local dates", () => {
		const now = new Date(2026, 8, 19, 12);
		const history = [
			{ id: "1", entry, played_at: new Date(2026, 8, 19, 11).toISOString() },
			{
				id: "2",
				entry: { ...entry, source_url: "https://youtu.be/Pqp9fDRp1lw" },
				played_at: new Date(2026, 8, 13, 0).toISOString(),
			},
			{ id: "3", entry, played_at: new Date(2026, 8, 12, 23, 59).toISOString() },
			{ id: "4", entry, played_at: new Date(2026, 8, 20).toISOString() },
		];
		const stats = listeningStats(history, 7, now);
		expect(stats.starts).toBe(2);
		expect(stats.tracks).toBe(1);
		expect(stats.artists).toBe(1);
		expect(stats.buckets.map((day) => day.count)).toEqual([1, 0, 0, 0, 0, 0, 1]);
		expect(stats.topArtists).toEqual([["Artist", 2]]);
	});
});

describe("recently played", () => {
	it("combines URL variants and keeps the latest entry and its metadata", () => {
		const older = { id: "old", entry, played_at: "2026-09-19T12:00:00Z" };
		const latest = {
			id: "latest",
			entry: {
				...entry,
				id: "new-queue-entry",
				title: "Updated title",
				video_id: null,
				source_url: "https://youtu.be/Pqp9fDRp1lw?t=10",
			},
			played_at: "2026-09-19T14:00:00Z",
		};
		const history = [older, latest];
		expect(groupHistory(history)).toEqual([{ ...latest, play_count: 2 }]);
		expect(history).toEqual([older, latest]);
		expect(older).not.toHaveProperty("play_count");
	});
	it("keeps different videos with the same title separate and sorts by latest play", () => {
		const older = { id: "old", entry, played_at: "2026-09-19T12:00:00Z" };
		const different = {
			id: "other",
			entry: {
				...entry,
				video_id: "bWHJbIm1TAA",
				source_url: "https://www.youtube.com/watch?v=bWHJbIm1TAA",
			},
			played_at: "2026-09-19T13:00:00Z",
		};
		expect(groupHistory([older, different])).toEqual([
			{ ...different, play_count: 1 },
			{ ...older, play_count: 1 },
		]);
		expect(groupHistory([])).toEqual([]);
	});
});
