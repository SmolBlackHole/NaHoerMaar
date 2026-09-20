<script setup lang="ts">
import type { ListenerProfile } from "#shared/profile";

defineProps<{
	contributor: ListenerProfile | null;
	compact?: boolean;
	origin?: "manual" | "radio";
}>();
const { icons } = useTheme();
</script>

<template>
	<span
		class="track-contributor flex min-w-0 items-center gap-2 text-xs text-muted"
		:title="
			contributor
				? `${origin === 'radio' ? 'Radio started' : 'Added'} by ${contributor.name}`
				: 'No contributor recorded'
		"
	>
		<UAvatar
			v-if="contributor"
			:src="`/avatars/${contributor.avatar}.png`"
			:alt="contributor.name"
			aria-hidden="true"
			class="size-6 shrink-0 bg-elevated"
		/>
		<UIcon v-else :name="icons.user" class="size-4 shrink-0 opacity-60" />
		<span v-if="contributor" class="truncate"
			><span v-if="origin === 'radio'">Radio · </span
			><span :class="compact ? 'sr-only' : ''">{{
				origin === "radio" ? "started by " : "Added by "
			}}</span
			>{{ contributor?.name ?? "Unknown" }}</span
		>
		<span v-else class="sr-only">No contributor recorded</span>
	</span>
</template>
