import type { components } from "./api.generated";

export type Appearance = Required<components["schemas"]["Appearance"]>;

export const defaultAppearance: Readonly<Appearance> = {
	mode: "dark",
	artworkColors: true,
	primaryColor: "teal",
	neutralColor: "zinc",
	fontFamily: "Geist",
	iconSet: "lucide",
	textSize: "md",
};
