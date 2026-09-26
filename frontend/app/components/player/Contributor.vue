<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
import type { Contributor } from "~/core/models/player";

defineOptions({ inheritAttrs: false });
const props = defineProps<{
	contributor: Contributor | null;
	compact?: boolean;
	origin?: string;
	context?: "request" | "radio-status";
}>();
const { icons } = useTheme();
const label = computed(() =>
	props.contributor
		? props.context === "radio-status"
			? `Radio started by ${props.contributor.display_name}`
			: props.origin === "radio"
				? `Requested by ${props.contributor.display_name} via radio`
				: `Requested by ${props.contributor.display_name}`
		: "No requester recorded",
);
</script>

<template>
	<UTooltip :text="label">
		<NuxtLink
			v-if="contributor"
			v-bind="$attrs"
			:to="`/profile/${contributor.user_id}`"
			class="track-contributor flex min-w-0 items-center gap-2 text-xs text-muted hover:text-highlighted"
		>
			<UAvatar
				v-if="contributor"
				:src="contributor.avatar_url"
				:alt="contributor.display_name"
				class="size-6 shrink-0 bg-elevated"
			/>
			<span class="truncate">
				<span v-if="origin === 'radio' && context !== 'radio-status'">Radio · </span>
				<span :class="compact ? 'sr-only' : ''">{{
					context === "radio-status"
						? "started by "
						: origin === "radio"
							? ""
							: "Requested by "
				}}</span>
				{{ contributor.display_name }}
			</span>
		</NuxtLink>
		<span
			v-else
			v-bind="$attrs"
			class="track-contributor flex min-w-0 items-center gap-2 text-xs text-muted"
		>
			<UIcon :name="icons.user" class="size-4 shrink-0 opacity-60" />
			<span class="sr-only">No requester recorded</span>
		</span>
	</UTooltip>
</template>
