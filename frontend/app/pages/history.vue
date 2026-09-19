<script setup lang="ts">
import { usePlayerStore } from "~/stores/player";
useSeoMeta({ title: "Recently played | NaHörMaar" });
const player = usePlayerStore();
</script>

<template>
	<UDashboardPanel id="history" class="min-w-0">
		<template #header
			><UDashboardNavbar title="Recently played"
				><template #leading><UDashboardSidebarCollapse /></template></UDashboardNavbar
		></template>
		<template #body>
			<div class="-m-4 sm:-m-6"><PlayerNotice /></div>
			<section class="mx-auto w-full max-w-5xl py-4 sm:py-8">
				<h1 class="text-highlighted text-2xl font-semibold">Worth another listen?</h1>
				<p class="text-muted mt-3 mb-6 text-sm">
					The last 100 started tracks. Pick one to put it back in the queue.
				</p>
				<div v-if="!player.snapshot" role="status" class="text-muted py-12 text-center">
					Waiting for playback history…
				</div>
				<PlayerRecentList v-else :entries="player.snapshot.recently_played" />
			</section>
		</template>
	</UDashboardPanel>
</template>
