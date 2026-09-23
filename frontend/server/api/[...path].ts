import { defineEventHandler, getRequestURL, proxyRequest } from "h3";

export default defineEventHandler(async (event) => {
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
	const abort = new AbortController();
	const closed = () => abort.abort();
	event.node.res.once("close", closed);
	try {
		return await proxyRequest(event, `${backendUrl}${event.path}`, {
			streamRequest: true,
			fetchOptions: {
				redirect: "manual",
				signal: abort.signal,
				headers: origin ? { origin } : undefined,
			},
		});
	} catch (error) {
		if (!abort.signal.aborted) throw error;
	} finally {
		event.node.res.off("close", closed);
	}
});
