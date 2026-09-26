<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
defineProps<{ open: boolean; busy?: boolean }>();
const emit = defineEmits<{
	"update:open": [value: boolean];
	confirm: [];
}>();
</script>

<template>
	<UModal
		:open="open"
		title="Replace the active radio?"
		description="The current radio stops finding tracks. Existing queue entries stay in place."
		:ui="{ footer: 'justify-end' }"
		@update:open="emit('update:open', $event)"
	>
		<template #footer>
			<UButton
				label="Keep current radio"
				color="neutral"
				variant="outline"
				@click="emit('update:open', false)"
			/>
			<UButton
				label="Replace radio"
				color="primary"
				:loading="busy"
				@click="emit('confirm')"
			/>
		</template>
	</UModal>
</template>
