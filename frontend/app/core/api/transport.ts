// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { components } from "./schema.generated";

export interface SessionCredentials {
	/** Increment whenever the session is cleared or replaced, even for the same user. */
	generation: number;
	csrf: string | null;
}

export interface AuthBoundary {
	current(): SessionCredentials;
	lost(reason: string): void;
}

export class SessionLost extends Error {
	constructor() {
		super("The browser session is no longer current.");
	}
}

export class ApiFailure extends Error {
	constructor(
		public readonly status: number,
		public readonly error: components["schemas"]["ErrorView"],
		public readonly requestId: string | null,
	) {
		super(error.error);
	}
}

export class InvalidResponse extends Error {
	constructor(public readonly requestId: string | null) {
		super("The backend returned an invalid JSON response.");
	}
}

interface RequestOptions extends RequestInit {
	/** Only session discovery may run before local credentials have been restored. */
	allowSignedOut?: boolean;
	timeoutMs?: number;
}

export function createTransport(fetcher: typeof fetch, auth: AuthBoundary) {
	return async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
		const { allowSignedOut = false, timeoutMs = 35_000, ...init } = options;
		const { generation, csrf } = auth.current();
		if (!allowSignedOut && !csrf) throw new SessionLost();
		const headers = new Headers(init.headers);
		if (!["GET", "HEAD", "OPTIONS"].includes((init.method ?? "GET").toUpperCase())) {
			if (!csrf) throw new SessionLost();
			headers.set("X-CSRF-Token", csrf);
		}
		const timeout = AbortSignal.timeout(timeoutMs);
		const response = await fetcher(path, {
			...init,
			headers,
			credentials: "same-origin",
			cache: "no-store",
			signal: init.signal ? AbortSignal.any([init.signal, timeout]) : timeout,
		});
		if (generation !== auth.current().generation) throw new SessionLost();
		if (response.status === 204) return undefined as T;
		const requestId = response.headers.get("x-request-id");
		const body: unknown = await response.json().catch(() => undefined);
		// Reading the response body is asynchronous too: the user may have logged out.
		if (generation !== auth.current().generation) throw new SessionLost();
		if (!response.ok) {
			const error = {
				error:
					body &&
					typeof body === "object" &&
					"error" in body &&
					typeof body.error === "string"
						? body.error
						: "http_error",
				retryable:
					body &&
					typeof body === "object" &&
					"retryable" in body &&
					typeof body.retryable === "boolean"
						? body.retryable
						: response.status >= 500,
			} satisfies components["schemas"]["ErrorView"];
			if (response.status === 401 || error.error === "access_denied") {
				auth.lost(error.error);
				throw new SessionLost();
			}
			throw new ApiFailure(response.status, error, requestId);
		}
		if (body === undefined) throw new InvalidResponse(requestId);
		return body as T;
	};
}

export type Transport = ReturnType<typeof createTransport>;
