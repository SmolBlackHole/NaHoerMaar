// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { Transport } from "../api/transport";
import type {
	BackgroundJob,
	BackgroundJobRun,
	BackgroundJobRunPage,
	BackgroundJobs,
	RunCatalogMaintenance,
	RunHousekeeping,
} from "../models/jobs";

export interface JobRunsQuery {
	limit?: number;
	cursor?: string;
	jobId?: string;
	status?: string;
}

export function createJobsRepository(request: Transport) {
	return {
		status: (signal?: AbortSignal) => request<BackgroundJobs>("/api/jobs", { signal }),
		runs: (query: JobRunsQuery = {}, signal?: AbortSignal) => {
			const params = new URLSearchParams();
			params.set("limit", String(query.limit ?? 20));
			if (query.cursor) params.set("cursor", query.cursor);
			if (query.jobId) params.set("job_id", query.jobId);
			if (query.status) params.set("status", query.status);
			return request<BackgroundJobRunPage>(`/api/jobs/runs?${params}`, { signal });
		},
		run: (id: string, signal?: AbortSignal) =>
			request<BackgroundJobRun>(`/api/jobs/runs/${encodeURIComponent(id)}`, { signal }),
		runCatalogMaintenance: (body: RunCatalogMaintenance, signal?: AbortSignal) =>
			request<BackgroundJob>("/api/jobs/catalog-maintenance", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify(body),
				signal,
			}),
		runHousekeeping: (body: RunHousekeeping, signal?: AbortSignal) =>
			request<BackgroundJob>("/api/jobs/housekeeping", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify(body),
				signal,
			}),
	};
}

export type JobsRepository = ReturnType<typeof createJobsRepository>;
