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
		return run((signal) =>
			client.jobs.runCatalogMaintenance({ batch_size: batchSize }, signal),
		);
	}

	function runHousekeeping(batchSize: number) {
		return run((signal) => client.jobs.runHousekeeping({ batch_size: batchSize }, signal));
	}

	function run(
		start: (signal: AbortSignal) => ReturnType<BackendClient["jobs"]["runHousekeeping"]>,
	) {
		return jobs.load(async (signal) => {
			const updated = await start(signal);
			const current = jobs.data.value;
			const registered = current?.jobs ?? [];
			return {
				jobs: registered.some(({ id }) => id === updated.id)
					? registered.map((job) => (job.id === updated.id ? updated : job))
					: [...registered, updated],
				recent_runs: current?.recent_runs ?? [],
				history_retention_days: current?.history_retention_days ?? 30,
			};
		});
	}

	return {
		jobs,
		load,
		runCatalogMaintenance,
		runHousekeeping,
		dispose: jobs.dispose,
	};
}
