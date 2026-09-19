import colors from "tailwindcss/colors";
import { colorNames, neutralNames } from "../config/theme";

export interface ArtworkPalette {
	primary: keyof typeof colorNames | null;
	neutral: keyof typeof neutralNames;
}

interface Oklab {
	l: number;
	a: number;
	b: number;
}

function linear(value: number): number {
	const channel = value / 255;
	return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
}

function oklab(red: number, green: number, blue: number): Oklab {
	const r = linear(red),
		g = linear(green),
		b = linear(blue);
	const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
	const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
	const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
	return {
		l: 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
		a: 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
		b: 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
	};
}

function swatch(name: keyof typeof colorNames | keyof typeof neutralNames): Oklab {
	const [lightness, chroma, hue] = colors[name][500].slice(6, -1).split(" ");
	const angle = ((parseFloat(hue!) || 0) * Math.PI) / 180;
	return {
		l: parseFloat(lightness!) / 100,
		a: Number(chroma) * Math.cos(angle),
		b: Number(chroma) * Math.sin(angle),
	};
}

const primaries = (Object.keys(colorNames) as (keyof typeof colorNames)[]).map((name) => ({
	name,
	color: swatch(name),
}));
const neutrals = (Object.keys(neutralNames) as (keyof typeof neutralNames)[]).map((name) => ({
	name,
	color: swatch(name),
}));

const MIN_CHROMA = 0.008;
const MIN_DOMINANT_SHARE = 0.1;

export function paletteFromPixels(pixels: Uint8ClampedArray): ArtworkPalette | null {
	const buckets = new Map<number, { count: number; a: number; b: number }>();
	let visible = 0;
	for (let i = 0; i + 3 < pixels.length; i += 4) {
		if (pixels[i + 3]! < 128) continue;
		visible++;
		const color = oklab(pixels[i]!, pixels[i + 1]!, pixels[i + 2]!);
		// Keep muted color fields: a high saturation cutoff leaves only bright edge artifacts.
		if (color.l < 0.18 || color.l > 0.95 || Math.hypot(color.a, color.b) < MIN_CHROMA) continue;
		// Group similar hues so highlights and shadows count toward the same cover color.
		const hue = (Math.atan2(color.b, color.a) + 2 * Math.PI) % (2 * Math.PI);
		const key = Math.round((hue / (2 * Math.PI)) * 24) % 24;
		const bucket = buckets.get(key) ?? { count: 0, a: 0, b: 0 };
		bucket.count++;
		bucket.a += color.a;
		bucket.b += color.b;
		buckets.set(key, bucket);
	}
	if (!visible) return null;
	const dominant = [...buckets.values()].sort((a, b) => b.count - a.count)[0];
	if (!dominant || dominant.count < visible * MIN_DOMINANT_SHARE)
		return { primary: null, neutral: "neutral" };
	const a = dominant.a / dominant.count;
	const b = dominant.b / dominant.count;
	const chroma = Math.hypot(a, b);
	// Match the accent by hue, independent of how dark the artwork is.
	const primary = primaries.reduce((best, candidate) => {
		const distance = (color: Oklab) => {
			const c = Math.hypot(color.a, color.b);
			return (a / chroma - color.a / c) ** 2 + (b / chroma - color.b / c) ** 2;
		};
		return distance(candidate.color) < distance(best.color) ? candidate : best;
	});
	// Desaturate the cover tint before finding the nearest existing gray palette.
	const tintScale = Math.min(chroma * 0.25, 0.045) / chroma;
	const neutral = neutrals.reduce((best, candidate) => {
		const distance = (color: Oklab) =>
			(a * tintScale - color.a) ** 2 + (b * tintScale - color.b) ** 2;
		return distance(candidate.color) < distance(best.color) ? candidate : best;
	});
	return { primary: primary.name, neutral: neutral.name };
}

export async function loadArtworkPalette(
	url: string,
	signal: AbortSignal,
): Promise<ArtworkPalette | null> {
	let bitmap: ImageBitmap | undefined;
	try {
		const response = await fetch(url, {
			signal: AbortSignal.any([signal, AbortSignal.timeout(10_000)]),
			credentials: "omit",
			referrerPolicy: "no-referrer",
		});
		if (!response.ok) return null;
		bitmap = await createImageBitmap(await response.blob());
		if (signal.aborted) return null;
		const canvas = document.createElement("canvas");
		canvas.width = canvas.height = 48;
		const context = canvas.getContext("2d", { willReadFrequently: true });
		if (!context) return null;
		context.drawImage(bitmap, 0, 0, 48, 48);
		return paletteFromPixels(context.getImageData(0, 0, 48, 48).data);
	} catch {
		return null;
	} finally {
		bitmap?.close();
	}
}
