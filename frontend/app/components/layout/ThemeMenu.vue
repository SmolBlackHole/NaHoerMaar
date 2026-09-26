<script setup lang="ts">
import type { DropdownMenuItem } from "@nuxt/ui";
import { useTheme } from "~/composables/useTheme";
import { useThemeMenu } from "~/composables/useThemeMenu";

const { settings, icons } = useTheme();
const appConfig = useAppConfig();
const { themeItems } = useThemeMenu({
	get artworkColors() {
		return settings.artwork_colors;
	},
	get primary() {
		return appConfig.ui.colors.primary;
	},
	get neutral() {
		return appConfig.ui.colors.neutral;
	},
	get mode() {
		return settings.mode;
	},
	get fontFamily() {
		return settings.font_family;
	},
	get iconSet() {
		return settings.icon_set;
	},
	get textSize() {
		return settings.text_size;
	},
	get icons() {
		return icons.value;
	},
	setPrimary(value) {
		settings.artwork_colors = false;
		settings.primary_color = value;
	},
	setNeutral(value) {
		settings.artwork_colors = false;
		settings.neutral_color = value;
	},
	setArtworkColors(value) {
		settings.artwork_colors = value;
	},
	setMode(value) {
		settings.mode = value;
	},
	setFontFamily(value) {
		settings.font_family = value;
	},
	setIconSet(value) {
		settings.icon_set = value;
	},
	setTextSize(value) {
		settings.text_size = value;
	},
});
</script>

<template>
	<UDropdownMenu
		:items="themeItems"
		:content="{ align: 'start', collisionPadding: 12 }"
		:ui="{ content: 'w-56', item: 'min-h-9' }"
	>
		<UTooltip text="Appearance">
			<UButton
				:icon="icons.paintbrush"
				aria-label="Appearance"
				color="neutral"
				variant="ghost"
				square
				class="size-10 shrink-0 justify-center text-muted data-[state=open]:bg-elevated"
			/>
		</UTooltip>
		<template #chip-leading="{ item }: { item: DropdownMenuItem }">
			<span class="inline-flex size-5 shrink-0 items-center justify-center">
				<span
					class="ring-bg size-2 rounded-full bg-(--chip-light) ring dark:bg-(--chip-dark)"
					:style="{
						'--chip-light': `var(--color-${item.chip}-500)`,
						'--chip-dark': `var(--color-${item.chip}-400)`,
					}"
				/>
			</span>
		</template>
	</UDropdownMenu>
</template>
