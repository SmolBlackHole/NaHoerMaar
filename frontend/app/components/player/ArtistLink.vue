<script setup lang="ts">
import { trackArtist, trackArtistUrl, type TrackDisplay } from "#shared/player";
defineOptions({ inheritAttrs: false });
const props = defineProps<{ entry: TrackDisplay }>();
const url = computed(() => trackArtistUrl(props.entry));
</script>

<template>
	<UTooltip :text="url ? `Find ${trackArtist(entry)} on YouTube` : trackArtist(entry)">
		<a
			v-if="url"
			v-bind="$attrs"
			:href="url"
			target="_blank"
			rel="noopener noreferrer"
			class="hover:text-highlighted hover:underline underline-offset-4"
			>{{ trackArtist(entry) }}</a
		>
		<span v-else v-bind="$attrs">{{ trackArtist(entry) }}</span>
	</UTooltip>
</template>
