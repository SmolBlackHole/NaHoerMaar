<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
import type { GroupStatistics } from "~/core/models/statistics";
import { formatStatisticsDuration } from "~/core/models/statistics";

type Highlight = {
	key: string;
	label: string;
	value: string;
	detail: string;
	icon: string;
	links?: readonly { label: string; to: string }[];
};

const props = defineProps<{ report: GroupStatistics }>();
const { icons } = useTheme();

function listenerName(
	listener: NonNullable<GroupStatistics["highlights"]["listener_pair"]>["first"],
) {
	return (
		listener.display_name ??
		listener.discord_display_name ??
		listener.discord_username ??
		"Listener"
	);
}

const highlights = computed<Highlight[]>(() => {
	const source = props.report.highlights;
	const items: Highlight[] = [];
	if (source.listener_pair) {
		const first = source.listener_pair.first;
		const second = source.listener_pair.second;
		items.push({
			key: "listener-pair",
			label: "Listening buddies",
			value: `${listenerName(first)} + ${listenerName(second)}`,
			detail: `${source.listener_pair.shared_playbacks} confirmed playbacks heard together.`,
			icon: icons.value.users,
			links: [
				{ label: listenerName(first), to: `/profile/${first.user_id}` },
				{ label: listenerName(second), to: `/profile/${second.user_id}` },
			],
		});
	}
	if (source.most_shared_track) {
		items.push({
			key: "shared-track",
			label: "Most shared track",
			value: source.most_shared_track.title,
			detail: `${source.most_shared_track.distinct_listeners} listeners across ${source.most_shared_track.plays} confirmed plays.`,
			icon: icons.value.headphones,
		});
	}
	if (source.radio_conversion) {
		items.push({
			key: "radio-conversion",
			label: "Radio found a keeper",
			value: source.radio_conversion.title,
			detail: `${source.radio_conversion.distinct_requesters} listeners manually requested it after Radio played it.`,
			icon: icons.value.radio,
		});
	}
	if (source.contagious_track) {
		const requester = source.contagious_track.original_requester;
		items.push({
			key: "contagious-track",
			label: "Caught on",
			value: source.contagious_track.title,
			detail: `${source.contagious_track.distinct_later_requesters} other listeners requested it later.`,
			icon: icons.value.sparkles,
			links: [
				{
					label: `Started by ${listenerName(requester)}`,
					to: `/profile/${requester.user_id}`,
				},
			],
		});
	}
	if (source.busiest_weekday && items.length < 4) {
		const weekday = new Intl.DateTimeFormat("en", {
			weekday: "long",
			timeZone: "UTC",
		}).format(new Date(Date.UTC(2026, 0, 4 + source.busiest_weekday.iso_weekday)));
		items.push({
			key: "weekday",
			label: "Busiest day",
			value: weekday,
			detail: `${formatStatisticsDuration(source.busiest_weekday.playback_seconds)} of playback in this period.`,
			icon: icons.value.clock,
		});
	}
	if (source.busiest_hour && items.length < 4) {
		items.push({
			key: "hour",
			label: "Prime time",
			value: `${String(source.busiest_hour.hour).padStart(2, "0")}:00`,
			detail: `${formatStatisticsDuration(source.busiest_hour.playback_seconds)} of playback started in this hour.`,
			icon: icons.value.clock,
		});
	}
	if (items.length < 4 && source.active_day_streaks.longest > 0) {
		items.push({
			key: "streak",
			label: "Longest streak",
			value: `${source.active_day_streaks.longest} ${source.active_day_streaks.longest === 1 ? "day" : "days"}`,
			detail: source.active_day_streaks.current
				? `${source.active_day_streaks.current} day current streak.`
				: "No active streak today.",
			icon: icons.value.play,
		});
	}
	return items.slice(0, 4);
});
</script>

<template>
	<section v-if="highlights.length" aria-labelledby="highlights-heading">
		<h2 id="highlights-heading" class="text-lg font-semibold text-highlighted">
			Group highlights
		</h2>
		<p class="mt-1 text-xs text-muted">The small stories hiding inside this period.</p>
		<div class="mt-4 grid gap-3 sm:grid-cols-2">
			<article
				v-for="(item, index) in highlights"
				:key="item.key"
				class="group relative min-w-0 overflow-hidden rounded-2xl bg-elevated/40 p-5 transition-[transform,background-color] duration-200 hover:-translate-y-0.5 hover:bg-elevated/65"
			>
				<span
					class="pointer-events-none absolute -right-1 -top-4 text-7xl font-bold tabular-nums text-primary/5"
					aria-hidden="true"
				>
					{{ String(index + 1).padStart(2, "0") }}
				</span>
				<div class="flex items-center gap-3 text-xs font-medium text-muted">
					<span
						class="grid size-9 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary transition-transform duration-200 group-hover:rotate-3 group-hover:scale-105"
					>
						<UIcon :name="item.icon" class="size-4" />
					</span>
					<span>{{ item.label }}</span>
				</div>
				<p class="mt-3 truncate text-lg font-semibold text-highlighted">{{ item.value }}</p>
				<p class="mt-1 text-sm leading-relaxed text-muted">{{ item.detail }}</p>
				<div v-if="item.links?.length" class="mt-3 flex flex-wrap gap-x-3 gap-y-1">
					<NuxtLink
						v-for="link in item.links"
						:key="link.to"
						:to="link.to"
						class="text-xs font-medium text-primary hover:underline"
					>
						{{ link.label }}
					</NuxtLink>
				</div>
			</article>
		</div>
	</section>
</template>
