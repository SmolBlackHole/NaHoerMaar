<script setup lang="ts">
import type { TrackRequest } from "~/core/models/player";
const props = defineProps<{
	entry?: TrackRequest;
	sourceId?: string | null;
	title?: string;
	labelled?: boolean;
}>();
const radioLabel = "Start a radio from the currently playing track";
const player = useNuxtApp().$backendCore.stores.usePlayerStore();
const { icons } = useTheme();
const confirming = ref(false);
const entryTitle = computed(() => props.title ?? props.entry?.track.title ?? "track");
async function start() {
	const sourceId = props.sourceId ?? props.entry?.source_id ?? props.entry?.track.sources[0]?.id;
	if (!sourceId) return null;
	return player.startRadio({
		kind: "track",
		track_source_id: sourceId,
		discovery_snapshot_id: null,
	});
}
function request() {
	if (player.state?.radio) confirming.value = true;
	else void start();
}
async function confirm() {
	if (await start()) confirming.value = false;
}
</script>

<template>
	<UTooltip v-if="labelled" :text="radioLabel">
		<UButton
			:aria-label="radioLabel"
			:icon="icons.radio"
			variant="ghost"
			color="neutral"
			class="radio-action shrink-0 justify-center"
			:disabled="!player.canControl"
			@click="request"
		>
			<span class="radio-label">Radio</span>
		</UButton>
	</UTooltip>
	<UDropdownMenu
		v-else
		:items="[
			{
				label: radioLabel,
				icon: icons.radio,
				disabled: !player.canControl,
				onSelect: request,
			},
		]"
		:content="{ align: 'end' }"
	>
		<UTooltip :text="`Options for ${entryTitle}`">
			<UButton
				:icon="icons.ellipsis"
				:aria-label="`Options for ${entryTitle}`"
				color="neutral"
				variant="ghost"
				class="size-11 shrink-0 justify-center"
			/>
		</UTooltip>
	</UDropdownMenu>
	<PlayerRadioReplaceConfirmation
		:open="confirming"
		:busy="player.isPending('radio.start')"
		@update:open="confirming = $event"
		@confirm="confirm"
	/>
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
