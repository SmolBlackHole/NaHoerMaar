<script setup lang="ts">
const core = useNuxtApp().$backendCore;
const session = core.stores.useSessionStore();
const consent = useConsentStore();
const route = useRoute();
const { icons } = useTheme();
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
			class="app-boot"
			role="status"
			aria-live="polite"
			aria-label="Loading NaHörMaar"
		>
			<div class="app-boot__content">
				<div class="app-boot__brand">
					<UIcon :name="icons.headphones" class="size-7 text-primary" />
					<span>NaHörMaar</span>
				</div>
				<div class="app-boot__progress" aria-hidden="true">
					<span />
				</div>
			</div>
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
.app-boot {
	display: grid;
	min-height: 100dvh;
	place-items: center;
	background: var(--ui-bg);
}
.app-boot__content {
	display: grid;
	justify-items: center;
	padding: 2rem;
}
.app-boot__brand {
	display: flex;
	align-items: center;
	gap: 0.75rem;
	font-size: 1.125rem;
	font-weight: 600;
	letter-spacing: -0.025em;
	color: var(--ui-text-highlighted);
}
.app-boot__progress {
	width: 7.5rem;
	height: 0.125rem;
	margin-top: 1.25rem;
	overflow: hidden;
	border-radius: 9999px;
	background: var(--ui-bg-accented);
}
.app-boot__progress span {
	display: block;
	width: 45%;
	height: 100%;
	border-radius: 9999px;
	background: var(--ui-primary);
	animation: app-boot-progress 1.1s ease-in-out infinite;
}
@keyframes app-boot-progress {
	0% {
		transform: translateX(-120%);
	}
	100% {
		transform: translateX(270%);
	}
}
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
	.app-boot__progress span {
		animation: none;
		transform: translateX(60%);
	}
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
