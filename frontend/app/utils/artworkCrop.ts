export interface ArtworkCrop {
	ratio: number;
	width: number;
	height: number;
}

// Only trim substantial, matching, nearly uniform bars on opposite edges.
export function artworkCropFromPixels(
	{ data, width, height }: Pick<ImageData, "data" | "width" | "height">,
	imageRatio: number,
): ArtworkCrop | null {
	function border(axis: "x" | "y") {
		const length = axis === "x" ? width : height;
		const cross = axis === "x" ? height : width;
		const offset = (along: number, across: number) =>
			(axis === "x" ? across * width + along : along * width + across) * 4;
		function uniform(along: number) {
			let matches = 0;
			for (let across = 0; across < cross; across++) {
				const pixel = offset(along, across);
				const reference = offset(0, across);
				if (
					data[pixel + 3]! >= 250 &&
					[0, 1, 2].every(
						(channel) =>
							Math.abs(data[pixel + channel]! - data[reference + channel]!) <= 12,
					)
				)
					matches++;
			}
			return matches >= cross * 0.98;
		}
		const limit = Math.floor(length * 0.32);
		let start = 0;
		let end = 0;
		while (start < limit && uniform(start)) start++;
		while (end < limit && uniform(length - end - 1)) end++;
		if (
			start === limit ||
			end === limit ||
			Math.min(start, end) < length * 0.08 ||
			Math.abs(start - end) > 2
		)
			return 0;
		return Math.min(start, end) / length;
	}
	const horizontal = border("x");
	const vertical = border("y");
	if (!horizontal && !vertical) return null;
	return { ratio: imageRatio, width: 1 - 2 * horizontal, height: 1 - 2 * vertical };
}

export async function loadArtworkCrop(
	url: string,
	signal: AbortSignal,
): Promise<ArtworkCrop | null> {
	let bitmap: ImageBitmap | undefined;
	try {
		const source = new URL(url);
		if (source.hostname !== "i.ytimg.com" || source.search) return null;
		const response = await fetch(`/artwork${source.pathname}`, {
			signal: AbortSignal.any([signal, AbortSignal.timeout(10_000)]),
			credentials: "omit",
			referrerPolicy: "no-referrer",
		});
		if (!response.ok) return null;
		bitmap = await createImageBitmap(await response.blob());
		if (signal.aborted) return null;
		const canvas = document.createElement("canvas");
		canvas.width = canvas.height = 128;
		const context = canvas.getContext("2d", { willReadFrequently: true });
		if (!context) return null;
		context.drawImage(bitmap, 0, 0, 128, 128);
		return artworkCropFromPixels(
			context.getImageData(0, 0, 128, 128),
			bitmap.width / bitmap.height,
		);
	} catch {
		return null;
	} finally {
		bitmap?.close();
	}
}
