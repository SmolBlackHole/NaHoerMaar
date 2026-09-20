export interface Appearance {
	mode: "light" | "dark" | "system" | "time";
	artworkColors: boolean;
	primaryColor: string;
	neutralColor: string;
	fontFamily: string;
	iconSet: "lucide" | "ph" | "heroicons" | "tabler";
	textSize: "sm" | "md" | "lg";
}

export const defaultAppearance: Readonly<Appearance> = {
	mode: "dark",
	artworkColors: true,
	primaryColor: "teal",
	neutralColor: "zinc",
	fontFamily: "Geist",
	iconSet: "lucide",
	textSize: "md",
};
