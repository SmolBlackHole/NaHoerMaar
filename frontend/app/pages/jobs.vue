<script setup lang="ts">
import { failureForCode } from "~/core/errors";

definePageMeta({ pageTransition: { name: "page", mode: "out-in" } });
useSeoMeta({ title: "Background Jobs | NaHörMaar" });

const core = useNuxtApp().$backendCore;
const session = core.stores.useSessionStore();
const workflow = core.workflows.jobs();
const { icons } = useTheme();
const visibility = useDocumentVisibility();
const batchSize = ref(10);
const submitting = ref(false);
const allowed = computed(() => ["owner", "admin"].includes(session.account?.role ?? ""));
const job = computed(() => workflow.jobs.data.value?.jobs[0] ?? null);
const jobFailure = computed(() =>
	job.value?.last_error ? failureForCode(job.value.last_error) : null,
);
let timer: ReturnType<typeof setTimeout> | undefined;
let disposed = false;

const dateTime = (value: string | null) =>
	value
		? new Intl.DateTimeFormat(undefined, {
				dateStyle: "medium",
				timeStyle: "medium",
			}).format(new Date(value))
		: "Not run yet";
const interval = computed(() => {
	const seconds = job.value?.interval_seconds ?? 300;
	return seconds % 60 === 0 ? `${seconds / 60} minutes` : `${seconds} seconds`;
});
const progress = computed(() => {
	if (!job.value?.running || !job.value.active_candidates) return 0;
	return Math.min(
		100,
		Math.round((job.value.active_processed / job.value.active_candidates) * 100),
	);
});

async function refresh() {
	if (!allowed.value || visibility.value !== "visible" || submitting.value) return;
	await workflow.load();
	if (job.value && batchSize.value === 10) batchSize.value = job.value.default_batch_size;
}

async function runNow() {
	if (submitting.value || job.value?.running) return;
	clearTimeout(timer);
	const requested = Math.min(100, Math.max(1, Math.trunc(batchSize.value || 1)));
	batchSize.value = requested;
	submitting.value = true;
	try {
		await workflow.runCatalogMaintenance(requested);
	} finally {
		submitting.value = false;
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
	<UDashboardPanel id="background-jobs" class="min-h-0 min-w-0" :ui="{ body: 'pt-4 sm:pt-4' }">
		<template #header>
			<UDashboardNavbar title="Background Jobs">
				<template #leading><UDashboardSidebarCollapse /></template>
			</UDashboardNavbar>
		</template>
		<template #body>
			<div v-if="!allowed" class="p-6 text-muted" role="alert">
				Only admins can manage background jobs.
			</div>
			<div v-else class="flex min-h-0 flex-col gap-5 pb-4 sm:pb-6">
				<div>
					<h1 class="text-xl font-semibold text-highlighted">Maintenance</h1>
					<p class="mt-1 text-sm text-muted">
						Inspect scheduled work and start a bounded run when catalog data needs
						attention.
					</p>
				</div>

				<p v-if="workflow.jobs.error.value" role="alert" class="text-sm text-warning">
					{{ workflow.jobs.error.value }}
				</p>

				<JobsSkeleton v-if="workflow.jobs.loading.value && !job" />
				<div v-else-if="!job" class="rounded-lg border border-default p-6">
					<p class="text-sm text-muted">No background jobs are registered.</p>
				</div>

				<UCard v-else>
					<template #header>
						<div class="flex flex-wrap items-start justify-between gap-3">
							<div>
								<div class="flex items-center gap-2">
									<h2 class="font-semibold text-highlighted">{{ job.label }}</h2>
									<UBadge
										:color="job.running ? 'primary' : 'neutral'"
										variant="subtle"
									>
										{{ job.running ? "Running" : "Idle" }}
									</UBadge>
								</div>
								<p class="mt-1 text-sm text-muted">
									Repairs incomplete track metadata and refreshes stale catalog
									results.
								</p>
							</div>
							<div class="flex items-end gap-2">
								<label class="grid gap-1 text-xs text-muted">
									Entries
									<UInput
										v-model.number="batchSize"
										type="number"
										:min="1"
										:max="100"
										class="w-24"
										aria-label="Catalog maintenance batch size"
									/>
								</label>
								<UTooltip
									text="Repair this many catalog entries now, without changing the scheduled default."
								>
									<UButton
										label="Run now"
										:icon="icons.play"
										:loading="submitting"
										:disabled="job.running || submitting"
										@click="runNow"
									/>
								</UTooltip>
							</div>
						</div>
					</template>

					<div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
						<div class="rounded-lg bg-elevated/50 p-4">
							<p class="text-xs uppercase tracking-wide text-muted">Schedule</p>
							<p class="mt-1 font-medium text-highlighted">Every {{ interval }}</p>
							<p class="mt-1 text-xs text-muted">
								Next: {{ dateTime(job.next_run_at) }}
							</p>
						</div>
						<div class="rounded-lg bg-elevated/50 p-4">
							<p class="text-xs uppercase tracking-wide text-muted">Last run</p>
							<p class="mt-1 font-medium text-highlighted">
								{{ dateTime(job.last_finished_at) }}
							</p>
							<p class="mt-1 text-xs text-muted">
								{{
									job.last_trigger
										? `Triggered ${job.last_trigger}`
										: "No completed run"
								}}
							</p>
						</div>
						<div class="rounded-lg bg-elevated/50 p-4">
							<p class="text-xs uppercase tracking-wide text-muted">Metadata</p>
							<p class="mt-1 font-medium text-highlighted">
								{{ job.last_metadata_repaired }} repaired
							</p>
							<p class="mt-1 text-xs text-muted">
								{{ job.last_metadata_candidates }} candidates
							</p>
						</div>
						<div class="rounded-lg bg-elevated/50 p-4">
							<p class="text-xs uppercase tracking-wide text-muted">
								Discovery cache
							</p>
							<p class="mt-1 font-medium text-highlighted">
								{{ job.last_discovery_refreshed }} refreshed
							</p>
							<p class="mt-1 text-xs text-muted">
								{{ job.last_discovery_candidates }} candidates
							</p>
						</div>
					</div>
					<div v-if="job.running" class="mt-4 rounded-lg border border-default p-4">
						<div class="flex items-center justify-between gap-3 text-sm">
							<p class="font-medium text-highlighted">
								{{
									job.active_candidates
										? `${job.active_processed} of ${job.active_candidates} entries processed`
										: `Preparing up to ${job.active_batch_size ?? job.default_batch_size} entries`
								}}
							</p>
							<p class="text-xs text-muted">
								Up to {{ job.parallel_requests }} requests at once
							</p>
						</div>
						<div
							class="mt-3 h-2 overflow-hidden rounded-full bg-elevated"
							role="progressbar"
							:aria-valuemin="0"
							:aria-valuemax="job.active_candidates || undefined"
							:aria-valuenow="
								job.active_candidates ? job.active_processed : undefined
							"
						>
							<div
								class="h-full rounded-full bg-primary transition-[width] duration-300"
								:class="job.active_candidates ? '' : 'animate-pulse'"
								:style="{ width: job.active_candidates ? `${progress}%` : '35%' }"
							/>
						</div>
					</div>
					<div v-if="jobFailure" class="mt-4 text-sm text-error" role="alert">
						<p class="font-medium">{{ jobFailure.title }}</p>
						<p>{{ jobFailure.description }}</p>
						<p class="mt-1 font-mono text-xs text-muted">Code: {{ jobFailure.code }}</p>
					</div>
				</UCard>
			</div>
		</template>
	</UDashboardPanel>
</template>
