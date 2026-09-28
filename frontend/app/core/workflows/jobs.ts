// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { SessionAuthority } from "../api/transport";
import type { BackendClient } from "../client";
import type {
	BackgroundJobRun,
	BackgroundJobRunPage,
	BackgroundJobs,
	RunJob,
} from "../models/jobs";
import { createCursorPagination } from "./cursorPagination";
import { createQueryState, type QueryState } from "./queryState";

export function createJobsWorkflow(client: BackendClient, authority: SessionAuthority) {
	const jobs = createQueryState<BackgroundJobs>(authority);
	const runHistory = createCursorPagination<
		BackgroundJobRunPage["items"][number],
		BackgroundJobRunPage
	>(
		authority,
		(pageSize, cursor, signal) => client.jobs.runs({ pageSize, cursor }, signal),
		({ id }) => id,
	);
	const runs = runHistory.page;
	const runDetails = new Map<string, QueryState<BackgroundJobRun>>();

	async function load() {
		const [status] = await Promise.all([refreshStatus(), runHistory.refresh()]);
		return status;
	}

	function refreshStatus() {
		return jobs.load((signal) => client.jobs.status(signal));
	}

	function runDetail(id: string) {
		let state = runDetails.get(id);
		if (!state) {
			state = createQueryState<BackgroundJobRun>(authority);
			runDetails.set(id, state);
		}
		return state;
	}

	function loadRun(id: string) {
		const detail = runDetail(id);
		if (detail.data.value) return Promise.resolve(detail.data.value);
		return detail.load((signal) => client.jobs.run(id, signal));
	}

	function runJob(id: string, options: RunJob) {
		return start((signal) => client.jobs.runJob(id, options, signal));
	}

	function start(start: (signal: AbortSignal) => Promise<BackgroundJobs["jobs"][number]>) {
		return jobs.load(async (signal) => {
			const updated = await start(signal);
			const current = jobs.data.value;
			const registered = current?.jobs ?? [];
			return {
				jobs: registered.some(({ id }) => id === updated.id)
					? registered.map((job) => (job.id === updated.id ? updated : job))
					: [...registered, updated],
				history_retention_days: current?.history_retention_days ?? 30,
			};
		});
	}

	return {
		jobs,
		runs,
		hasMoreRuns: runHistory.hasMore,
		load,
		refreshStatus,
		more: runHistory.more,
		runDetail,
		loadRun,
		runJob,
		dispose() {
			jobs.dispose();
			runHistory.dispose();
			for (const detail of runDetails.values()) detail.dispose();
			runDetails.clear();
		},
	};
}
