<script setup lang="ts">
interface ArtworkEntry {
	artwork_url?: string | null;
	thumbnail_url?: string | null;
}

const props = defineProps<{ entry: ArtworkEntry | null; large?: boolean }>();
const { icons } = useTheme();
const failed = ref(false);
const consent = useConsentStore();
const source = computed(() =>
	consent.youtube ? (props.entry?.artwork_url ?? props.entry?.thumbnail_url ?? null) : null,
);
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
