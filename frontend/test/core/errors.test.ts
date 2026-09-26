// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import { describe, expect, it } from "vitest";
import { ApiFailure, InvalidResponse } from "../../app/core/api/transport";
import { failureForCode, presentFailure } from "../../app/core/errors";

describe("frontend failure presentation", () => {
	it("turns stable domain codes into useful player copy", () => {
		expect(failureForCode("nothing_playing")).toMatchObject({
			title: "There is literally nothing playing.",
			description: "You managed to pause, seek or skip silence. Impressive.",
			retryable: false,
		});
		expect(failureForCode("provider_failed")).toMatchObject({
			title: "YouTube has chosen violence.",
			retryable: true,
		});
	});

	it("keeps diagnostics while hiding machine codes from the UI", () => {
		const failure = presentFailure(
			new ApiFailure(403, { error: "origin_forbidden", retryable: false }, "request-id"),
		);

		expect(failure).toMatchObject({
			code: "origin_forbidden",
			requestId: "request-id",
			retryable: false,
		});
		expect(failure.description).not.toContain("origin_forbidden");
	});

	it("does not leak arbitrary exception text", () => {
		expect(presentFailure(new Error("database password is secret")).description).toBe(
			"Something unexpected happened. The logs probably know more than we do.",
		);
		expect(presentFailure(new InvalidResponse("request-id"))).toMatchObject({
			code: "invalid_response",
			requestId: "request-id",
			retryable: true,
		});
	});
});
