<script setup lang="ts">
import { trackArtist, trackArtistUrl, type TrackDisplay } from "#shared/player";
const props = defineProps<{ entry: TrackDisplay }>();
const url = computed(() => trackArtistUrl(props.entry));
</script>

<template>
	<a
		v-if="url"
		:href="url"
		target="_blank"
		rel="noopener noreferrer"
		class="hover:text-highlighted hover:underline underline-offset-4"
		:title="`Find ${trackArtist(entry)} on YouTube`"
		>{{ trackArtist(entry) }}</a
	>
	<span v-else>{{ trackArtist(entry) }}</span>
</template>
