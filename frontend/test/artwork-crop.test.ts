import { describe, expect, it } from "vitest";
import { artworkCropFromPixels } from "../app/utils/artworkCrop";

function image(bars: { left?: number; right?: number; top?: number; bottom?: number } = {}) {
	const width = 128;
	const height = 128;
	const data = new Uint8ClampedArray(width * height * 4);
	for (let y = 0; y < height; y++) {
		for (let x = 0; x < width; x++) {
			const border =
				x < (bars.left ?? 0) ||
				x >= width - (bars.right ?? 0) ||
				y < (bars.top ?? 0) ||
				y >= height - (bars.bottom ?? 0);
			data.set(
				border ? [38, 38, 38, 255] : [100 + (x % 80), 60 + (y % 80), 180, 255],
				(y * width + x) * 4,
			);
		}
	}
	return { data, width, height };
}

describe("artwork framing", () => {
	it("fills from the square artwork inside a padded widescreen thumbnail", () => {
		expect(artworkCropFromPixels(image({ left: 28, right: 28 }), 16 / 9)).toEqual({
			ratio: 16 / 9,
			width: 9 / 16,
			height: 1,
		});
	});
	it("recognizes horizontal letterboxing without forcing a square crop", () => {
		expect(artworkCropFromPixels(image({ top: 16, bottom: 16 }), 4 / 3)).toEqual({
			ratio: 4 / 3,
			width: 1,
			height: 0.75,
		});
	});
	it("recognizes matching sidebars with a vertical gray gradient", () => {
		const padded = image({ left: 28, right: 28 });
		for (let y = 0; y < padded.height; y++) {
			for (let x = 0; x < padded.width; x++) {
				if (x < 28 || x >= 100) {
					const shade = 30 + y / 2;
					padded.data.set([shade, shade, shade, 255], (y * padded.width + x) * 4);
				}
			}
		}
		expect(artworkCropFromPixels(padded, 16 / 9)?.width).toBe(9 / 16);
	});
	it("trims colored padding around album artwork", () => {
		const padded = image({ left: 28, right: 28 });
		for (let y = 0; y < padded.height; y++) {
			for (let x = 0; x < padded.width; x++) {
				if (x < 28 || x >= 100)
					padded.data.set([125, 90, 7, 255], (y * padded.width + x) * 4);
			}
		}
		expect(artworkCropFromPixels(padded, 16 / 9)?.width).toBe(9 / 16);
	});
	it("keeps full-bleed art, asymmetric margins and flat images intact", () => {
		expect(artworkCropFromPixels(image(), 16 / 9)).toBeNull();
		expect(artworkCropFromPixels(image({ left: 28, right: 12 }), 16 / 9)).toBeNull();
		expect(artworkCropFromPixels(image({ left: 128 }), 16 / 9)).toBeNull();
	});
});
