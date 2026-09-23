<script setup lang="ts">
import type { components } from "#shared/api.generated";
import { useRepositories } from "~/repositories";
import { useProfileStore } from "~/stores/profile";

type LogEntry = components["schemas"]["LogEntryView"];

useSeoMeta({ title: "Bot logs | NaHörMaar" });
const profile = useProfileStore();
const { diagnostics } = useRepositories();
const entries = ref<LogEntry[]>([]);
const level = ref("all");
const live = ref(true);
const loading = ref(false);
const error = ref("");
const viewport = ref<HTMLElement | null>(null);
let cursor: number | undefined;
let timer: ReturnType<typeof setTimeout> | undefined;
let request: AbortController | undefined;
let disposed = false;
let generation = 0;
const levels = ["all", "INFO", "WARNING", "ERROR", "CRITICAL"];
const visibleEntries = computed(() =>
	level.value === "all"
		? entries.value
		: entries.value.filter((entry) => entry.level === level.value),
);
const time = (value: string) =>
	new Intl.DateTimeFormat(undefined, { dateStyle: "short", timeStyle: "medium" }).format(
		new Date(value),
	);

function stop() {
	generation++;
	clearTimeout(timer);
	request?.abort();
	request = undefined;
	loading.value = false;
}

async function poll() {
	if (!live.value || !profile.session?.is_admin || document.hidden) {
		if (!disposed && live.value && profile.session?.is_admin) timer = setTimeout(poll, 2000);
		return;
	}
	const version = generation;
	const controller = new AbortController();
	request = controller;
	loading.value = true;
	try {
		const result = await diagnostics.logs(cursor, controller.signal);
		if (version !== generation) return;
		const atBottom =
			!viewport.value ||
			viewport.value.scrollHeight - viewport.value.scrollTop - viewport.value.clientHeight <
				64;
		if (result.entries.length) {
			cursor = result.entries.at(-1)!.id;
			entries.value = [...entries.value, ...result.entries].slice(-500);
			if (atBottom)
				await nextTick(() =>
					viewport.value?.scrollTo({ top: viewport.value.scrollHeight }),
				);
		}
		error.value = "";
	} catch (failure) {
		if (
			version === generation &&
			!(failure instanceof DOMException && failure.name === "AbortError")
		)
			error.value = "Could not load bot logs. Retrying…";
	} finally {
		if (request === controller) request = undefined;
		if (version === generation) {
			loading.value = false;
			if (!disposed && live.value && profile.session?.is_admin)
				timer = setTimeout(poll, 2000);
		}
	}
}

watch(
	[live, () => profile.session?.is_admin],
	() => {
		if (!import.meta.client) return;
		stop();
		if (live.value && profile.session?.is_admin) void poll();
	},
	{ immediate: true },
);
onBeforeUnmount(() => {
	disposed = true;
	stop();
});
</script>

<template>
	<UDashboardPanel
		id="bot-logs"
		class="min-h-0 min-w-0"
		:ui="{ body: 'pt-4 sm:pt-4' }"
	>
		<template #header>
			<UDashboardNavbar title="Bot logs">
				<template #leading><UDashboardSidebarCollapse /></template>
				<template #right>
					<span v-if="profile.session?.is_admin" class="text-muted text-xs">
						{{ live ? "Live" : "Paused" }}
					</span>
				</template>
			</UDashboardNavbar>
		</template>
		<template #body>
			<div v-if="!profile.session?.is_admin" class="p-6 text-muted" role="alert">
				Only admins can read bot logs.
			</div>
			<div v-else class="logs-page flex min-h-0 flex-col gap-4 pb-4 sm:pb-6">
				<div class="flex flex-wrap items-end justify-between gap-3">
					<div>
						<h1 class="text-highlighted text-xl font-semibold">Live diagnostics</h1>
						<p class="text-muted mt-1 text-sm">
							Recent bot events from this run. Older messages disappear automatically.
						</p>
					</div>
					<div class="flex items-center gap-2">
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
							@click="entries = []"
						/>
					</div>
				</div>
				<p v-if="error" role="alert" class="text-warning text-sm">{{ error }}</p>
				<div
					ref="viewport"
					class="log-viewport rounded-lg border border-default bg-elevated/30"
					role="log"
					aria-label="Bot logs"
					aria-live="off"
				>
					<div v-if="!visibleEntries.length" class="text-muted p-6 text-sm">
						{{ loading ? "Loading logs…" : "No log messages in this view yet." }}
					</div>
					<div
						v-for="entry in visibleEntries"
						:key="entry.id"
						class="log-row grid gap-x-3 px-4 py-2 text-xs hover:bg-elevated/60 sm:grid-cols-[10rem_5rem_12rem_minmax(0,1fr)] sm:py-1.5"
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
						<span class="text-muted truncate" :title="entry.source">{{
							entry.source
						}}</span>
						<span class="min-w-0 break-all text-highlighted">{{ entry.message }}</span>
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
	.log-row span:nth-of-type(2),
	.log-row span:nth-of-type(3) {
		grid-column: 1 / -1;
	}
}
</style>
