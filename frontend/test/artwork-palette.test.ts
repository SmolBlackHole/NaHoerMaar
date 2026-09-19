import { afterEach, describe, expect, it, vi } from "vitest";
import { loadArtworkPalette, paletteFromPixels } from "../app/utils/artworkPalette";

function pixels(...groups: { rgb: number[]; count: number; alpha?: number }[]) {
	return new Uint8ClampedArray(
		groups.flatMap(({ rgb, count, alpha = 255 }) =>
			Array.from({ length: count }, () => [...rgb, alpha]).flat(),
		),
	);
}

describe("artwork palette", () => {
	it("matches the dominant hue instead of averaging opposing colors", () => {
		const image = pixels({ rgb: [220, 35, 35], count: 60 }, { rgb: [25, 80, 230], count: 40 });
		expect(paletteFromPixels(image)?.primary).toBe("red");
	});
	it("ignores black bars, white lettering and transparent pixels", () => {
		const cover = { rgb: [20, 180, 160], count: 100 };
		const padded = pixels(
			cover,
			{ rgb: [0, 0, 0], count: 300 },
			{ rgb: [255, 255, 255], count: 200 },
			{ rgb: [255, 0, 0], count: 500, alpha: 0 },
		);
		expect(paletteFromPixels(padded)).toEqual(paletteFromPixels(pixels(cover)));
		expect(paletteFromPixels(padded)?.primary).toBe("teal");
	});
	it("keeps a hue consistent across brighter and darker covers", () => {
		const bright = paletteFromPixels(pixels({ rgb: [240, 70, 25], count: 100 }));
		const dark = paletteFromPixels(pixels({ rgb: [120, 35, 12], count: 100 }));
		expect(dark?.primary).toBe(bright?.primary);
	});
	it("keeps the muted green atmosphere of Rapture instead of its blue glitch edges", () => {
		// Representative colors sampled from the reported thumbnail.
		const image = pixels(
			{ rgb: [23, 35, 30], count: 45 },
			{ rgb: [36, 50, 47], count: 15 },
			{ rgb: [57, 71, 112], count: 3 },
			{ rgb: [37, 50, 71], count: 2 },
			{ rgb: [0, 0, 0], count: 35 },
		);
		expect(paletteFromPixels(image)).toEqual({ primary: "emerald", neutral: "neutral" });
	});
	it("requires the winning color itself to occupy enough of the image", () => {
		const image = pixels(
			{ rgb: [110, 110, 110], count: 80 },
			{ rgb: [220, 35, 35], count: 7 },
			{ rgb: [25, 80, 230], count: 7 },
			{ rgb: [30, 200, 70], count: 6 },
		);
		expect(paletteFromPixels(image)).toEqual({ primary: null, neutral: "neutral" });
	});
	it("chooses warm and cool neutrals from the existing palettes", () => {
		expect(paletteFromPixels(pixels({ rgb: [225, 130, 30], count: 100 }))?.neutral).toBe(
			"stone",
		);
		expect(paletteFromPixels(pixels({ rgb: [30, 120, 240], count: 100 }))?.neutral).toBe(
			"slate",
		);
	});
	it("leaves the manual accent available for grayscale and tiny color noise", () => {
		const grayscale = { rgb: [110, 110, 110], count: 100 };
		const result = { primary: null, neutral: "neutral" };
		expect(paletteFromPixels(pixels(grayscale))).toEqual(result);
		expect(paletteFromPixels(pixels(grayscale, { rgb: [255, 0, 0], count: 2 }))).toEqual(
			result,
		);
		expect(paletteFromPixels(new Uint8ClampedArray())).toBeNull();
		expect(paletteFromPixels(pixels({ rgb: [20, 180, 160], count: 10, alpha: 0 }))).toBeNull();
	});
});

describe("artwork loading", () => {
	afterEach(() => vi.unstubAllGlobals());
	it("falls back on network, CORS and HTTP failures", async () => {
		vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
		expect(
			await loadArtworkPalette("https://example.com/cover.jpg", new AbortController().signal),
		).toBeNull();
		vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false }));
		expect(
			await loadArtworkPalette(
				"https://example.com/missing.jpg",
				new AbortController().signal,
			),
		).toBeNull();
	});
	it("releases decoded images when a newer track has canceled the request", async () => {
		const controller = new AbortController();
		const bitmap = { close: vi.fn() };
		vi.stubGlobal(
			"fetch",
			vi.fn().mockResolvedValue({ ok: true, blob: async () => new Blob() }),
		);
		vi.stubGlobal(
			"createImageBitmap",
			vi.fn().mockImplementation(async () => {
				controller.abort();
				return bitmap;
			}),
		);
		expect(
			await loadArtworkPalette("https://example.com/cover.jpg", controller.signal),
		).toBeNull();
		expect(bitmap.close).toHaveBeenCalledOnce();
	});
	it("samples a small canvas and releases the source image", async () => {
		const bitmap = { close: vi.fn() };
		const drawImage = vi.fn();
		const getImageData = vi
			.fn()
			.mockReturnValue({ data: pixels({ rgb: [20, 180, 160], count: 100 }) });
		const canvas = { width: 0, height: 0, getContext: () => ({ drawImage, getImageData }) };
		vi.stubGlobal(
			"fetch",
			vi.fn().mockResolvedValue({ ok: true, blob: async () => new Blob() }),
		);
		vi.stubGlobal("createImageBitmap", vi.fn().mockResolvedValue(bitmap));
		vi.stubGlobal("document", { createElement: () => canvas });
		expect(
			(
				await loadArtworkPalette(
					"https://example.com/cover.jpg",
					new AbortController().signal,
				)
			)?.primary,
		).toBe("teal");
		expect(drawImage).toHaveBeenCalledWith(bitmap, 0, 0, 48, 48);
		expect(getImageData).toHaveBeenCalledWith(0, 0, 48, 48);
		expect(bitmap.close).toHaveBeenCalledOnce();
	});
});
