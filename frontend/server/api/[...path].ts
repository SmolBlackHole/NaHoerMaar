import { defineEventHandler, proxyRequest } from "h3";

export default defineEventHandler(async (event) => {
	const backendUrl = useRuntimeConfig(event).backendUrl.replace(/\/+$/, "");
	const abort = new AbortController();
	const closed = () => abort.abort();
	event.node.res.once("close", closed);
	try {
		return await proxyRequest(event, `${backendUrl}${event.path}`, {
			streamRequest: true,
			fetchOptions: { redirect: "manual", signal: abort.signal },
		});
	} catch (error) {
		if (!abort.signal.aborted) throw error;
	} finally {
		event.node.res.off("close", closed);
	}
});
