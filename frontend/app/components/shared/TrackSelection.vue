<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
const props = withDefaults(
	defineProps<{
		active: boolean;
		selectedCount: number;
		availableCount: number;
		startLabel?: string;
		selectAllLabel?: string;
		allowSelectAll?: boolean;
		disabled?: boolean;
	}>(),
	{
		startLabel: "Select",
		selectAllLabel: "Select all",
		allowSelectAll: false,
		disabled: false,
	},
);
const emit = defineEmits<{
	start: [];
	clear: [];
	selectAll: [];
	done: [];
}>();
const { icons } = useTheme();
</script>

<template>
	<div class="track-selection">
		<template v-if="active">
			<span class="track-selection__count text-xs tabular-nums text-muted">
				{{ selectedCount }} selected
			</span>
			<UButton
				v-if="allowSelectAll"
				:label="selectAllLabel"
				color="neutral"
				variant="ghost"
				:disabled="disabled || !availableCount || selectedCount === availableCount"
				@click="emit('selectAll')"
			/>
			<UButton
				label="Clear"
				color="neutral"
				variant="ghost"
				:disabled="disabled || !selectedCount"
				@click="emit('clear')"
			/>
			<slot name="actions" />
			<UButton
				label="Done"
				:icon="icons.check"
				color="neutral"
				variant="soft"
				:disabled="disabled"
				@click="emit('done')"
			/>
		</template>
		<template v-else>
			<UButton
				:label="startLabel"
				:icon="icons.check"
				color="neutral"
				variant="ghost"
				:disabled="disabled || !availableCount"
				@click="emit('start')"
			/>
			<slot name="idle" />
		</template>
	</div>
</template>

<style scoped>
.track-selection {
	display: flex;
	flex-wrap: wrap;
	align-items: center;
	justify-content: flex-end;
	gap: 0.25rem;
}
.track-selection__count {
	margin-inline-end: 0.5rem;
}
@container workspace (max-width: 600px) {
	.track-selection {
		justify-content: flex-start;
	}
}
</style>
