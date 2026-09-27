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
const maxHour = computed(() =>
	Math.max(1, ...props.pattern.hours.map((item) => item.listening_seconds)),
);

function hourLabel(hour: number) {
	return `${String(hour).padStart(2, "0")}:00`;
}

function hourIntensity(seconds: number) {
	if (!seconds) return "bg-elevated/45 ring-default/70";
	const ratio = seconds / maxHour.value;
	if (ratio <= 0.2) return "bg-primary/15 ring-primary/20";
	if (ratio <= 0.4) return "bg-primary/30 ring-primary/30";
	if (ratio <= 0.6) return "bg-primary/50 ring-primary/40";
	if (ratio <= 0.8) return "bg-primary/70 ring-primary/50";
	return "bg-primary ring-primary/70";
}
</script>

<template>
	<section aria-labelledby="listening-rhythm-heading">
		<div>
			<h2 id="listening-rhythm-heading" class="text-lg font-semibold text-highlighted">
				Listening rhythm
			</h2>
			<p class="mt-1 text-xs text-muted">When this listener usually hears music.</p>
		</div>

		<div class="mt-5 grid gap-7 xl:grid-cols-[minmax(18rem,0.8fr)_minmax(0,1.2fr)]">
			<div class="space-y-2.5">
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

			<div>
				<div class="grid grid-cols-8 gap-1.5 sm:grid-cols-12">
					<UTooltip
						v-for="item in pattern.hours"
						:key="item.hour"
						:text="`${hourLabel(item.hour)}: ${formatStatisticsDuration(item.listening_seconds)}`"
					>
						<div
							class="aspect-square rounded-md ring-1 ring-inset transition-[background-color,box-shadow,transform] duration-150 hover:scale-105"
							:class="hourIntensity(item.listening_seconds)"
						/>
					</UTooltip>
				</div>
				<div class="mt-2 flex justify-between text-[0.6875rem] text-muted">
					<span>Midnight</span>
					<span>Noon</span>
					<span>23:00</span>
				</div>
			</div>
		</div>
	</section>
</template>
