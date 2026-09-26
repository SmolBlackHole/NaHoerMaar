<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
import type { Contributor } from "~/core/models/player";

defineOptions({ inheritAttrs: false });
const props = defineProps<{
	contributor: Contributor | null;
	compact?: boolean;
	origin?: string;
}>();
const { icons } = useTheme();
const avatar = computed(() => {
	if (!props.contributor) return undefined;
	if (props.contributor.pixabot) return `/avatars/${props.contributor.pixabot}.png`;
	const hash = props.contributor.discord_avatar_hash;
	if (!hash) return undefined;
	const extension = hash.startsWith("a_") ? "gif" : "png";
	return `https://cdn.discordapp.com/avatars/${props.contributor.discord_id}/${hash}.${extension}?size=64`;
});
const label = computed(() =>
	props.contributor
		? `${props.origin === "radio" ? "Radio started" : "Requested"} by ${props.contributor.display_name}`
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
				:src="avatar"
				:alt="contributor.display_name"
				class="size-6 shrink-0 bg-elevated"
				:class="contributor.pixabot && '[&>img]:[image-rendering:pixelated]'"
			/>
			<span class="truncate">
				<span v-if="origin === 'radio'">Radio · </span>
				<span :class="compact ? 'sr-only' : ''">{{
					origin === "radio" ? "started by " : "Requested by "
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
