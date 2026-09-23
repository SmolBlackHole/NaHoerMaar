export default defineNuxtConfig({
	modules: ["@nuxt/ui", "@vueuse/nuxt", "@pinia/nuxt"],
	css: ["~/assets/css/main.css"],
	ui: { colorMode: false },
	runtimeConfig: {
		backendUrl: "http://127.0.0.1:8000",
		publicOrigin: "http://localhost:3000",
	},
	vite: {
		build: {
			rolldownOptions: { makeAbsoluteExternalsRelative: false },
		},
	},
	app: {
		pageTransition: { name: "page", mode: "out-in" },
		head: { title: "NaHörMaar", htmlAttrs: { lang: "en" } },
	},
	experimental: {
		appManifest: true,
		checkOutdatedBuildInterval: 15_000,
		emitRouteChunkError: "automatic-immediate",
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
