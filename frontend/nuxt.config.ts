export default defineNuxtConfig({
	modules: ["@nuxt/ui", "@vueuse/nuxt", "@pinia/nuxt"],
	css: ["~/assets/css/main.css"],
	colorMode: { preference: "dark", fallback: "dark" },
	runtimeConfig: {
		backendUrl: "http://127.0.0.1:8000",
		publicOrigin: process.env.PUBLIC_ORIGIN || "http://localhost:3012",
	},
	vite: {
		build: {
			rolldownOptions: { makeAbsoluteExternalsRelative: false },
		},
	},
	app: {
		head: { title: "NaHörMaar", htmlAttrs: { lang: "en" } },
	},
	fonts: {
		providers: { fontsource: false },
		families: [
			{ name: "Public Sans", provider: "google", global: true },
			{ name: "DM Sans", provider: "google", global: true },
			{ name: "Geist", provider: "google", global: true },
			{ name: "Inter", provider: "google", global: true },
			{ name: "Poppins", provider: "google", global: true },
			{ name: "Outfit", provider: "google", global: true },
			{ name: "Raleway", provider: "google", global: true },
		],
	},
	compatibilityDate: "2026-09-08",
});
