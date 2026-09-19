<script setup lang="ts">
import { useTheme } from "~/composables/useTheme";

const { icons } = useTheme();
const open = ref(false);
const collapsed = ref(false);
const links = computed(() => [
	{
		label: "Player",
		icon: icons.value.music,
		to: "/",
		exact: true,
		onSelect: () => {
			open.value = false;
		},
	},
	{
		label: "Overview",
		icon: icons.value.layoutDashboard,
		to: "/dashboard",
		onSelect: () => {
			open.value = false;
		},
	},
	{
		label: "Recently played",
		icon: icons.value.timer,
		to: "/history",
		onSelect: () => {
			open.value = false;
		},
	},
]);
</script>

<template>
	<UDashboardGroup unit="rem" storage-key="nahormaar-layout" :persistent="false">
		<UDashboardSidebar
			id="navigation"
			v-model:open="open"
			v-model:collapsed="collapsed"
			collapsible
			resizable
			:menu="{
				title: 'Player settings',
				description: 'Choose a Discord channel and appearance.',
			}"
			class="navigation-sidebar bg-elevated/25"
			:ui="{ footer: 'shrink-0 flex-col items-stretch border-t border-default py-3 gap-2' }"
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
				/>
				<PlayerVoiceChannel v-if="!isCollapsed" />
				<UTooltip v-else text="Expand to choose a voice channel">
					<UButton
						:icon="icons.headphones"
						aria-label="Choose voice channel"
						color="neutral"
						variant="ghost"
						@click="collapsed = false"
					/>
				</UTooltip>
			</template>
			<template #footer="{ collapsed: isCollapsed }">
				<ProfileMenu :collapsed="isCollapsed" @click="open = false" />
				<LayoutThemeMenu :collapsed="isCollapsed" />
				<LayoutAppFooter
					v-if="!isCollapsed"
					class="justify-start px-2 pt-2 leading-relaxed"
				/>
			</template>
		</UDashboardSidebar>
		<slot />
	</UDashboardGroup>
</template>

<style>
.navigation-sidebar {
	transition: width 240ms cubic-bezier(0.16, 1, 0.3, 1);
}
.navigation-sidebar[data-dragging="true"] {
	transition: none;
}
@media (prefers-reduced-motion: reduce) {
	.navigation-sidebar {
		transition: none;
	}
}
</style>
