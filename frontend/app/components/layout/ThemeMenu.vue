<script setup lang="ts">
import type { DropdownMenuItem } from "@nuxt/ui";
import type { IconSet } from "~/config/icons";
import type { ColorModePreference, TextSize } from "~/stores/settings";
import { useTheme } from "~/composables/useTheme";
import { useThemeMenu } from "~/composables/useThemeMenu";

const { settings, icons } = useTheme();
const appConfig = useAppConfig();
const { themeItems } = useThemeMenu({
	get artworkColors() {
		return settings.artworkColors;
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
		return settings.fontFamily;
	},
	get iconSet() {
		return settings.iconSet;
	},
	get textSize() {
		return settings.textSize;
	},
	get icons() {
		return icons.value;
	},
	setPrimary(value) {
		settings.artworkColors = false;
		settings.primaryColor = value;
	},
	setNeutral(value) {
		settings.artworkColors = false;
		settings.neutralColor = value;
	},
	setArtworkColors(value) {
		settings.artworkColors = value;
	},
	setMode(value) {
		settings.mode = value as ColorModePreference;
	},
	setFontFamily(value) {
		settings.fontFamily = value;
	},
	setIconSet(value) {
		settings.iconSet = value as IconSet;
	},
	setTextSize(value) {
		settings.textSize = value as TextSize;
	},
});
</script>

<template>
	<UDropdownMenu
		:items="themeItems"
		:content="{ align: 'start', collisionPadding: 12 }"
		:ui="{ content: 'w-56', item: 'min-h-9' }"
	>
		<UButton
			:icon="icons.paintbrush"
			aria-label="Appearance"
			title="Appearance"
			color="neutral"
			variant="ghost"
			square
			class="size-10 shrink-0 justify-center text-muted data-[state=open]:bg-elevated"
		/>
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
