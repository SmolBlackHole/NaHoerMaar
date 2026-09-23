import { defineEventHandler, getHeader, getRequestURL, sendRedirect } from "h3";

const loopbackHosts = new Set(["localhost", "127.0.0.1", "[::1]"]);

export default defineEventHandler((event) => {
	if (!["GET", "HEAD"].includes(event.method)) return;
	const navigation =
		getHeader(event, "sec-fetch-mode") === "navigate" ||
		getHeader(event, "accept")?.includes("text/html");
	if (!navigation) return;

	const requested = getRequestURL(event, {
		xForwardedHost: false,
		xForwardedProto: false,
	});
	const canonical = new URL(useRuntimeConfig(event).publicOrigin);
	if (
		requested.origin === canonical.origin ||
		requested.protocol !== canonical.protocol ||
		requested.port !== canonical.port ||
		!loopbackHosts.has(requested.hostname) ||
		!loopbackHosts.has(canonical.hostname)
	)
		return;

	canonical.pathname = requested.pathname;
	canonical.search = requested.search;
	return sendRedirect(event, canonical.href, 307);
});
