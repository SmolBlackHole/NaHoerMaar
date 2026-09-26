<script setup lang="ts">
import { artistNames, type Track } from "~/core/models/player";
defineOptions({ inheritAttrs: false });
const props = defineProps<{ entry: Track }>();
const artist = computed(() => artistNames(props.entry));
const url = computed(() =>
	props.entry.artists[0]
		? `https://music.youtube.com/search?q=${encodeURIComponent(props.entry.artists[0].name)}`
		: null,
);
</script>

<template>
	<UTooltip :text="url ? `Find ${artist} on YouTube Music` : artist">
		<a
			v-if="url"
			v-bind="$attrs"
			:href="url"
			target="_blank"
			rel="noopener noreferrer"
			class="hover:text-highlighted hover:underline underline-offset-4"
			>{{ artist }}</a
		>
		<span v-else v-bind="$attrs">{{ artist }}</span>
	</UTooltip>
</template>
