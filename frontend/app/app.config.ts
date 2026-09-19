export default defineAppConfig({
	ui: {
		dashboardPanel: { slots: { root: "min-h-0", body: "p-5 sm:p-8" } },
		dashboardNavbar: {
			slots: { root: "border-0 h-16", title: "font-medium text-sm text-muted" },
		},
		navigationMenu: { slots: { link: "py-3", linkLeadingIcon: "size-5" } },
		colors: {
			primary: "teal",
			neutral: "zinc",
		},
	},
});
