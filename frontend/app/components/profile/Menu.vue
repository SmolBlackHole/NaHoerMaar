<script setup lang="ts">
import { useProfileStore } from "~/stores/profile";
defineProps<{ collapsed?: boolean }>();
const profile = useProfileStore();
</script>

<template>
	<UButton
		v-if="profile.profile"
		to="/profile"
		color="neutral"
		variant="ghost"
		class="min-w-0 flex-1 gap-2.5 py-2"
		:class="collapsed && 'justify-center px-0'"
		aria-label="Edit your profile"
		title="Edit your profile"
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
	</UButton>
</template>
