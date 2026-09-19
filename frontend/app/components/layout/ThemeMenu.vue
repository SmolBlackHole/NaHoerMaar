<script setup lang="ts">
import type { DropdownMenuItem } from "@nuxt/ui";
import type { IconSet } from "~/config/icons";
import type { ColorModePreference, TextSize } from "~/stores/settings";
import { useTheme } from "~/composables/useTheme";
import { useThemeMenu } from "~/composables/useThemeMenu";

defineProps<{ collapsed?: boolean }>();

const { settings, icons } = useTheme();
const { themeItems } = useThemeMenu({
	get primary() {
		return settings.primaryColor;
	},
	get neutral() {
		return settings.neutralColor;
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
		settings.primaryColor = value;
	},
	setNeutral(value) {
		settings.neutralColor = value;
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
			:label="collapsed ? undefined : 'Appearance'"
			:icon="icons.paintbrush"
			:trailing-icon="collapsed ? undefined : icons.chevronsUpDown"
			aria-label="Appearance"
			color="neutral"
			variant="ghost"
			block
			:square="collapsed"
			class="data-[state=open]:bg-elevated"
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
