import { describe, expect, it } from "vitest";
import { errorPageContent } from "../app/utils/errorPage";

describe("custom error page copy", () => {
	it("uses the intended 404 message without offering a pointless retry", () => {
		const error = errorPageContent(404);

		expect(error.title).toBe("And where the fuck do you think you're going?");
		expect(error.retryable).toBe(false);
	});

	it("offers retries for temporary backend failures", () => {
		for (const status of [408, 409, 429, 500, 502, 503, 504]) {
			expect(errorPageContent(status).retryable).toBe(true);
		}
	});

	it("keeps every supported response on the themed icon contract", () => {
		for (const status of [
			400, 401, 402, 403, 404, 405, 408, 409, 410, 418, 422, 423, 425, 429, 451, 500, 501,
			502, 503, 504, 507,
		]) {
			expect(errorPageContent(status).icon).not.toMatch(/^i-/);
		}
	});

	it("does not expose arbitrary server messages for unknown errors", () => {
		expect(errorPageContent(599)).toMatchObject({ code: 599, retryable: true });
		expect(errorPageContent(undefined).code).toBe(500);
	});
});
