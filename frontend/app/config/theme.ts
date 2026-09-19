export const colorNames = {
	red: "Red",
	orange: "Orange",
	amber: "Amber",
	yellow: "Yellow",
	lime: "Lime",
	green: "Green",
	emerald: "Emerald",
	teal: "Teal",
	cyan: "Cyan",
	sky: "Sky",
	blue: "Blue",
	indigo: "Indigo",
	violet: "Violet",
	purple: "Purple",
	fuchsia: "Fuchsia",
	pink: "Pink",
	rose: "Rose",
};

export const neutralNames = {
	slate: "Slate",
	gray: "Gray",
	zinc: "Zinc",
	neutral: "Neutral",
	stone: "Stone",
};

export const primaryItems = Object.entries(colorNames).map(([value, label]) => ({ label, value }));
export const neutralItems = Object.entries(neutralNames).map(([value, label]) => ({
	label,
	value,
}));

export const modeItems = [
	{ label: "Time of day", value: "time" },
	{ label: "System", value: "system" },
	{ label: "Light", value: "light" },
	{ label: "Dark", value: "dark" },
];

export const fontItems = [
	"Public Sans",
	"DM Sans",
	"Geist",
	"Inter",
	"Poppins",
	"Outfit",
	"Raleway",
].map((f) => ({ label: f, value: f }));

export const textSizes = [
	{ label: "Small", value: "sm" },
	{ label: "Medium", value: "md" },
	{ label: "Large", value: "lg" },
] as const;
