<script setup lang="ts">
import type { LogPage } from "~/core/models/logs";

type LogEntry = LogPage["entries"][number];

definePageMeta({ pageTransition: { name: "page", mode: "out-in" } });
useSeoMeta({ title: "Bot logs | NaHörMaar" });
const core = useNuxtApp().$backendCore;
const session = core.stores.useSessionStore();
const logs = core.workflows.logs();
const level = ref("all");
const filter = ref("");
const live = ref(true);
const viewport = ref<HTMLElement | null>(null);
const visibility = useDocumentVisibility();
const levels = ["all", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];
const allowed = computed(() => ["owner", "admin"].includes(session.account?.role ?? ""));
const entries = computed(() => logs.page.data.value?.entries ?? []);
const visibleEntries = computed(() => {
	const needle = filter.value.trim().toLocaleLowerCase();
	return entries.value.filter(
		(entry) =>
			(level.value === "all" || entry.level === level.value) &&
			(!needle ||
				[
					entry.actor_id,
					entry.source,
					entry.message,
					entry.request_id,
					entry.message_id,
					entry.correlation_id,
					entry.causation_id,
				].some((value) => value?.toLocaleLowerCase().includes(needle))),
	);
});
let timer: ReturnType<typeof setTimeout> | undefined;
let disposed = false;

const time = (value: string) =>
	new Intl.DateTimeFormat(undefined, { dateStyle: "short", timeStyle: "medium" }).format(
		new Date(value),
	);
const reference = (entry: LogEntry) =>
	entry.correlation_id ?? entry.request_id ?? entry.message_id ?? entry.causation_id;
const shortReference = (entry: LogEntry) => reference(entry)?.slice(0, 8) ?? "system";
const copyReference = (entry: LogEntry) => {
	const value = reference(entry);
	if (value) void globalThis.navigator.clipboard.writeText(value);
};

async function poll() {
	if (disposed || !live.value || !allowed.value || visibility.value !== "visible") return;
	const atBottom =
		!viewport.value ||
		viewport.value.scrollHeight - viewport.value.scrollTop - viewport.value.clientHeight < 64;
	await logs.poll(200);
	if (atBottom)
		await nextTick(() => viewport.value?.scrollTo({ top: viewport.value!.scrollHeight }));
}
function schedule() {
	clearTimeout(timer);
	if (!disposed && live.value && allowed.value)
		timer = setTimeout(async () => {
			await poll();
			schedule();
		}, 2000);
}
async function start() {
	if (!allowed.value) return;
	await logs.loadLatest(200);
	await nextTick(() => viewport.value?.scrollTo({ top: viewport.value!.scrollHeight }));
	schedule();
}
watch([live, allowed, visibility], () => {
	clearTimeout(timer);
	if (live.value && allowed.value && visibility.value === "visible") void start();
});
onMounted(start);
onBeforeUnmount(() => {
	disposed = true;
	clearTimeout(timer);
	logs.dispose();
});
</script>

<template>
	<UDashboardPanel id="bot-logs" class="min-h-0 min-w-0" :ui="{ body: 'pt-4 sm:pt-4' }">
		<template #header>
			<UDashboardNavbar title="Bot logs">
				<template #leading><UDashboardSidebarCollapse /></template>
				<template #right>
					<span v-if="allowed" class="text-xs text-muted">{{
						live ? "Live" : "Paused"
					}}</span>
				</template>
			</UDashboardNavbar>
		</template>
		<template #body>
			<div v-if="!allowed" class="p-6 text-muted" role="alert">
				Only admins can read bot logs.
			</div>
			<div v-else class="logs-page flex min-h-0 flex-col gap-4 pb-4 sm:pb-6">
				<div class="flex flex-wrap items-end justify-between gap-3">
					<div>
						<h1 class="text-xl font-semibold text-highlighted">Live diagnostics</h1>
						<p class="mt-1 text-sm text-muted">
							Actor, request and event context from the current backend.
						</p>
					</div>
					<div class="flex flex-wrap items-center gap-2">
						<UInput
							v-model="filter"
							icon="i-lucide-search"
							placeholder="Filter actor, source, message, or ID"
							aria-label="Filter logs"
							class="w-72 max-w-full"
						/>
						<USelect
							v-model="level"
							:items="levels"
							aria-label="Log level"
							class="w-32"
						/>
						<UButton
							:label="live ? 'Pause' : 'Resume'"
							:icon="live ? 'i-lucide-pause' : 'i-lucide-play'"
							color="neutral"
							variant="outline"
							@click="live = !live"
						/>
						<UButton
							label="Clear view"
							color="neutral"
							variant="ghost"
							@click="
								logs.page.set({
									entries: [],
									cursor: logs.page.data.value?.cursor ?? 0,
								})
							"
						/>
					</div>
				</div>
				<p v-if="logs.page.error.value" role="alert" class="text-sm text-warning">
					{{ logs.page.error.value }}
				</p>
				<div
					ref="viewport"
					class="log-viewport rounded-lg border border-default bg-elevated/30"
					role="log"
					aria-label="Bot logs"
					aria-live="off"
				>
					<div v-if="!visibleEntries.length" class="p-6 text-sm text-muted">
						{{
							logs.page.loading.value
								? "Loading logs…"
								: "No log messages in this view yet."
						}}
					</div>
					<div
						v-for="entry in visibleEntries"
						:key="entry.id"
						class="log-row grid gap-x-3 px-4 py-2 text-xs hover:bg-elevated/60 sm:grid-cols-[10rem_5rem_6rem_10rem_12rem_minmax(0,1fr)] sm:py-1.5"
					>
						<time class="text-muted tabular-nums" :datetime="entry.timestamp">{{
							time(entry.timestamp)
						}}</time>
						<span
							:class="
								entry.level === 'ERROR' || entry.level === 'CRITICAL'
									? 'text-error'
									: entry.level === 'WARNING'
										? 'text-warning'
										: 'text-primary'
							"
							class="font-semibold"
							>{{ entry.level }}</span
						>
						<UTooltip
							v-if="reference(entry)"
							:text="`Copy correlation or request ID ${reference(entry)}`"
						>
							<button
								type="button"
								class="log-trace cursor-copy truncate text-left text-muted hover:text-highlighted"
								@click="copyReference(entry)"
							>
								{{ shortReference(entry) }}
							</button>
						</UTooltip>
						<span v-else class="log-trace text-dimmed">system</span>
						<UTooltip :text="entry.actor_id ?? 'Background task or system event'">
							<NuxtLink
								v-if="entry.actor_id"
								:to="`/profile/${entry.actor_id}`"
								class="log-actor truncate text-muted hover:text-highlighted"
							>
								{{ entry.actor_id }}
							</NuxtLink>
							<span v-else class="log-actor truncate text-muted">System</span>
						</UTooltip>
						<UTooltip :text="entry.source">
							<span class="log-source truncate text-muted">{{ entry.source }}</span>
						</UTooltip>
						<span class="log-message min-w-0 break-all text-highlighted">{{
							entry.message
						}}</span>
					</div>
				</div>
			</div>
		</template>
	</UDashboardPanel>
</template>

<style scoped>
.logs-page {
	height: 100%;
}
.log-viewport {
	min-height: 12rem;
	flex: 1;
	overflow: auto;
	font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
}
.log-row + .log-row {
	border-top: 1px solid color-mix(in srgb, currentColor 6%, transparent);
}
@media (max-width: 639px) {
	.log-row {
		grid-template-columns: auto 1fr;
	}
	.log-actor,
	.log-trace,
	.log-source,
	.log-message {
		grid-column: 1 / -1;
	}
}
</style>
