import type { DropdownMenuItem } from "@nuxt/ui";
import { colorNames, fontItems, modeItems, neutralNames, textSizes } from "~/config/theme";
import { iconItems } from "~/config/icons";

export interface ThemeContext {
	primary: string;
	neutral: string;
	mode: string;
	fontFamily?: string;
	iconSet?: string;
	textSize: string;
	icons: Record<string, string>;
	setPrimary(v: string): void;
	setNeutral(v: string): void;
	setMode(v: string): void;
	setFontFamily(v: string): void;
	setIconSet(v: string): void;
	setTextSize(v: string): void;
}

const colors = Object.keys(colorNames) as (keyof typeof colorNames)[];
const neutrals = Object.keys(neutralNames) as (keyof typeof neutralNames)[];

export function useThemeMenu(ctx: ThemeContext) {
	const themeItems = computed<DropdownMenuItem[]>(() => [
		{
			label: "Primary color",
			icon: ctx.icons.paintbrush,
			children: colors.map((c) => ({
				label: colorNames[c],
				chip: c,
				slot: "chip" as const,
				type: "checkbox" as const,
				checked: ctx.primary === c,
				onSelect(e: Event) {
					e.preventDefault();
					ctx.setPrimary(c);
				},
			})),
		},
		{
			label: "Neutral color",
			icon: ctx.icons.paintBucket,
			children: neutrals.map((c) => ({
				label: neutralNames[c],
				chip: c === "neutral" ? "slate" : c,
				slot: "chip" as const,
				type: "checkbox" as const,
				checked: ctx.neutral === c,
				onSelect(e: Event) {
					e.preventDefault();
					ctx.setNeutral(c);
				},
			})),
		},
		{
			label: "Color mode",
			icon: ctx.icons.sunMoon,
			children: modeItems.map((m) => ({
				label: m.label,
				type: "checkbox" as const,
				checked: ctx.mode === m.value,
				onSelect(e: Event) {
					e.preventDefault();
					ctx.setMode(m.value);
				},
			})),
		},
		{
			label: "Icons",
			icon: ctx.icons.sparkles,
			children: iconItems.map((iset) => ({
				label: iset.label,
				type: "checkbox" as const,
				checked: ctx.iconSet === iset.value,
				onSelect(e: Event) {
					e.preventDefault();
					ctx.setIconSet(iset.value);
				},
			})),
		},
		{
			label: "Font family",
			icon: ctx.icons.type,
			children: fontItems.map((f) => ({
				label: f.label,
				type: "checkbox" as const,
				checked: ctx.fontFamily === f.value,
				onSelect(e: Event) {
					e.preventDefault();
					ctx.setFontFamily(f.value);
				},
			})),
		},
		{
			label: "Text size",
			icon: ctx.icons.type,
			children: textSizes.map((size) => ({
				label: size.label,
				type: "checkbox" as const,
				checked: ctx.textSize === size.value,
				onSelect(event: Event) {
					event.preventDefault();
					ctx.setTextSize(size.value);
				},
			})),
		},
	]);

	return { themeItems };
}

// Re-usable chip-leading slot (used by both menus)
export { colorNames, neutralNames };
