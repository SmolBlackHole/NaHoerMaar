<script setup lang="ts">
import type { QueueEntry } from "#shared/player";
import { trackArtwork } from "#shared/player";

const props = defineProps<{ entry: QueueEntry | null; large?: boolean }>();
const { icons } = useTheme();
const failed = ref(false);
const source = computed(() => trackArtwork(props.entry));
watch(source, () => {
	failed.value = false;
});
</script>

<template>
	<div
		class="bg-elevated flex shrink-0 items-center justify-center overflow-hidden rounded-lg"
		:class="large ? 'aspect-video w-full' : 'size-12'"
	>
		<img
			v-if="source && !failed"
			:src="source"
			alt=""
			:loading="large ? 'eager' : 'lazy'"
			referrerpolicy="no-referrer"
			class="size-full object-cover"
			@error="failed = true"
		/>
		<UIcon
			v-else
			:name="icons.music"
			class="text-muted"
			:class="large ? 'size-16' : 'size-5'"
		/>
	</div>
</template>
