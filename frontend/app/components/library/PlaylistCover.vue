<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
const props = withDefaults(defineProps<{ artworkUrls?: string[]; name?: string }>(), {
	artworkUrls: () => [],
	name: "Playlist",
});
const { icons } = useTheme();
const artwork = computed(() => [...new Set(props.artworkUrls.filter(Boolean))].slice(0, 4));
</script>

<template>
	<div
		class="playlist-cover relative grid shrink-0 overflow-hidden rounded-xl bg-elevated text-muted"
		:class="{ 'grid-cols-2': artwork.length > 1, 'grid-rows-2': artwork.length > 2 }"
		role="img"
		:aria-label="`${name} artwork`"
	>
		<img
			v-for="(url, index) in artwork"
			:key="url"
			:src="url"
			alt=""
			loading="lazy"
			decoding="async"
			class="size-full min-h-0 min-w-0 object-cover"
			:class="{ 'col-span-2': artwork.length === 3 && index === 0 }"
		/>
		<div v-if="!artwork.length" class="absolute inset-0 grid place-items-center">
			<UIcon :name="icons.music" class="size-1/3" aria-hidden="true" />
		</div>
	</div>
</template>

<style scoped>
.playlist-cover {
	grid-auto-flow: dense;
	box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--ui-border) 70%, transparent);
}
</style>
