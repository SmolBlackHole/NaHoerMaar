import { randomUUID } from "node:crypto";
import { defineEventHandler, getRequestURL, proxyRequest, setResponseHeader } from "h3";

const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;

export default defineEventHandler(async (event) => {
	const started = performance.now();
	const config = useRuntimeConfig(event);
	const backendUrl = config.backendUrl.replace(/\/+$/, "");
	const publicOrigin = new URL(config.publicOrigin).origin;
	const directOrigin = getRequestURL(event, {
		xForwardedHost: false,
		xForwardedProto: false,
	}).origin;
	const forwardedOrigin = getRequestURL(event, {
		xForwardedHost: true,
		xForwardedProto: true,
	}).origin;
	const requestOrigin = event.node.req.headers.origin;
	const requestIdHeader = event.node.req.headers["x-request-id"];
	const requestId =
		typeof requestIdHeader === "string" && UUID.test(requestIdHeader)
			? requestIdHeader.toLowerCase()
			: randomUUID();
	setResponseHeader(event, "x-request-id", requestId);
	const tunnelRewroteToLoopback =
		forwardedOrigin === publicOrigin &&
		requestOrigin !== undefined &&
		/^https?:\/\/(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?$/.test(requestOrigin);
	const origin =
		requestOrigin &&
		([publicOrigin, directOrigin, forwardedOrigin].includes(requestOrigin) ||
			tunnelRewroteToLoopback)
			? publicOrigin
			: requestOrigin;
	const originRewritten = origin !== requestOrigin;
	const method = event.node.req.method || "GET";
	const path = getRequestURL(event).pathname;
	const abort = new AbortController();
	const closed = () => abort.abort();
	let outcome = "completed";
	let errorName: string | undefined;
	event.node.res.once("close", closed);
	try {
		return await proxyRequest(event, `${backendUrl}${event.path}`, {
			streamRequest: true,
			fetchOptions: {
				redirect: "manual",
				signal: abort.signal,
				headers: {
					...(origin ? { origin } : {}),
					"x-request-id": requestId,
				},
			},
		});
	} catch (error) {
		outcome = abort.signal.aborted ? "client_aborted" : "failed";
		errorName = error instanceof Error ? error.name : typeof error;
		if (!abort.signal.aborted) throw error;
	} finally {
		event.node.res.off("close", closed);
		const message =
			`nitro.proxy.${outcome} trace_id=${requestId} method=${method} ` +
			`path=${path} status=${event.node.res.statusCode} ` +
			`duration_ms=${(performance.now() - started).toFixed(1)} ` +
			`origin_rewritten=${originRewritten}`;
		if (outcome === "failed") console.error(`${message} error=${errorName}`);
		else console.info(message);
	}
});
