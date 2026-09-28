<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
import type { BackgroundJob, BackgroundJobRunSummary, RunJob } from "~/core/models/jobs";
import { failureForCode } from "~/core/errors";

definePageMeta({ pageTransition: { name: "page", mode: "out-in" } });
useSeoMeta({ title: "Background Jobs | NaHörMaar" });

const core = useNuxtApp().$backendCore;
const session = core.stores.useSessionStore();
const workflow = core.workflows.jobs();
const { icons } = useTheme();
const visibility = useDocumentVisibility();
const batchSizes = reactive<Record<string, number>>({});
const previewRuns = reactive<Record<string, boolean>>({});
const ageDays = reactive<Record<string, number>>({});
const submitting = reactive<Record<string, boolean>>({});
const expandedJobs = ref<string[]>([]);
const expandedRun = ref<string>();
const allowed = computed(() => ["owner", "admin"].includes(session.account?.role ?? ""));
const jobs = computed(() => workflow.jobs.data.value?.jobs ?? []);
const runs = computed(() => workflow.runs.data.value?.entries ?? []);
const runningJobs = computed(() => jobs.value.filter(({ running }) => running).length);
const latestRun = computed(() => runs.value[0]);
const attentionRuns = computed(
	() => jobs.value.filter(({ health }) => health === "needs_attention").length,
);
let timer: ReturnType<typeof setTimeout> | undefined;
let disposed = false;

const dateTime = (value: string | null) =>
	value
		? new Intl.DateTimeFormat(undefined, {
				dateStyle: "medium",
				timeStyle: "short",
			}).format(new Date(value))
		: "Not run yet";

function intervalLabel(seconds: number) {
	if (seconds >= 86_400 && seconds % 86_400 === 0)
		return `Every ${seconds / 86_400} day${seconds === 86_400 ? "" : "s"}`;
	if (seconds >= 3600 && seconds % 3600 === 0)
		return `Every ${seconds / 3600} hour${seconds === 3600 ? "" : "s"}`;
	if (seconds >= 60 && seconds % 60 === 0) return `Every ${seconds / 60} minutes`;
	return `Every ${seconds} seconds`;
}

function durationLabel(seconds: number | null) {
	if (seconds === null) return "Interrupted";
	if (seconds < 1) return "<1s";
	if (seconds < 60) return `${Math.round(seconds)}s`;
	const minutes = Math.floor(seconds / 60);
	const remaining = Math.round(seconds % 60);
	return `${minutes}m ${remaining}s`;
}

function progress(job: BackgroundJob) {
	if (!job.running || !job.active_candidates) return 0;
	return Math.min(100, Math.round((job.active_processed / job.active_candidates) * 100));
}

function runColor(status: string) {
	if (status === "succeeded") return "success" as const;
	if (status === "failed") return "error" as const;
	if (status === "partial") return "warning" as const;
	return "neutral" as const;
}

function runIcon(status: string) {
	if (status === "succeeded") return icons.value.success;
	if (status === "failed") return icons.value.error;
	if (status === "partial") return icons.value.warning;
	if (status === "running") return icons.value.loading;
	return icons.value.stop;
}

function detailIcon(outcome: string) {
	if (outcome === "changed") return icons.value.success;
	if (outcome === "failed") return icons.value.error;
	if (outcome === "skipped") return icons.value.clock;
	return icons.value.minus;
}

function detailTone(outcome: string) {
	if (outcome === "changed") return "text-success";
	if (outcome === "failed") return "text-error";
	if (outcome === "skipped") return "text-warning";
	return "text-dimmed";
}

function runLabel(run: BackgroundJobRunSummary) {
	return jobs.value.find(({ id }) => id === run.job_id)?.label ?? run.job_id;
}

function jobState(job: BackgroundJob) {
	if (job.running) return { label: "Running", color: "primary" as const };
	if (job.health === "needs_attention")
		return { label: "Needs attention", color: "warning" as const };
	if (job.health === "healthy") return { label: "Healthy", color: "success" as const };
	return { label: "Not run", color: "neutral" as const };
}

function jobIcon(job: BackgroundJob) {
	return job.module === "operations" ? icons.value.trash : icons.value.music;
}

function jobSteps(job: BackgroundJob) {
	return job.scope;
}

function progressLabel(job: BackgroundJob) {
	if (!job.active_candidates)
		return `Preparing up to ${job.active_options?.batch_size ?? job.controls.batch_size.default} entries`;
	if (job.progress_unit === "steps")
		return `${job.active_processed} of ${job.active_candidates} cleanup stages complete`;
	return `${job.active_processed} of ${job.active_candidates} candidates processed`;
}

function runFacts(run: BackgroundJobRunSummary) {
	return [
		{ label: "Requested", value: run.requested_count },
		{ label: "Candidates", value: run.candidate_count },
		{ label: "Processed", value: run.processed_count },
		{ label: "Changed", value: run.changed_count },
		{ label: "Failed", value: run.failure_count },
	];
}

function jobExpanded(id: string) {
	return expandedJobs.value.includes(id);
}

function toggleJob(id: string) {
	expandedJobs.value = jobExpanded(id)
		? expandedJobs.value.filter((entry) => entry !== id)
		: [...expandedJobs.value, id];
}

async function toggleRun(id: string) {
	if (expandedRun.value === id) {
		expandedRun.value = undefined;
		return;
	}
	expandedRun.value = id;
	await workflow.loadRun(id);
}

const runDetail = (id: string) => workflow.runDetail(id).data.value;
const runDetailLoading = (id: string) => workflow.runDetail(id).loading.value;
const runDetailError = (id: string) => workflow.runDetail(id).error.value;

async function refresh() {
	if (!allowed.value || visibility.value !== "visible" || Object.values(submitting).some(Boolean))
		return;
	await workflow.load();
	for (const job of jobs.value) {
		batchSizes[job.id] ??= job.controls.batch_size.default;
		if (job.controls.preview) previewRuns[job.id] ??= job.controls.preview.default;
		if (job.controls.age_days) ageDays[job.id] ??= job.controls.age_days.default;
	}
}

function loadMoreRuns() {
	return workflow.more();
}

async function runNow(job: BackgroundJob) {
	if (submitting[job.id] || job.running) return;
	clearTimeout(timer);
	const batchControl = job.controls.batch_size;
	const requested = Math.min(
		batchControl.maximum,
		Math.max(batchControl.minimum, Math.trunc(batchSizes[job.id] || batchControl.default)),
	);
	batchSizes[job.id] = requested;
	const options: RunJob = { batch_size: requested };
	if (job.controls.preview) options.preview = previewRuns[job.id];
	if (job.controls.age_days) {
		const ageControl = job.controls.age_days;
		const age = Math.min(
			ageControl.maximum,
			Math.max(ageControl.minimum, Math.trunc(ageDays[job.id] || ageControl.default)),
		);
		ageDays[job.id] = age;
		options.age_days = age;
	}
	submitting[job.id] = true;
	try {
		await workflow.runJob(job.id, options);
	} finally {
		submitting[job.id] = false;
		schedule();
	}
}

function schedule() {
	clearTimeout(timer);
	if (!disposed && allowed.value && visibility.value === "visible")
		timer = setTimeout(async () => {
			await refresh();
			schedule();
		}, 3000);
}

watch([allowed, visibility], () => {
	clearTimeout(timer);
	if (allowed.value && visibility.value === "visible") {
		void refresh();
		schedule();
	}
});
onMounted(async () => {
	await refresh();
	schedule();
});
onBeforeUnmount(() => {
	disposed = true;
	clearTimeout(timer);
	workflow.dispose();
});
</script>

<template>
	<div class="flex min-h-0 min-w-0 flex-1">
		<UDashboardPanel
			id="background-jobs"
			class="min-h-0 min-w-0 flex-1"
			:ui="{ body: 'pt-4 sm:pt-4' }"
		>
			<template #header>
				<UDashboardNavbar title="Background Jobs">
					<template #leading><UDashboardSidebarCollapse /></template>
				</UDashboardNavbar>
			</template>
			<template #body>
				<div v-if="!allowed" class="p-6 text-muted" role="alert">
					Only admins can manage background jobs.
				</div>
				<div v-else class="w-full space-y-10 pb-4 sm:pb-6">
					<div class="flex flex-wrap items-end justify-between gap-4">
						<div>
							<h1 class="text-2xl font-semibold text-highlighted">
								Keep things tidy
							</h1>
							<p class="mt-2 max-w-2xl text-sm text-muted">
								Small, bounded jobs repair catalog data and clear expired records
								without interrupting playback.
							</p>
						</div>
						<p v-if="workflow.jobs.data.value" class="text-xs text-muted">
							History stays around for
							{{ workflow.jobs.data.value.history_retention_days }} days.
						</p>
					</div>

					<p v-if="workflow.jobs.error.value" role="alert" class="text-sm text-warning">
						{{ workflow.jobs.error.value }}
					</p>

					<JobsSkeleton v-if="workflow.jobs.loading.value && !jobs.length" />
					<div
						v-else-if="!jobs.length"
						class="grid min-h-64 place-items-center text-center"
					>
						<div class="max-w-sm">
							<UIcon :name="icons.settings" class="mx-auto size-10 text-dimmed" />
							<h2 class="mt-4 text-lg font-semibold text-highlighted">
								No maintenance jobs
							</h2>
							<p class="mt-2 text-sm text-muted">
								Nothing is registered for this application instance.
							</p>
						</div>
					</div>

					<template v-else>
						<div
							class="grid overflow-hidden rounded-2xl border border-default sm:grid-cols-2 xl:grid-cols-4"
						>
							<div class="p-5 sm:border-r sm:border-default">
								<p class="flex items-center gap-2 text-sm text-muted">
									<UIcon :name="icons.settings" class="size-4 text-primary" />
									Registered jobs
								</p>
								<p
									class="mt-3 text-2xl font-semibold tabular-nums text-highlighted"
								>
									{{ jobs.length }}
								</p>
							</div>
							<div class="border-t border-default p-5 sm:border-r sm:border-t-0">
								<p class="flex items-center gap-2 text-sm text-muted">
									<UIcon
										:name="runningJobs ? icons.loading : icons.check"
										class="size-4 text-primary"
										:class="runningJobs ? 'animate-spin' : ''"
									/>
									Running now
								</p>
								<p
									class="mt-3 text-2xl font-semibold tabular-nums text-highlighted"
								>
									{{ runningJobs }}
								</p>
							</div>
							<div class="border-t border-default p-5 xl:border-r xl:border-t-0">
								<p class="flex items-center gap-2 text-sm text-muted">
									<UIcon :name="icons.clock" class="size-4 text-primary" /> Latest
									run
								</p>
								<p class="mt-3 truncate text-base font-semibold text-highlighted">
									{{
										latestRun ? dateTime(latestRun.finished_at) : "Not run yet"
									}}
								</p>
							</div>
							<div class="border-t border-default p-5 xl:border-t-0">
								<p class="flex items-center gap-2 text-sm text-muted">
									<UIcon
										:name="attentionRuns ? icons.warning : icons.success"
										class="size-4"
										:class="attentionRuns ? 'text-warning' : 'text-success'"
									/>
									Need attention
								</p>
								<p
									class="mt-3 text-2xl font-semibold tabular-nums text-highlighted"
								>
									{{ attentionRuns }}
								</p>
							</div>
						</div>

						<section aria-labelledby="scheduled-jobs-title">
							<div class="mb-4">
								<h2
									id="scheduled-jobs-title"
									class="text-lg font-semibold text-highlighted"
								>
									Scheduled jobs
								</h2>
								<p class="mt-1 text-xs text-muted">
									Automatic work can also be started manually with a bounded
									batch.
								</p>
							</div>

							<div class="grid gap-4 xl:grid-cols-2">
								<article
									v-for="job in jobs"
									:key="job.id"
									class="overflow-hidden rounded-xl bg-elevated/35"
								>
									<div class="p-5 sm:p-6">
										<div class="flex items-start gap-4">
											<span
												class="mt-0.5 grid size-6 shrink-0 place-items-center text-primary"
											>
												<UIcon :name="jobIcon(job)" class="size-5" />
											</span>
											<div class="min-w-0 flex-1">
												<div class="flex flex-wrap items-center gap-2">
													<h3 class="font-semibold text-highlighted">
														{{ job.label }}
													</h3>
													<UBadge
														:color="jobState(job).color"
														variant="subtle"
													>
														{{ jobState(job).label }}
													</UBadge>
												</div>
												<p class="mt-1 text-sm leading-relaxed text-muted">
													{{ job.description }}
												</p>
											</div>
										</div>

										<dl class="mt-6 grid grid-cols-3 gap-4">
											<div>
												<dt class="text-xs text-muted">Schedule</dt>
												<dd
													class="mt-1 text-sm font-medium text-highlighted"
												>
													{{ intervalLabel(job.interval_seconds) }}
												</dd>
											</div>
											<div>
												<dt class="text-xs text-muted">Last changed</dt>
												<dd
													class="mt-1 text-sm font-medium tabular-nums text-highlighted"
												>
													{{ job.last_changed }} records
												</dd>
											</div>
											<div>
												<dt class="text-xs text-muted">Next run</dt>
												<dd
													class="mt-1 truncate text-sm font-medium text-highlighted"
												>
													{{ dateTime(job.next_run_at) }}
												</dd>
											</div>
										</dl>

										<div
											v-if="job.running"
											class="mt-5 rounded-xl bg-default/45 p-4"
										>
											<div
												class="flex items-center justify-between gap-3 text-sm"
											>
												<p class="font-medium text-highlighted">
													{{ progressLabel(job) }}
												</p>
												<p
													v-if="job.parallel_requests > 1"
													class="text-xs text-muted"
												>
													{{ job.parallel_requests }} at once
												</p>
											</div>
											<div
												class="mt-3 h-2 overflow-hidden rounded-full bg-accented"
												role="progressbar"
												:aria-valuemin="0"
												:aria-valuemax="job.active_candidates || undefined"
												:aria-valuenow="
													job.active_candidates
														? job.active_processed
														: undefined
												"
											>
												<div
													class="h-full rounded-full bg-primary transition-[width] duration-300"
													:class="
														job.active_candidates ? '' : 'animate-pulse'
													"
													:style="{
														width: job.active_candidates
															? `${progress(job)}%`
															: '35%',
													}"
												/>
											</div>
										</div>

										<div
											v-if="job.last_error"
											class="mt-4 text-sm text-error"
											role="alert"
										>
											<p class="font-medium">
												{{ failureForCode(job.last_error).title }}
											</p>
											<p>{{ failureForCode(job.last_error).description }}</p>
										</div>

										<div
											class="mt-6 flex flex-wrap items-end justify-between gap-4"
										>
											<button
												type="button"
												class="flex items-center gap-2 text-sm font-medium text-muted outline-none transition-colors hover:text-highlighted focus-visible:text-highlighted"
												:aria-expanded="jobExpanded(job.id)"
												:aria-controls="`job-scope-${job.id}`"
												@click="toggleJob(job.id)"
											>
												<UIcon
													:name="icons.chevronRight"
													class="size-4 transition-transform duration-300"
													:class="jobExpanded(job.id) ? 'rotate-90' : ''"
												/>
												What it maintains
											</button>

											<div class="flex flex-wrap items-end justify-end gap-3">
												<UCheckbox
													v-if="job.controls.preview"
													v-model="previewRuns[job.id]"
													label="Preview only"
													:disabled="job.running || submitting[job.id]"
													class="pb-2"
												/>
												<label
													v-if="job.controls.age_days"
													class="grid gap-1 text-xs text-muted"
												>
													Minimum age (days)
													<UInput
														v-model.number="ageDays[job.id]"
														type="number"
														:min="job.controls.age_days.minimum"
														:max="job.controls.age_days.maximum"
														class="w-28"
														:aria-label="`${job.label} minimum age in days`"
													/>
												</label>
												<label class="grid gap-1 text-xs text-muted">
													Entries
													<UInput
														v-model.number="batchSizes[job.id]"
														type="number"
														:min="job.controls.batch_size.minimum"
														:max="job.controls.batch_size.maximum"
														class="w-24"
														:aria-label="`${job.label} batch size`"
													/>
												</label>
												<UTooltip
													:text="`Process at most ${job.controls.batch_size.maximum} entries.`"
												>
													<UButton
														label="Run now"
														:icon="icons.play"
														:loading="submitting[job.id]"
														:disabled="
															job.running || submitting[job.id]
														"
														@click="runNow(job)"
													/>
												</UTooltip>
											</div>
										</div>
									</div>

									<div
										class="grid transition-[grid-template-rows] duration-300 ease-out motion-reduce:transition-none"
										:class="
											jobExpanded(job.id)
												? 'grid-rows-[1fr]'
												: 'grid-rows-[0fr]'
										"
									>
										<div class="min-h-0 overflow-hidden">
											<ul
												:id="`job-scope-${job.id}`"
												class="grid gap-2 border-t border-default/60 px-5 py-4 text-sm text-muted transition-all duration-300 sm:grid-cols-2 sm:px-6"
												:class="
													jobExpanded(job.id)
														? 'translate-y-0 opacity-100'
														: '-translate-y-2 opacity-0'
												"
											>
												<li
													v-for="step in jobSteps(job)"
													:key="step"
													class="flex items-start gap-2"
												>
													<UIcon
														:name="icons.check"
														class="mt-0.5 size-4 shrink-0 text-primary"
													/>
													<span>{{ step }}</span>
												</li>
											</ul>
										</div>
									</div>
								</article>
							</div>
						</section>

						<section
							v-if="
								runs.length ||
								workflow.runs.loading.value ||
								workflow.runs.error.value
							"
							aria-labelledby="job-history-title"
						>
							<div class="mb-4 flex flex-wrap items-end justify-between gap-3">
								<div>
									<h2
										id="job-history-title"
										class="text-lg font-semibold text-highlighted"
									>
										Recent runs
									</h2>
									<p class="mt-1 text-xs text-muted">
										Open a run to see every repaired item, refreshed search and
										cleanup result.
									</p>
								</div>
								<p class="text-xs text-muted">{{ runs.length }} recorded runs</p>
							</div>

							<p
								v-if="workflow.runs.error.value && !runs.length"
								class="rounded-xl bg-warning/10 px-4 py-3 text-sm text-warning"
								role="alert"
							>
								{{ workflow.runs.error.value }}
							</p>
							<div
								v-if="workflow.runs.loading.value && !runs.length"
								class="grid gap-2"
								aria-label="Loading job history"
							>
								<div
									v-for="row in 3"
									:key="row"
									class="grid gap-3 rounded-xl bg-elevated/35 p-4 sm:grid-cols-[minmax(0,1fr)_auto_auto_auto_auto] sm:items-center"
								>
									<div class="flex items-center gap-3">
										<USkeleton class="size-9 shrink-0 rounded-full" />
										<div class="grid flex-1 gap-2">
											<USkeleton class="h-4 w-40 max-w-full" />
											<USkeleton class="h-3 w-52 max-w-full" />
										</div>
									</div>
									<USkeleton class="h-4 w-20" />
									<USkeleton class="h-4 w-12" />
									<USkeleton class="h-5 w-16 rounded-full" />
									<USkeleton class="size-4" />
								</div>
							</div>

							<div v-else class="grid gap-2">
								<article
									v-for="run in runs"
									:key="run.id"
									class="overflow-hidden rounded-xl bg-elevated/35 transition-colors hover:bg-elevated/50"
								>
									<button
										type="button"
										class="grid w-full gap-3 p-4 text-left outline-none sm:grid-cols-[minmax(0,1fr)_auto_auto_auto_auto] sm:items-center"
										:aria-expanded="expandedRun === run.id"
										:aria-controls="`run-details-${run.id}`"
										@click="toggleRun(run.id)"
									>
										<div class="flex min-w-0 items-center gap-3">
											<span
												class="grid size-9 shrink-0 place-items-center rounded-full bg-default/70"
											>
												<UIcon
													:name="runIcon(run.status)"
													class="size-4"
													:class="
														run.status === 'running'
															? 'animate-spin'
															: ''
													"
												/>
											</span>
											<div class="min-w-0">
												<p class="truncate font-medium text-highlighted">
													{{ runLabel(run) }}
												</p>
												<p class="truncate text-xs capitalize text-muted">
													{{ dateTime(run.started_at) }} ·
													{{ run.trigger }}
												</p>
											</div>
										</div>
										<p class="text-sm text-muted sm:text-right">
											<span
												class="font-medium tabular-nums text-highlighted"
												>{{ run.changed_count }}</span
											>
											changed
										</p>
										<p class="text-sm tabular-nums text-muted sm:text-right">
											{{ durationLabel(run.duration_seconds) }}
										</p>
										<UBadge
											:color="runColor(run.status)"
											variant="subtle"
											class="capitalize"
										>
											{{ run.status }}
										</UBadge>
										<UIcon
											:name="icons.chevronDown"
											class="size-4 text-muted transition-transform duration-300"
											:class="expandedRun === run.id ? 'rotate-180' : ''"
										/>
									</button>

									<div
										class="grid transition-[grid-template-rows] duration-300 ease-out motion-reduce:transition-none"
										:class="
											expandedRun === run.id
												? 'grid-rows-[1fr]'
												: 'grid-rows-[0fr]'
										"
									>
										<div class="min-h-0 overflow-hidden">
											<div
												:id="`run-details-${run.id}`"
												class="border-t border-default/60 px-4 pb-5 pt-4 transition-all duration-300 sm:pl-16"
												:class="
													expandedRun === run.id
														? 'translate-y-0 opacity-100'
														: '-translate-y-2 opacity-0'
												"
											>
												<dl
													class="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-5"
												>
													<div
														v-for="fact in runFacts(run)"
														:key="fact.label"
													>
														<dt class="text-xs text-muted">
															{{ fact.label }}
														</dt>
														<dd
															class="mt-0.5 font-medium tabular-nums text-highlighted"
														>
															{{ fact.value }}
														</dd>
													</div>
												</dl>

												<p
													v-if="run.error_code"
													class="mt-4 text-sm text-error"
												>
													{{ failureForCode(run.error_code).title }}
													<span class="text-muted"
														>({{ run.error_code }})</span
													>
												</p>

												<div
													v-if="runDetailLoading(run.id)"
													class="mt-5 grid gap-2"
													aria-label="Loading run details"
												>
													<div
														v-for="row in 2"
														:key="row"
														class="flex items-start gap-3 rounded-xl bg-default/45 px-3 py-3"
													>
														<USkeleton
															class="mt-0.5 size-4 shrink-0 rounded"
														/>
														<div class="grid flex-1 gap-2">
															<USkeleton
																class="h-4 w-44 max-w-full"
															/>
															<USkeleton
																class="h-3 w-80 max-w-full"
															/>
														</div>
													</div>
												</div>
												<p
													v-else-if="runDetailError(run.id)"
													class="mt-5 text-sm text-warning"
													role="alert"
												>
													{{ runDetailError(run.id) }}
												</p>
												<div
													v-else-if="runDetail(run.id)?.details.length"
													class="mt-5 grid gap-2"
												>
													<div
														v-for="(detail, index) in runDetail(run.id)
															?.details ?? []"
														:key="`${run.id}-${index}`"
														class="flex items-start gap-3 rounded-xl bg-default/45 px-3 py-3"
													>
														<UIcon
															:name="detailIcon(detail.outcome)"
															class="mt-0.5 size-4 shrink-0"
															:class="detailTone(detail.outcome)"
														/>
														<div class="min-w-0 flex-1">
															<div
																class="flex flex-wrap items-center gap-x-2 gap-y-1"
															>
																<p
																	class="truncate text-sm font-medium text-highlighted"
																>
																	{{ detail.label }}
																</p>
																<span
																	v-if="detail.source"
																	class="text-xs text-dimmed"
																	>{{ detail.source }}</span
																>
																<span
																	v-if="detail.affected_count"
																	class="text-xs tabular-nums text-muted"
																>
																	{{ detail.affected_count }}
																	affected
																</span>
															</div>
															<p
																class="mt-0.5 text-xs leading-relaxed text-muted"
															>
																{{ detail.summary }}
															</p>
															<p
																v-if="detail.error_code"
																class="mt-1 font-mono text-xs text-error"
															>
																{{ detail.error_code }}
															</p>
														</div>
													</div>
												</div>
												<p v-else class="mt-5 text-sm text-muted">
													Item-level details were not recorded for this
													earlier run.
												</p>
											</div>
										</div>
									</div>
								</article>
							</div>
							<div v-if="workflow.hasMoreRuns.value" class="mt-4 flex justify-center">
								<UButton
									label="Load more runs"
									color="neutral"
									variant="soft"
									:icon="icons.chevronDown"
									:loading="workflow.runs.loading.value"
									@click="loadMoreRuns"
								/>
							</div>
						</section>
					</template>
				</div>
			</template>
		</UDashboardPanel>
	</div>
</template>
