// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import createClient, { type Client } from "openapi-fetch";
import type { components, paths } from "./schema.generated";

const CLIENT_ORIGIN = "http://nahoermaar.invalid";
const REQUEST_TIMEOUT_MS = 35_000;
const SESSION_PATH = "/api/auth/session";

export interface SessionCredentials {
	/** Increment whenever the session is cleared or replaced, even for the same user. */
	generation: number;
	csrf: string | null;
}

export interface AuthBoundary {
	current(): SessionCredentials;
	lost(reason: string): void;
}

export interface SessionNotice {
	credentials: SessionCredentials;
	reason: string;
}

export interface SessionAuthority extends AuthBoundary {
	replace(csrf: string | null, reason: string): void;
	subscribe(listener: (notice: SessionNotice) => void): () => void;
}

/** Mutable auth boundary shared by the session store and all request workflows. */
export function createSessionAuthority(): SessionAuthority {
	let credentials: SessionCredentials = { generation: 0, csrf: null };
	const listeners = new Set<(notice: SessionNotice) => void>();
	const replace = (csrf: string | null, reason: string) => {
		credentials = { generation: credentials.generation + 1, csrf };
		const notice = { credentials: { ...credentials }, reason };
		for (const listener of listeners) listener(notice);
	};
	return {
		current: () => ({ ...credentials }),
		lost: (reason) => replace(null, reason),
		replace,
		subscribe(listener) {
			listeners.add(listener);
			return () => listeners.delete(listener);
		},
	};
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
		super("The request could not be completed.");
	}
}

export class InvalidResponse extends Error {
	constructor(public readonly requestId: string | null) {
		super("The backend returned an invalid JSON response.");
	}
}

export type GeneratedApiClient = Client<paths>;

type OpenApiResult = { data?: unknown; error?: unknown; response: Response };
type ApiOperation = (client: GeneratedApiClient) => Promise<OpenApiResult>;
type OperationData<TOperation extends ApiOperation> = Extract<
	Awaited<ReturnType<TOperation>>,
	{ data: unknown }
>["data"];

/** Execute one generated OpenAPI operation through the shared session boundary. */
export function createTransport(fetcher: typeof fetch, auth: AuthBoundary) {
	const client = createClient<paths>({
		baseUrl: CLIENT_ORIGIN,
		fetch: async (request) => {
			const url = new URL(request.url);
			const { csrf } = auth.current();
			const method = request.method.toUpperCase();
			const allowSignedOut = method === "GET" && url.pathname === SESSION_PATH;
			if (!allowSignedOut && !csrf) throw new SessionLost();

			const headers = new Headers(request.headers);
			if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
				if (!csrf) throw new SessionLost();
				headers.set("X-CSRF-Token", csrf);
			}
			const signal = AbortSignal.any([
				request.signal,
				AbortSignal.timeout(REQUEST_TIMEOUT_MS),
			]);
			const body = ["GET", "HEAD"].includes(method) ? undefined : await request.text();
			return fetcher(url.pathname + url.search, {
				method,
				headers,
				body: body || undefined,
				credentials: "same-origin",
				cache: "no-store",
				signal,
			});
		},
	});

	return async function request<TOperation extends ApiOperation>(
		operation: TOperation,
	): Promise<OperationData<TOperation>> {
		const generation = auth.current().generation;
		let result: OpenApiResult;
		try {
			result = await operation(client);
		} catch (error) {
			if (generation !== auth.current().generation) throw new SessionLost();
			if (error instanceof SyntaxError) throw new InvalidResponse(null);
			throw error;
		}
		if (generation !== auth.current().generation) throw new SessionLost();

		const requestId = result.response.headers.get("x-request-id");
		if (!("data" in result)) {
			const body: unknown = result.error;
			const error = {
				code:
					body &&
					typeof body === "object" &&
					"code" in body &&
					typeof body.code === "string"
						? body.code
						: "invalid_response",
				retryable:
					body &&
					typeof body === "object" &&
					"retryable" in body &&
					typeof body.retryable === "boolean"
						? body.retryable
						: result.response.status >= 500,
			} satisfies components["schemas"]["ErrorView"];
			if (result.response.status === 401) {
				auth.lost(error.code);
				throw new SessionLost();
			}
			throw new ApiFailure(result.response.status, error, requestId);
		}
		if (result.data === undefined && result.response.status !== 204) {
			throw new InvalidResponse(requestId);
		}
		return result.data as OperationData<TOperation>;
	};
}

export type Transport = ReturnType<typeof createTransport>;
