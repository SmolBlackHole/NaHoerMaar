<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
import type { ReactionSummary, ReactionValue } from "~/core/models/library";

const props = defineProps<{
	open: boolean;
	trackId: string;
	title: string;
	summary: ReactionSummary | null;
}>();
const emit = defineEmits<{ "update:open": [value: boolean] }>();
const PAGE_SIZE = 8;
const core = useNuxtApp().$backendCore;
const library = core.workflows.library();
const { icons } = useTheme();
const reaction = ref<ReactionValue>("like");
const currentPage = ref(1);
const result = computed(() => library.participants.data.value);
const initialLoading = computed(() => library.participants.loading.value && !result.value);

const tabs = computed(() => [
	{
		label: `Liked ${props.summary?.likes ?? 0}`,
		value: "like" as const,
		icon: icons.value.like,
	},
	{
		label: `Disliked ${props.summary?.dislikes ?? 0}`,
		value: "dislike" as const,
		icon: icons.value.dislike,
	},
]);

async function load(page: number, newSnapshot = false) {
	const loaded = await library.loadParticipants(props.trackId, {
		page,
		pageSize: PAGE_SIZE,
		reaction: reaction.value,
		newSnapshot,
	});
	if (loaded) currentPage.value = loaded.page;
}

watch(
	() => [props.open, props.trackId] as const,
	([open]) => {
		if (!open) {
			library.clearParticipants();
			return;
		}
		const nextReaction = (props.summary?.likes ?? 0) > 0 ? "like" : "dislike";
		const reactionChanged = reaction.value !== nextReaction;
		reaction.value = nextReaction;
		currentPage.value = 1;
		if (!reactionChanged) void load(1, true);
	},
);
watch(reaction, () => {
	if (props.open) void load(1, true);
});
onScopeDispose(library.dispose);

function reactedAt(value: string) {
	return new Intl.DateTimeFormat(undefined, {
		dateStyle: "medium",
		timeStyle: "short",
	}).format(new Date(value));
}
</script>

<template>
	<UModal
		:open="open"
		:title="`Reactions to ${title}`"
		description="See who liked or disliked this track."
		:ui="{ content: 'sm:max-w-xl', body: 'p-0 sm:p-0' }"
		@update:open="emit('update:open', $event)"
	>
		<template #body>
			<div class="px-4 pt-1 sm:px-6">
				<div
					class="inline-flex rounded-lg bg-elevated p-1"
					role="group"
					aria-label="Reaction filter"
				>
					<UButton
						v-for="tab in tabs"
						:key="tab.value"
						:label="tab.label"
						:icon="tab.icon"
						:color="reaction === tab.value ? 'primary' : 'neutral'"
						:variant="reaction === tab.value ? 'soft' : 'ghost'"
						:aria-pressed="reaction === tab.value"
						@click="reaction = tab.value"
					/>
				</div>
			</div>

			<div class="mt-4 min-h-72 border-t border-default">
				<ul
					v-if="initialLoading"
					class="divide-y divide-default"
					aria-label="Loading listeners"
				>
					<li
						v-for="index in 4"
						:key="index"
						class="flex items-center gap-3 px-4 py-4 sm:px-6"
						aria-hidden="true"
					>
						<USkeleton class="size-10 shrink-0 rounded-full" />
						<div class="min-w-0 flex-1 space-y-2">
							<USkeleton class="h-4 w-36" />
							<USkeleton class="h-3 w-28" />
						</div>
					</li>
				</ul>
				<div
					v-else-if="library.participants.error.value && !result"
					class="grid min-h-72 place-items-center px-6 text-center"
					role="alert"
				>
					<div>
						<UIcon :name="icons.warning" class="mx-auto size-8 text-warning" />
						<p class="mt-3 font-medium text-highlighted">
							Reactions could not be loaded
						</p>
						<UButton
							class="mt-4"
							label="Try again"
							:icon="icons.reload"
							color="neutral"
							variant="outline"
							@click="load(1, true)"
						/>
					</div>
				</div>
				<div
					v-else-if="result && !result.items.length"
					class="grid min-h-72 place-items-center px-6 text-center"
				>
					<div>
						<UIcon
							:name="reaction === 'like' ? icons.like : icons.dislike"
							class="mx-auto size-8 text-muted"
						/>
						<p class="mt-3 font-medium text-highlighted">
							No {{ reaction === "like" ? "Likes" : "Dislikes" }} yet
						</p>
						<p class="mt-1 text-sm text-muted">This side is still quiet.</p>
					</div>
				</div>
				<ul
					v-else-if="result"
					class="divide-y divide-default"
					:aria-label="
						reaction === 'like'
							? 'Listeners who liked this track'
							: 'Listeners who disliked this track'
					"
				>
					<li
						v-for="participant in result.items"
						:key="participant.user_id"
						class="flex items-center gap-3 px-4 py-3 sm:px-6"
					>
						<NuxtLink
							:to="`/profile/${participant.user_id}`"
							class="flex min-w-0 flex-1 items-center gap-3 rounded-lg focus-visible:outline-none"
						>
							<UAvatar
								:src="participant.avatar_url ?? undefined"
								:alt="participant.display_name"
								size="md"
							/>
							<span class="min-w-0">
								<span class="block truncate text-sm font-medium text-highlighted">{{
									participant.display_name
								}}</span>
								<time
									:datetime="participant.reacted_at"
									class="mt-0.5 block text-xs text-muted"
									>{{ reactedAt(participant.reacted_at) }}</time
								>
							</span>
						</NuxtLink>
						<UIcon
							:name="reaction === 'like' ? icons.like : icons.dislike"
							class="size-4 shrink-0 text-primary"
						/>
					</li>
				</ul>
			</div>
			<div
				v-if="result && result.page_count > 1"
				class="border-t border-default px-4 sm:px-6"
			>
				<SharedPagePagination
					v-model:page="currentPage"
					:page-size="result.page_size"
					:total="result.total"
					:disabled="library.participants.loading.value"
					@select="load"
				/>
			</div>
		</template>
	</UModal>
</template>
