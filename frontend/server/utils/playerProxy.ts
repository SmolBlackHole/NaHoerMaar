import {
	createError,
	defineEventHandler,
	getHeader,
	getRequestURL,
	readRawBody,
	sendProxy,
} from "h3";

const routes: Record<string, RegExp> = {
	GET: /^\/api\/(auth\/(session|discord(?:\/callback)?)|state|channels|events|catalog\/search|youtube\/playlists\/[a-f0-9-]{36})$/,
	POST: /^\/api\/(auth\/logout|queue(?:\/clear|\/batch|\/undo|\/[a-f0-9-]{36}\/move)?|player\/(play|pause|skip|stop)|youtube\/playlists)$/,
	PUT: /^\/api\/(profile(?:\/appearance)?|player\/(volume|seek)|voice\/channel)$/,
	DELETE: /^\/api\/(queue\/[a-f0-9-]{36}|voice\/channel|youtube\/playlists\/[a-f0-9-]{36})$/,
};

export function playerProxy(backendUrl: () => string, publicOrigin: () => string) {
	return defineEventHandler(async (event) => {
		const url = getRequestURL(event, { xForwardedHost: false, xForwardedProto: false });
		const origin = getHeader(event, "origin");
		const expected = new URL(publicOrigin());
		const authNavigation =
			event.method === "GET" &&
			["/api/auth/discord", "/api/auth/discord/callback"].includes(url.pathname);
		if (
			url.host !== expected.host ||
			(origin && origin !== expected.origin) ||
			(!authNavigation && getHeader(event, "sec-fetch-site") === "cross-site")
		)
			throw createError({ statusCode: 403, statusMessage: "Origin not allowed" });
		if (!routes[event.method]?.test(url.pathname))
			throw createError({ statusCode: 404, statusMessage: "Unknown player endpoint" });

		const target = new URL(url.pathname, backendUrl());
		if (url.pathname === "/api/catalog/search") {
			for (const name of ["q", "offset", "source", "snapshot_id"])
				for (const value of url.searchParams.getAll(name))
					target.searchParams.append(name, value);
		}
		if (url.pathname === "/api/auth/discord/callback") {
			for (const name of ["state", "code", "error"])
				for (const value of url.searchParams.getAll(name))
					target.searchParams.append(name, value);
		}
		const headers = new Headers({ "accept-encoding": "identity" });
		for (const name of [
			"origin",
			"content-type",
			"idempotency-key",
			"last-event-id",
			"accept",
			"x-csrf-token",
		]) {
			const value = getHeader(event, name);
			if (value) headers.set(name, value);
		}
		const abort = new AbortController();
		const cookies = (getHeader(event, "cookie") ?? "")
			.split(";")
			.map((part) => part.trim())
			.filter((part) => /^(nahormaar_session|nahormaar_login)=/.test(part));
		if (cookies.length) headers.set("cookie", cookies.join("; "));
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
