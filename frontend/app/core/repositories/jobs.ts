// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { Transport } from "../api/transport";
import type { BackgroundJob, BackgroundJobs, RunCatalogMaintenance } from "../models/jobs";

export function createJobsRepository(request: Transport) {
	return {
		status: (signal?: AbortSignal) => request<BackgroundJobs>("/api/jobs", { signal }),
		runCatalogMaintenance: (body: RunCatalogMaintenance, signal?: AbortSignal) =>
			request<BackgroundJob>("/api/jobs/catalog-maintenance", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify(body),
				signal,
			}),
	};
}

export type JobsRepository = ReturnType<typeof createJobsRepository>;
