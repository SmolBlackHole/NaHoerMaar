<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
import type { GroupStatistics } from "~/core/models/statistics";

const props = defineProps<{ coverage: GroupStatistics["coverage"] }>();
const { icons } = useTheme();
const recordedSince = computed(() => {
	if (!props.coverage.recorded_since) return null;
	return new Intl.DateTimeFormat(undefined, {
		dateStyle: "medium",
		timeStyle: "short",
	}).format(new Date(props.coverage.recorded_since));
});
</script>

<template>
	<div
		v-if="coverage.partial"
		class="flex items-start gap-3 rounded-xl bg-elevated/35 px-4 py-3 text-xs text-muted"
	>
		<UIcon :name="icons.info" class="mt-0.5 size-4 shrink-0 text-primary" />
		<p>
			Available data is shown in full.
			<template v-if="recordedSince">
				Recording began {{ recordedSince }}, so earlier activity in this period is not
				included.
			</template>
			<template v-else> Earlier activity in this period was not recorded. </template>
		</p>
	</div>
</template>
