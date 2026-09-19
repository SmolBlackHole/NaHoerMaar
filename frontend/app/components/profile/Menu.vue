<script setup lang="ts">
import { useProfileStore } from "~/stores/profile";
defineProps<{ collapsed?: boolean }>();
const profile = useProfileStore();
const { icons } = useTheme();
</script>

<template>
	<UButton
		v-if="profile.profile"
		to="/profile"
		color="neutral"
		variant="ghost"
		class="w-full gap-3"
		:class="collapsed && 'justify-center px-0'"
		aria-label="Edit your profile"
	>
		<img
			:src="`/avatars/${profile.profile.avatar}.png`"
			alt=""
			width="32"
			height="32"
			class="size-8 shrink-0 rounded-lg bg-elevated [image-rendering:pixelated]"
		/>
		<span v-if="!collapsed" class="min-w-0 flex-1 truncate text-left">{{
			profile.profile.name
		}}</span>
		<UIcon v-if="!collapsed" :name="icons.cog" class="size-4 text-muted" />
	</UButton>
</template>
