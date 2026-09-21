import type { ApiError, MutationReply } from "../../shared/engine";

export class SessionLost extends Error {}
export class ApiFailure extends Error {
	constructor(
		public status: number,
		public data: ApiError | MutationReply,
	) {
		super(
			"message" in data && data.message
				? data.message
				: [404, 410].includes(status)
					? "These results expired. Search or open the playlist again."
					: "The request could not be completed. Try again.",
		);
	}
}
export interface AuthHooks {
	current(): { generation: number; csrfToken: string | null };
	lost(code: string): void;
}
interface RequestOptions extends RequestInit {
	anonymous?: boolean;
	timeoutMs?: number;
}
export function createHttpTransport(fetcher: typeof fetch, auth: AuthHooks) {
	return async function json<T>(path: string, options: RequestOptions = {}): Promise<T> {
		const { anonymous = false, timeoutMs = 35_000, ...init } = options;
		const current = auth.current();
		if (!anonymous && !current.csrfToken) throw new SessionLost();
		const headers = new Headers(init.headers);
		if (!anonymous && !["GET", "HEAD"].includes((init.method ?? "GET").toUpperCase()))
			headers.set("X-CSRF-Token", current.csrfToken!);
		const timeout = AbortSignal.timeout(timeoutMs);
		const response = await fetcher(path, {
			...init,
			headers,
			cache: "no-store",
			signal: init.signal ? AbortSignal.any([init.signal, timeout]) : timeout,
		});
		if (current.generation !== auth.current().generation) throw new SessionLost();
		if (response.status === 204) return undefined as T;
		const data = await response.json().catch((error: unknown) => {
			if (response.ok) throw error;
			return {
				code: "http_error",
				message: null,
				retryable: response.status >= 500,
			} satisfies ApiError;
		});
		if (current.generation !== auth.current().generation) throw new SessionLost();
		if (!response.ok) {
			if (
				response.status === 401 ||
				["access_denied", "access_unavailable", "auth_unavailable"].includes(data?.code)
			) {
				auth.lost(data?.code ?? "signed_out");
				throw new SessionLost();
			}
			throw new ApiFailure(
				response.status,
				data && typeof data === "object"
					? data
					: { code: "http_error", message: null, retryable: response.status >= 500 },
			);
		}
		return data as T;
	};
}
export type HttpTransport = ReturnType<typeof createHttpTransport>;
