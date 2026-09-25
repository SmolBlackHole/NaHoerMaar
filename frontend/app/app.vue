<script setup lang="ts">
const core = useNuxtApp().$backendCore;
const session = core.stores.useSessionStore();
const consent = useConsentStore();
const route = useRoute();
const publicPage = computed(() => ["/licenses", "/licenses/"].includes(route.path));
onMounted(() => {
	consent.ready = true;
	try {
		localStorage.removeItem("nuxt-color-mode");
	} catch {
		/* Storage can be unavailable. */
	}
	if (!publicPage.value) void session.restore();
});
watch(publicPage, (isPublic) => {
	if (!isPublic && import.meta.client) void session.restore();
});
</script>

<template>
	<UApp :toaster="{ position: 'top-right', max: 3, ui: { viewport: 'top-16' } }">
		<NuxtLoadingIndicator />
		<div
			v-if="!publicPage && session.status === 'checking'"
			class="grid min-h-dvh place-items-center text-muted"
			role="status"
		>
			Loading NaHörMaar…
		</div>
		<AuthWelcome v-else-if="!publicPage && session.status !== 'authenticated'" />
		<ProfileWelcome v-else-if="!publicPage && !session.account?.profile_complete" />
		<div
			v-if="
				publicPage ||
				(session.status === 'authenticated' && session.account?.profile_complete)
			"
			class="contents"
		>
			<NuxtLayout>
				<NuxtPage :page-key="route.path" />
			</NuxtLayout>
		</div>
		<PrivacyConsent />
	</UApp>
</template>

<style>
.page-enter-active {
	transition: opacity 140ms ease-out;
}
.page-leave-active {
	transition: opacity 90ms ease-in;
}
.page-enter-from,
.page-leave-to {
	opacity: 0;
}
@media (prefers-reduced-motion: reduce) {
	.animate-spin {
		animation: none !important;
	}
	.page-enter-active,
	.page-leave-active {
		transition: none;
	}
}
@layer base {
	:where(button, a, [role="tab"], [role="menuitem"]) {
		-webkit-tap-highlight-color: transparent;
		transition-property: color, background-color, border-color;
		transition-duration: 130ms;
		transition-timing-function: ease-out;
	}
	:where(
			button,
			[role="button"],
			[role="tab"],
			[role="menuitem"],
			[role="menuitemcheckbox"],
			[role="menuitemradio"],
			[role="option"],
			[role="combobox"]
		):not(:disabled):not([aria-disabled="true"]):not([data-disabled]) {
		cursor: pointer;
	}
}
</style>
