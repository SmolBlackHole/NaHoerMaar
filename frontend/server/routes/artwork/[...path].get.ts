import { createError, defineEventHandler, getRequestURL, setHeader } from "h3";

// Public thumbnails only, so the browser can inspect their embedded borders.
export default defineEventHandler(async (event) => {
	const url = getRequestURL(event);
	const match =
		/^\/artwork\/(vi(?:_webp)?\/[\w-]{11}\/(?:maxresdefault|sddefault|hqdefault|mqdefault|default)\.(?:jpg|webp))$/.exec(
			url.pathname,
		);
	if (!match || url.search) throw createError({ statusCode: 404 });
	const response = await fetch(`https://i.ytimg.com/${match[1]}`, {
		redirect: "error",
		signal: AbortSignal.timeout(8000),
	});
	const type = response.headers.get("content-type");
	if (!response.ok || !response.body || !["image/jpeg", "image/webp"].includes(type ?? "")) {
		await response.body?.cancel();
		throw createError({ statusCode: response.status === 404 ? 404 : 502 });
	}
	const reader = response.body.getReader();
	const chunks: Uint8Array[] = [];
	let bytes = 0;
	try {
		while (true) {
			const { done, value } = await reader.read();
			if (done) break;
			bytes += value.byteLength;
			if (bytes > 2 * 1024 * 1024) throw createError({ statusCode: 502 });
			chunks.push(value);
		}
	} finally {
		await reader.cancel();
	}
	setHeader(event, "Content-Type", type!);
	setHeader(event, "Cache-Control", "public, max-age=3600");
	setHeader(event, "X-Content-Type-Options", "nosniff");
	return Buffer.concat(chunks);
});
