<script setup lang="ts">
import type { IncidentPeriod, IncidentSeverity } from "~/core/models/incidents";

definePageMeta({ pageTransition: { name: "page", mode: "out-in" } });
useSeoMeta({ title: "Incidents | NaHörMaar" });

const core = useNuxtApp().$backendCore;
const session = core.stores.useSessionStore();
const incidents = core.workflows.incidents();
const { icons } = useTheme();
const PAGE_SIZE = 20;
const period = ref<IncidentPeriod>("24h");
const severity = ref<IncidentSeverity | "all">("all");
const component = ref("");
const code = ref("");
const actorId = ref("");
const page = ref(1);
const periodItems = [
	{ label: "Last 24 hours", value: "24h" },
	{ label: "Last 7 days", value: "7d" },
	{ label: "Last 14 days", value: "14d" },
];
const allowed = computed(() => ["owner", "admin"].includes(session.account?.role ?? ""));
const report = computed(() => incidents.report.data.value);
const userIncidents = computed(() =>
	(report.value?.items ?? []).filter((incident) => incident.trigger === "user"),
);
const systemIncidents = computed(() =>
	(report.value?.items ?? []).filter((incident) => incident.trigger === "system"),
);
const severityItems = ["all", "warning", "error", "critical"];
let filterTimer: ReturnType<typeof setTimeout> | undefined;

const duration = (seconds: number | null) => {
	if (seconds === null) return "No history yet";
	if (seconds < 60) return `${Math.floor(seconds)}s`;
	if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
	const hours = Math.floor(seconds / 3600);
	const minutes = Math.floor((seconds % 3600) / 60);
	return minutes ? `${hours}h ${minutes}m` : `${hours}h`;
};
const dateTime = (value: string) =>
	new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(
		new Date(value),
	);
const userName = (user: {
	display_name: string | null;
	discord_username: string | null;
	user_id: string;
}) => user.display_name ?? user.discord_username ?? user.user_id;

async function load(nextPage = page.value) {
	if (!allowed.value) return;
	const next = await incidents.load({
		page: nextPage,
		pageSize: PAGE_SIZE,
		filters: {
			period: period.value,
			severity: severity.value === "all" ? undefined : severity.value,
			component: component.value.trim() || undefined,
			code: code.value.trim() || undefined,
			actorId: actorId.value.trim() || undefined,
		},
	});
	if (next) page.value = next.page;
}

watch([period, severity, component, code, actorId], () => {
	clearTimeout(filterTimer);
	filterTimer = setTimeout(() => void load(1), 250);
});
onMounted(() => void load());
onBeforeUnmount(() => {
	clearTimeout(filterTimer);
	incidents.dispose();
});
</script>

<template>
	<UDashboardPanel id="incidents" class="min-h-0 min-w-0" :ui="{ body: 'pt-4 sm:pt-4' }">
		<template #header>
			<UDashboardNavbar title="Incidents">
				<template #leading><UDashboardSidebarCollapse /></template>
				<template #right>
					<UButton
						:icon="icons.reload"
						label="Refresh"
						color="neutral"
						variant="outline"
						:loading="incidents.report.loading.value"
						@click="load()"
					/>
				</template>
			</UDashboardNavbar>
		</template>
		<template #body>
			<div v-if="!allowed" class="p-6 text-muted" role="alert">
				Only admins can read incident statistics.
			</div>
			<div v-else class="flex flex-col gap-6 pb-6">
				<div class="flex flex-wrap items-end justify-between gap-4">
					<div>
						<h1 class="text-xl font-semibold text-highlighted">What needs attention</h1>
						<p class="mt-1 text-sm text-muted">
							Structured warnings, failures and recoveries retained for 14 days.
						</p>
					</div>
					<div class="flex flex-wrap items-center justify-end gap-2">
						<USelect
							v-model="period"
							:items="periodItems"
							value-key="value"
							aria-label="Incident period"
							class="w-44"
						/>
						<USelect
							v-model="severity"
							:items="severityItems"
							aria-label="Incident severity"
							class="w-36"
						/>
					</div>
				</div>
				<div class="grid gap-2 sm:grid-cols-3">
					<UInput
						v-model="component"
						placeholder="Component"
						aria-label="Filter by component"
					/>
					<UInput
						v-model="code"
						placeholder="Error code"
						aria-label="Filter by error code"
					/>
					<UInput
						v-model="actorId"
						placeholder="Actor ID"
						aria-label="Filter by actor ID"
					/>
				</div>

				<p v-if="incidents.report.error.value" role="alert" class="text-sm text-warning">
					{{ incidents.report.error.value }}
				</p>
				<IncidentsSkeleton v-if="incidents.report.loading.value && !report" />
				<template v-else-if="report">
					<div
						class="incident-metrics grid overflow-hidden rounded-xl border border-default sm:grid-cols-2 xl:grid-cols-4"
					>
						<div class="p-5">
							<p class="flex items-center gap-2 text-sm text-muted">
								<UIcon :name="icons.warning" /> Warnings
							</p>
							<p class="mt-3 text-3xl font-semibold tabular-nums text-highlighted">
								{{ report.totals.warnings }}
							</p>
							<p class="mt-1 text-xs text-muted">Rejected operations and retries</p>
						</div>
						<div class="p-5">
							<p class="flex items-center gap-2 text-sm text-muted">
								<UIcon :name="icons.error" /> Errors
							</p>
							<p class="mt-3 text-3xl font-semibold tabular-nums text-highlighted">
								{{ report.totals.errors + report.totals.critical }}
							</p>
							<p class="mt-1 text-xs text-muted">
								{{ report.totals.critical }} critical
							</p>
						</div>
						<div class="p-5">
							<p class="flex items-center gap-2 text-sm text-muted">
								<UIcon :name="icons.success" /> Recoveries
							</p>
							<p class="mt-3 text-3xl font-semibold tabular-nums text-highlighted">
								{{ report.totals.recoveries }}
							</p>
							<p class="mt-1 text-xs text-muted">
								After {{ report.totals.retries }} retries
							</p>
						</div>
						<div class="p-5">
							<p class="flex items-center gap-2 text-sm text-muted">
								<UIcon :name="icons.clock" /> Failure-free now
							</p>
							<p class="mt-3 text-3xl font-semibold tabular-nums text-highlighted">
								{{ duration(report.current_failure_free_seconds) }}
							</p>
							<p class="mt-1 text-xs text-muted">
								Best {{ duration(report.longest_failure_free_seconds) }}
							</p>
						</div>
					</div>

					<div class="grid gap-6 xl:grid-cols-2">
						<section class="rounded-xl bg-elevated/35 p-5">
							<h2 class="text-base font-semibold text-highlighted">Common issues</h2>
							<p class="mt-1 text-sm text-muted">
								Stable codes and the components reporting them.
							</p>
							<div v-if="report.common_errors.length" class="mt-5 space-y-4">
								<div
									v-for="item in report.common_errors"
									:key="`${item.component}:${item.error_code}`"
									class="flex items-center justify-between gap-4"
								>
									<div class="min-w-0">
										<p class="truncate font-medium text-highlighted">
											{{ item.error_code }}
										</p>
										<p class="text-xs text-muted">{{ item.component }}</p>
									</div>
									<UBadge color="neutral" variant="subtle">{{
										item.count
									}}</UBadge>
								</div>
							</div>
							<p v-else class="mt-5 text-sm text-muted">
								No incidents in this period.
							</p>
						</section>
						<section class="rounded-xl bg-elevated/35 p-5">
							<h2 class="text-base font-semibold text-highlighted">
								Affected operations
							</h2>
							<p class="mt-1 text-sm text-muted">
								Where warnings and errors surfaced.
							</p>
							<div v-if="report.operations.length" class="mt-5 space-y-4">
								<div
									v-for="item in report.operations"
									:key="item.operation_type"
									class="flex items-center justify-between gap-4"
								>
									<div class="min-w-0">
										<p class="truncate font-medium text-highlighted">
											{{ item.operation_type }}
										</p>
										<p class="text-xs text-muted">
											{{ item.warnings }} warnings, {{ item.errors }} errors
										</p>
									</div>
									<UBadge color="neutral" variant="subtle">{{
										item.count
									}}</UBadge>
								</div>
							</div>
							<p v-else class="mt-5 text-sm text-muted">
								No affected operations yet.
							</p>
						</section>
					</div>

					<section
						v-if="report.associated_users.length"
						class="rounded-xl bg-elevated/35 p-5"
					>
						<h2 class="text-base font-semibold text-highlighted">
							Rejected user operations
						</h2>
						<p class="mt-1 text-sm text-muted">
							Users associated with rejected commands. This does not assign system
							failures to them.
						</p>
						<div class="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
							<NuxtLink
								v-for="user in report.associated_users"
								:key="user.user_id"
								:to="`/profile/${user.user_id}`"
								class="rounded-lg bg-default/65 p-4 transition-colors hover:bg-elevated"
							>
								<p class="truncate font-medium text-highlighted">
									{{ userName(user) }}
								</p>
								<p class="mt-1 text-xs text-muted">
									{{ user.rejected_commands }} rejected commands
								</p>
							</NuxtLink>
						</div>
					</section>

					<div class="grid gap-6 xl:grid-cols-2">
						<section class="rounded-xl bg-elevated/35 p-5">
							<h2
								class="flex items-center gap-2 text-base font-semibold text-highlighted"
							>
								<UIcon :name="icons.user" /> User-triggered
							</h2>
							<p class="mt-1 text-sm text-muted">
								Failures triggered during a user operation.
							</p>
							<div v-if="userIncidents.length" class="mt-5 space-y-3">
								<div
									v-for="item in userIncidents"
									:key="item.id"
									class="rounded-lg bg-default/65 p-4"
								>
									<div class="flex items-start justify-between gap-3">
										<div class="min-w-0">
											<p class="truncate font-medium text-highlighted">
												{{ item.error_code }}
											</p>
											<p class="mt-1 text-xs text-muted">
												{{ item.operation_type }} · {{ item.component }}
											</p>
										</div>
										<time
											class="shrink-0 text-xs text-muted"
											:datetime="item.occurred_at"
											>{{ dateTime(item.occurred_at) }}</time
										>
									</div>
								</div>
							</div>
							<p v-else class="mt-5 text-sm text-muted">
								No user-triggered incidents.
							</p>
						</section>
						<section class="rounded-xl bg-elevated/35 p-5">
							<h2
								class="flex items-center gap-2 text-base font-semibold text-highlighted"
							>
								<UIcon :name="icons.system" /> System-triggered
							</h2>
							<p class="mt-1 text-sm text-muted">
								Provider, playback and background failures.
							</p>
							<div v-if="systemIncidents.length" class="mt-5 space-y-3">
								<div
									v-for="item in systemIncidents"
									:key="item.id"
									class="rounded-lg bg-default/65 p-4"
								>
									<div class="flex items-start justify-between gap-3">
										<div class="min-w-0">
											<p class="truncate font-medium text-highlighted">
												{{ item.error_code }}
											</p>
											<p class="mt-1 text-xs text-muted">
												{{ item.operation_type }} · {{ item.component }}
											</p>
										</div>
										<time
											class="shrink-0 text-xs text-muted"
											:datetime="item.occurred_at"
											>{{ dateTime(item.occurred_at) }}</time
										>
									</div>
								</div>
							</div>
							<p v-else class="mt-5 text-sm text-muted">
								No system-triggered incidents.
							</p>
						</section>
					</div>

					<SharedPagePagination
						v-if="report.total > 0"
						v-model:page="page"
						:page-size="report.page_size"
						:total="report.total"
						:disabled="incidents.report.loading.value"
						@select="load"
					/>
				</template>
			</div>
		</template>
	</UDashboardPanel>
</template>

<style scoped>
@media (min-width: 640px) {
	.incident-metrics > :not(:first-child) {
		border-left: 1px solid var(--ui-border);
	}
}
</style>
