// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { Transport } from "../api/transport";
import type {
	BackgroundJob,
	BackgroundJobRun,
	BackgroundJobRunPage,
	BackgroundJobs,
	JobId,
	JobRunStatus,
	RunJob,
} from "../models/jobs";

export interface JobRunsQuery {
	pageSize?: number;
	cursor?: string;
	jobId?: JobId;
	status?: JobRunStatus;
}

export function createJobsRepository(request: Transport) {
	return {
		status: (signal?: AbortSignal) => request((api) => api.GET("/api/jobs", { signal })),
		runs: (query: JobRunsQuery = {}, signal?: AbortSignal) =>
			request((api) =>
				api.GET("/api/jobs/runs", {
					params: {
						query: {
							page_size: query.pageSize ?? 20,
							cursor: query.cursor,
							job_id: query.jobId,
							status: query.status,
						},
					},
					signal,
				}),
			),
		run: (id: string, signal?: AbortSignal) =>
			request((api) =>
				api.GET("/api/jobs/runs/{run_id}", {
					params: { path: { run_id: id } },
					signal,
				}),
			),
		runJob: (id: string, body: RunJob, signal?: AbortSignal) =>
			request((api) =>
				api.POST("/api/jobs/{job_id}/runs", {
					params: { path: { job_id: id } },
					body,
					signal,
				}),
			),
	};
}

export type JobsRepository = ReturnType<typeof createJobsRepository>;
