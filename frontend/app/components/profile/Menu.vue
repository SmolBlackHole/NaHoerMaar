<script setup lang="ts">
defineProps<{ collapsed?: boolean }>();
const account = useNuxtApp().$backendCore.stores.useAccountStore();
const details = computed(() => account.account);
const discordAvatar = computed(() => details.value?.discord.avatar_url ?? undefined);
</script>

<template>
	<UTooltip v-if="details" text="Edit your profile">
		<UButton
			to="/profile"
			color="neutral"
			variant="ghost"
			class="min-w-0 gap-2.5"
			:class="collapsed ? 'size-10 flex-none justify-center rounded-full p-0' : 'flex-1 py-2'"
			aria-label="Edit your profile"
		>
			<UAvatar
				:src="discordAvatar"
				:alt="details.discord.username ?? 'Profile'"
				size="sm"
				class="shrink-0"
				:class="collapsed && 'size-10'"
			/>
			<span v-if="!collapsed" class="min-w-0 flex-1 truncate text-left">{{
				details.profile.display_name ?? details.discord.username ?? "Profile"
			}}</span>
		</UButton>
	</UTooltip>
</template>
