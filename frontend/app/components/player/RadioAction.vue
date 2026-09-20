<script setup lang="ts">
import type { QueueEntry } from "#shared/player";
import { trackTitle } from "#shared/player";
const props = defineProps<{ entry: QueueEntry; labelled?: boolean }>();
const player = usePlayerStore();
const radio = useRadioStore();
const route = useRoute();
const { icons } = useTheme();
async function open() {
	const source = {
		kind: "track" as const,
		source_url: props.entry.source_url,
		title: trackTitle(props.entry),
	};
	if (route.path !== "/") {
		await navigateTo("/");
		await nextTick();
	}
	await radio.open(source);
}
</script>

<template>
	<UButton
		v-if="labelled"
		aria-label="Radio from this track"
		title="Radio from this track"
		:icon="icons.radio"
		variant="ghost"
		color="neutral"
		class="radio-action shrink-0 justify-center"
		:disabled="!player.enabled"
		@click="open"
	>
		<span class="radio-label">Radio</span>
	</UButton>
	<UDropdownMenu
		v-else
		:items="[
			{
				label: 'Radio from this track',
				icon: icons.radio,
				disabled: !player.enabled,
				onSelect: open,
			},
		]"
		:content="{ align: 'end' }"
	>
		<UButton
			:icon="icons.ellipsis"
			:aria-label="`Options for ${trackTitle(entry)}`"
			color="neutral"
			variant="ghost"
			class="size-11 shrink-0 justify-center"
		/>
	</UDropdownMenu>
</template>

<style scoped>
.radio-action {
	min-width: 2.75rem;
	min-height: 2.75rem;
}
@container workspace (max-width: 1000px) {
	.radio-label {
		display: none;
	}
}
</style>
