// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { SessionAuthority } from "../api/transport";
import type { BackendClient } from "../client";
import type { BackgroundJobs } from "../models/jobs";
import { createQueryState } from "./queryState";

export function createJobsWorkflow(client: BackendClient, authority: SessionAuthority) {
	const jobs = createQueryState<BackgroundJobs>(authority);

	function load() {
		return jobs.load((signal) => client.jobs.status(signal));
	}

	function runCatalogMaintenance(batchSize: number) {
		return jobs.load(async (signal) => {
			const updated = await client.jobs.runCatalogMaintenance(
				{ batch_size: batchSize },
				signal,
			);
			const current = jobs.data.value?.jobs ?? [];
			return {
				jobs: current.some(({ id }) => id === updated.id)
					? current.map((job) => (job.id === updated.id ? updated : job))
					: [...current, updated],
			};
		});
	}

	return { jobs, load, runCatalogMaintenance, dispose: jobs.dispose };
}
