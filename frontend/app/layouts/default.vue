<script setup lang="ts">
import { useTheme } from "~/composables/useTheme";
import { useThemeEffects } from "~/composables/useThemeEffects";
import { usePlayerStore } from "~/stores/player";

useThemeEffects();
usePlayerNotifications();
const player = usePlayerStore();
const radio = useRadioPreviewStore();
const profile = useProfileStore();
const settings = useSettingsStore();
const toast = useToast();
watch(
	() =>
		profile.status === "authenticated" && profile.profileComplete ? profile.profile?.id : null,
	(signedIn) => {
		player.dispose();
		radio.dispose();
		if (signedIn) player.connect();
	},
	{ flush: "sync", immediate: true },
);
watch(
	() => settings.error,
	(description) => {
		if (description)
			toast.add({
				id: "appearance-save",
				title: "Appearance not saved",
				description,
				color: "warning",
				actions: [
					{
						label: "Retry",
						onClick: () => {
							void settings.retry();
						},
					},
				],
			});
		else toast.remove("appearance-save");
	},
);
onBeforeUnmount(player.dispose);

const { icons } = useTheme();
const open = ref(false);
const collapsed = ref(false);
const consent = useConsentStore();
watch(
	() => consent.open,
	(visible) => {
		if (visible) open.value = false;
	},
);
const links = computed(() => [
	{
		label: "Player",
		"aria-label": "Player",
		icon: icons.value.music,
		to: "/",
		exact: true,
		onSelect: () => {
			open.value = false;
		},
	},
	{
		label: "Overview",
		"aria-label": "Overview",
		icon: icons.value.layoutDashboard,
		to: "/dashboard",
		onSelect: () => {
			open.value = false;
		},
	},
	...(profile.session?.is_admin
		? [
				{
					label: "Logs",
					"aria-label": "Bot logs",
					icon: icons.value.file,
					to: "/logs",
					onSelect: () => {
						open.value = false;
					},
				},
			]
		: []),
]);
</script>

<template>
	<UDashboardGroup unit="rem" storage-key="nahormaar-layout" :persistent="false">
		<div class="music-shell flex min-h-0 flex-1 overflow-hidden">
			<UDashboardSidebar
				id="navigation"
				v-model:open="open"
				v-model:collapsed="collapsed"
				collapsible
				resizable
				:default-size="15"
				:collapsed-size="4"
				:min-size="13"
				:max-size="20"
				:menu="{
					title: 'Player settings',
					description: 'Choose a Discord channel and appearance.',
				}"
				class="navigation-sidebar min-h-0"
				:ui="{
					header: 'h-16 px-3',
					body: 'px-3 pt-0 gap-1',
					footer: 'shrink-0 flex-col items-stretch px-3 pt-3 pb-4 gap-1',
				}"
			>
				<template #header="{ collapsed: isCollapsed }">
					<LayoutWorkspaceMenu :collapsed="isCollapsed" @navigate="open = false" />
				</template>
				<template #default="{ collapsed: isCollapsed }">
					<UNavigationMenu
						:collapsed="isCollapsed"
						:items="links"
						orientation="vertical"
						highlight
						tooltip
						:ui="{ link: 'min-h-11 gap-3 px-2.5 py-2.5' }"
					/>
				</template>
				<template #footer="{ collapsed: isCollapsed }">
					<PlayerVoiceChannel :collapsed="isCollapsed" />
					<div class="sidebar-account" :class="{ 'is-collapsed': isCollapsed }">
						<ProfileMenu :collapsed="isCollapsed" @click="open = false" />
						<LayoutThemeMenu />
					</div>
					<LayoutAppFooter v-if="!isCollapsed" compact />
				</template>
			</UDashboardSidebar>
			<div class="workspace-column flex min-h-0 min-w-0 flex-1 flex-col">
				<main class="flex min-h-0 min-w-0 flex-1 flex-col">
					<slot />
				</main>
				<PlayerDock />
			</div>
		</div>
	</UDashboardGroup>
</template>

<style scoped>
.music-shell {
	overflow: clip;
}
.music-shell :deep(.navigation-sidebar) {
	background: var(--room-sidebar);
	overflow: hidden;
	transition: width 240ms cubic-bezier(0.16, 1, 0.3, 1);
}
.music-shell :deep(.navigation-sidebar[data-collapsed="true"]) {
	transition-duration: 180ms;
}
.music-shell :deep(.navigation-sidebar[data-dragging="true"]) {
	transition: none;
}
.sidebar-account {
	display: flex;
	align-items: center;
	gap: 0.25rem;
}
.sidebar-account.is-collapsed {
	flex-direction: column;
}
.workspace-column {
	container-type: inline-size;
	container-name: workspace;
}
@media (prefers-reduced-motion: reduce) {
	.music-shell :deep(.navigation-sidebar) {
		transition: none;
	}
}
</style>
