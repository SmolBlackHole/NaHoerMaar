import {
	createError,
	defineEventHandler,
	getHeader,
	getRequestURL,
	readRawBody,
	sendProxy,
} from "h3";

const localHosts = new Set(["127.0.0.1", "localhost", "[::1]"]);
const routes: Record<string, RegExp> = {
	GET: /^\/api\/(state|channels|events)$/,
	POST: /^\/api\/(queue(?:\/clear|\/[a-f0-9-]{36}\/move)?|player\/(play|pause|skip|stop))$/,
	PUT: /^\/api\/(player\/(volume|seek)|voice\/channel)$/,
	DELETE: /^\/api\/(queue\/[a-f0-9-]{36}|voice\/channel)$/,
};

export function playerProxy(backendUrl: () => string) {
	return defineEventHandler(async (event) => {
		const url = getRequestURL(event, { xForwardedHost: false, xForwardedProto: false });
		const origin = getHeader(event, "origin");
		if (
			!localHosts.has(url.hostname) ||
			(origin && origin !== url.origin) ||
			getHeader(event, "sec-fetch-site") === "cross-site"
		)
			throw createError({ statusCode: 403, statusMessage: "Origin not allowed" });
		if (!routes[event.method]?.test(url.pathname))
			throw createError({ statusCode: 404, statusMessage: "Unknown player endpoint" });

		const target = new URL(url.pathname, backendUrl());
		const headers = new Headers({ origin: target.origin, "accept-encoding": "identity" });
		for (const name of ["content-type", "idempotency-key", "last-event-id", "accept"]) {
			const value = getHeader(event, name);
			if (value) headers.set(name, value);
		}
		const abort = new AbortController();
		const closed = () => abort.abort();
		event.node.res.once("close", closed);
		try {
			return await sendProxy(event, target.href, {
				fetch,
				fetchOptions: {
					method: event.method,
					headers,
					redirect: "manual",
					signal: abort.signal,
					body: ["POST", "PUT"].includes(event.method)
						? await readRawBody(event)
						: undefined,
				},
			});
		} catch (error) {
			if (abort.signal.aborted) return;
			if (event.node.res.headersSent) {
				event.node.res.destroy();
				return;
			}
			throw error;
		} finally {
			event.node.res.off("close", closed);
		}
	});
}
