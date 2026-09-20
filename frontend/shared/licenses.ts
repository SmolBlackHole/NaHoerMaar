export interface SoftwareLicense {
	name: string;
	version: string;
	license: string;
	category: "JavaScript" | "Python" | "Audio" | "Fonts & artwork" | "Project";
	url: string | null;
	textUrl: string | null;
}

export interface LicenseInventory {
	packages: SoftwareLicense[];
}
