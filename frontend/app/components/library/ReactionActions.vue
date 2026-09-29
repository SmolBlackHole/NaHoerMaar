<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
import type { DropdownMenuItem } from "@nuxt/ui";
import type { ReactionValue } from "~/core/models/library";

defineOptions({ inheritAttrs: false });

const props = withDefaults(
	defineProps<{
		trackId: string;
		title: string;
		compact?: boolean;
		showCounts?: boolean;
		showDetails?: boolean;
		mode?: "split" | "menu";
	}>(),
	{ compact: false, showCounts: true, showDetails: true, mode: "split" },
);
const attrs = useAttrs();
const core = useNuxtApp().$backendCore;
const session = core.stores.useSessionStore();
const reactions = core.stores.useLibraryStore();
const toast = useToast();
const { icons } = useTheme();
const detailsOpen = ref(false);
const summary = computed(() => reactions.summary(props.trackId));
const pending = computed(() => reactions.isPending(props.trackId));
const total = computed(() => (summary.value?.likes ?? 0) + (summary.value?.dislikes ?? 0));
const menuIcon = computed(() => {
	if (summary.value?.reaction === "dislike") return icons.value.dislike;
	return icons.value.like;
});
const menuItems = computed<DropdownMenuItem[][]>(() => {
	const reaction = summary.value?.reaction;
	const choices: DropdownMenuItem[] = [
		{
			label: reaction === "like" ? "Remove Like" : "Like",
			icon: icons.value.like,
			onSelect: () => toggle("like"),
		},
		{
			label: reaction === "dislike" ? "Remove Dislike" : "Dislike",
			icon: icons.value.dislike,
			onSelect: () => toggle("dislike"),
		},
	];
	if (props.showDetails)
		choices.push({
			label: total.value ? `See ${total.value} reactions` : "See reactions",
			icon: icons.value.users,
			onSelect: () => {
				detailsOpen.value = true;
			},
		});
	return [choices];
});

watch(
	() => [props.trackId, session.status] as const,
	([trackId, status]) => {
		if (status === "authenticated") void reactions.load([trackId]);
	},
	{ immediate: true },
);

async function toggle(value: ReactionValue) {
	const saved = await reactions.toggleReaction(props.trackId, value);
	if (!saved && reactions.error)
		toast.add({
			title: "Reaction not saved",
			description: reactions.error,
			color: "warning",
		});
}

function count(value: ReactionValue) {
	return value === "like" ? (summary.value?.likes ?? 0) : (summary.value?.dislikes ?? 0);
}

function countLabel(value: ReactionValue) {
	const amount = count(value);
	const noun = value === "like" ? "Like" : "Dislike";
	return `${amount} ${noun}${amount === 1 ? "" : "s"}`;
}
</script>

<template>
	<div
		v-if="mode === 'split'"
		v-bind="attrs"
		class="reaction-actions"
		:class="{
			'reaction-actions--compact': compact,
			'reaction-actions--counts': showCounts && !compact,
		}"
	>
		<UTooltip :text="summary?.reaction === 'like' ? 'Remove Like' : `Like ${title}`">
			<UButton
				:icon="icons.like"
				:label="showCounts && !compact ? countLabel('like') : undefined"
				:aria-label="
					summary?.reaction === 'like' ? `Remove Like from ${title}` : `Like ${title}`
				"
				:aria-pressed="summary?.reaction === 'like'"
				:color="summary?.reaction === 'like' ? 'primary' : 'neutral'"
				:variant="summary?.reaction === 'like' ? 'soft' : 'ghost'"
				:loading="pending && summary?.reaction === 'like'"
				:disabled="pending || session.status !== 'authenticated'"
				class="reaction-button"
				@click="toggle('like')"
			/>
		</UTooltip>
		<UTooltip :text="summary?.reaction === 'dislike' ? 'Remove Dislike' : `Dislike ${title}`">
			<UButton
				:icon="icons.dislike"
				:label="showCounts && !compact ? countLabel('dislike') : undefined"
				:aria-label="
					summary?.reaction === 'dislike'
						? `Remove Dislike from ${title}`
						: `Dislike ${title}`
				"
				:aria-pressed="summary?.reaction === 'dislike'"
				:color="summary?.reaction === 'dislike' ? 'primary' : 'neutral'"
				:variant="summary?.reaction === 'dislike' ? 'soft' : 'ghost'"
				:loading="pending && summary?.reaction === 'dislike'"
				:disabled="pending || session.status !== 'authenticated'"
				class="reaction-button"
				@click="toggle('dislike')"
			/>
		</UTooltip>
		<UTooltip v-if="showDetails" :text="total ? `See ${total} reactions` : 'See reactions'">
			<UButton
				:icon="icons.users"
				:aria-label="`See reactions for ${title}`"
				color="neutral"
				variant="ghost"
				class="reaction-button"
				@click="detailsOpen = true"
			/>
		</UTooltip>
	</div>
	<UDropdownMenu v-else v-bind="attrs" :items="menuItems" :content="{ align: 'end' }">
		<UTooltip :text="`React to ${title}`">
			<UButton
				:icon="menuIcon"
				:aria-label="`React to ${title}`"
				:aria-pressed="Boolean(summary?.reaction)"
				:color="summary?.reaction ? 'primary' : 'neutral'"
				:variant="summary?.reaction ? 'soft' : 'ghost'"
				:loading="pending"
				:disabled="pending || session.status !== 'authenticated'"
				class="reaction-button"
			/>
		</UTooltip>
	</UDropdownMenu>
	<LibraryReactionDetail
		:open="detailsOpen"
		:track-id="trackId"
		:title="title"
		:summary="summary"
		@update:open="detailsOpen = $event"
	/>
</template>

<style scoped>
.reaction-actions {
	display: inline-flex;
	align-items: center;
	gap: 0.125rem;
}
.reaction-button {
	min-width: 2.5rem;
	min-height: 2.5rem;
	justify-content: center;
}
.reaction-actions--compact .reaction-button {
	width: 2.5rem;
	min-width: 2.5rem;
}
.reaction-actions--counts {
	gap: 0.25rem;
}
.reaction-actions--counts .reaction-button {
	min-width: auto;
}
@container workspace (max-width: 600px) {
	.reaction-actions:not(.reaction-actions--compact):not(.reaction-actions--counts)
		.reaction-button {
		width: 2.5rem;
		min-width: 2.5rem;
		padding-inline: 0;
	}
}
</style>
