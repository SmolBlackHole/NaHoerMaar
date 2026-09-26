<script setup lang="ts">
defineProps<{ collapsed?: boolean }>();
const profile = useNuxtApp().$backendCore.stores.useProfileStore();
const details = computed(() => profile.profile);
const discordAvatar = computed(() => {
	const discord = details.value?.discord;
	if (!discord?.avatar_hash) return undefined;
	const extension = discord.avatar_hash.startsWith("a_") ? "gif" : "png";
	return `https://cdn.discordapp.com/avatars/${discord.id}/${discord.avatar_hash}.${extension}?size=64`;
});
</script>

<template>
	<UTooltip v-if="details" text="Edit your profile">
		<UButton
			to="/profile"
			color="neutral"
			variant="ghost"
			class="min-w-0 flex-1 gap-2.5 py-2"
			:class="collapsed && 'justify-center px-0'"
			aria-label="Edit your profile"
		>
			<img
				v-if="details.profile.pixabot"
				:src="`/avatars/${details.profile.pixabot}.png`"
				alt=""
				width="32"
				height="32"
				class="size-8 shrink-0 rounded-lg [image-rendering:pixelated]"
			/>
			<UAvatar
				v-else
				:src="discordAvatar"
				:alt="details.discord.username ?? 'Profile'"
				size="sm"
				class="shrink-0"
			/>
			<span v-if="!collapsed" class="min-w-0 flex-1 truncate text-left">{{
				details.profile.display_name ?? details.discord.username ?? "Profile"
			}}</span>
		</UButton>
	</UTooltip>
</template>
