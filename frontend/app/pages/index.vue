<script setup lang="ts">
import { TabsContent, TabsList, TabsRoot, TabsTrigger } from "reka-ui";
import { usePlayerStore } from "~/stores/player";

useSeoMeta({ title: "Player | NaHörMaar" });
const player = usePlayerStore();
const { icons } = useTheme();
const route = useRoute();
const router = useRouter();
const selected = computed({
	get: () => (route.query.view === "queue" ? "queue" : "player"),
	set: (value: string | number) => {
		void router.replace({
			query: { ...route.query, view: value === "queue" ? "queue" : undefined },
		});
	},
});
const tabs = computed(() => [
	{ label: "Player", value: "player", icon: icons.value.music },
	{ label: "Queue", value: "queue", icon: icons.value.list },
]);
async function openQueue() {
	await router.replace({ query: { ...route.query, view: "queue" } });
	await nextTick();
	requestAnimationFrame(() => {
		if (selected.value === "queue") document.getElementById("youtube-link")?.focus();
	});
}
</script>

<template>
	<TabsRoot v-model="selected" :unmount-on-hide="false" class="player-tabs">
		<UDashboardPanel
			id="music-player"
			class="player-page min-w-0"
			:class="{ 'is-listening': selected === 'player' }"
			:ui="{ body: 'pt-0 sm:pt-0' }"
		>
			<template #header>
				<UDashboardNavbar class="player-navbar" :ui="{ left: 'h-full gap-3 sm:gap-4' }">
					<template #left>
						<UDashboardSidebarCollapse />
						<h1 class="sr-only">Music player</h1>
						<TabsList aria-label="Player views" class="music-tab-list">
							<TabsTrigger
								v-for="tab in tabs"
								:key="tab.value"
								:value="tab.value"
								class="music-tab-trigger"
							>
								<UIcon :name="tab.icon" class="size-5 shrink-0" />
								{{ tab.label }}
								<span v-if="tab.value === 'queue'" class="tab-count">{{
									player.snapshot?.upcoming.length ?? 0
								}}</span>
							</TabsTrigger>
						</TabsList>
					</template>
					<template #right>
						<PlayerVoiceChannel compact />
						<PlayerConnection />
					</template>
				</UDashboardNavbar>
			</template>
			<template #body>
				<Transition name="player-view" :duration="{ enter: 200, leave: 0 }">
					<TabsContent
						v-show="selected === 'player'"
						force-mount
						value="player"
						class="music-tab-content"
						:inert="selected !== 'player'"
					>
						<PlayerNowPlaying :active="selected === 'player'" @queue="openQueue" />
					</TabsContent>
				</Transition>
				<Transition name="player-view" :duration="{ enter: 200, leave: 0 }">
					<TabsContent
						v-show="selected === 'queue'"
						force-mount
						value="queue"
						class="music-tab-content"
						:inert="selected !== 'queue'"
					>
						<div class="queue-workspace">
							<PlayerQueue />
							<PlayerHistory />
						</div>
					</TabsContent>
				</Transition>
			</template>
		</UDashboardPanel>
	</TabsRoot>
</template>

<style>
.player-tabs {
	display: flex;
	flex-direction: column;
	flex: 1;
	min-width: 0;
	min-height: 0;
}
.music-tab-list {
	display: flex;
	align-items: stretch;
	height: 100%;
	padding: 0;
	gap: 1.5rem;
}
.music-tab-trigger {
	position: relative;
	display: inline-flex;
	align-items: center;
	min-height: 2.75rem;
	padding-inline: 0.125rem;
	border-radius: 0;
	gap: 0.5rem;
	color: var(--ui-text-muted);
	font-size: 0.875rem;
	font-weight: 500;
	white-space: nowrap;
	transition: color 140ms ease-out;
}
.music-tab-trigger:hover {
	color: var(--ui-text-highlighted);
}
.music-tab-trigger[data-state="active"] {
	color: var(--ui-text-highlighted);
}
.music-tab-trigger[data-state="active"]::after {
	content: "";
	position: absolute;
	inset-inline: 0;
	bottom: 0;
	height: 2px;
	background: var(--ui-primary);
}
.tab-count {
	color: var(--ui-text-muted);
	font-size: 0.6875rem;
	font-variant-numeric: tabular-nums;
}
.music-tab-content {
	min-width: 0;
	outline-offset: 4px;
}
.player-view-enter-active {
	transition: opacity 200ms ease-out;
}
.player-view-enter-from {
	opacity: 0;
}
.player-view-leave-active {
	display: none !important;
}
@media (prefers-reduced-motion: reduce) {
	.player-view-enter-active {
		transition: none;
	}
}
.player-page {
	isolation: isolate;
}
.player-page.is-listening {
	--ui-text: #e4e4e7;
	--ui-text-highlighted: #fafafa;
	--ui-text-muted: #b6b8c0;
	--ui-text-dimmed: #a1a1aa;
	--ui-primary: var(--color-primary-400);
	color: var(--ui-text);
}
.is-listening .music-tab-content[data-state="active"] {
	display: flex;
	flex: 1;
}
.is-listening .player-navbar {
	position: relative;
	z-index: 2;
}
.queue-workspace {
	width: 100%;
}
.is-listening :focus-visible {
	outline-color: #fff;
}
@container workspace (max-width: 600px) {
	.music-tab-list {
		gap: 1rem;
	}
}
</style>
