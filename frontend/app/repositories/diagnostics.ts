import type { components } from "../../shared/api.generated";
import type { HttpTransport } from "./transport";

export function createDiagnosticsRepository(json: HttpTransport) {
	return {
		logs: (after?: number, signal?: AbortSignal) =>
			json<components["schemas"]["LogsView"]>(
				`/api/diagnostics/logs${after === undefined ? "" : `?after=${after}`}`,
				{ signal, timeoutMs: 10_000 },
			),
	};
}
export type DiagnosticsRepository = ReturnType<typeof createDiagnosticsRepository>;
