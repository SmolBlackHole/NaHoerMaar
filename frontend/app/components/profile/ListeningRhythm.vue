<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
import type { PersonalStatistics } from "~/core/models/statistics";
import { formatStatisticsDuration } from "~/core/models/statistics";

type Pattern = PersonalStatistics["highlights"]["listening_pattern"];

const props = defineProps<{ pattern: Pattern }>();
const weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const maxWeekday = computed(() =>
	Math.max(1, ...props.pattern.weekdays.map((item) => item.listening_seconds)),
);
</script>

<template>
	<section aria-labelledby="listening-rhythm-heading">
		<div>
			<h2 id="listening-rhythm-heading" class="text-lg font-semibold text-highlighted">
				Listening rhythm
			</h2>
			<p class="mt-1 text-xs text-muted">When this listener usually hears music.</p>
		</div>

		<div class="mt-5 space-y-2.5">
			<div
				v-for="(item, index) in pattern.weekdays"
				:key="item.iso_weekday"
				class="grid grid-cols-[2.25rem_minmax(0,1fr)_3.75rem] items-center gap-3"
			>
				<span class="text-xs font-medium text-muted">{{ weekdays[index] }}</span>
				<UTooltip
					:text="`${weekdays[index]}: ${formatStatisticsDuration(item.listening_seconds)}`"
				>
					<div class="h-2 overflow-hidden rounded-full bg-elevated">
						<div
							class="h-full rounded-full bg-primary transition-[width]"
							:style="{
								width: `${item.listening_seconds ? Math.max(5, (item.listening_seconds / maxWeekday) * 100) : 0}%`,
							}"
						/>
					</div>
				</UTooltip>
				<span class="text-right text-xs tabular-nums text-muted">
					{{ formatStatisticsDuration(item.listening_seconds) }}
				</span>
			</div>
		</div>
	</section>
</template>
