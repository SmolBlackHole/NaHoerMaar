// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { GeneratedApiClient } from "../../app/core/api/transport";

declare const api: GeneratedApiClient;

if (false) {
	// @ts-expect-error The generated client rejects paths outside the schema.
	api.GET("/api/not-a-route");

	// @ts-expect-error The session resource has no PATCH operation.
	api.PATCH("/api/auth/session");

	// @ts-expect-error The generated path parameter is required.
	api.GET("/api/profiles/{user_id}");

	api.GET("/api/playbacks", {
		params: {
			query: {
				// @ts-expect-error Numbered-page queries require a numeric page.
				page: "one",
			},
		},
	});

	api.POST("/api/player/control", {
		params: { header: { "Idempotency-Key": "75a6a294-6d68-4887-a6a4-d30e41f2668a" } },
		body: {
			action: "seek",
			// @ts-expect-error Seek seconds are numeric.
			seconds: "forty-two",
		},
	});
}
