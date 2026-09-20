<script setup lang="ts">
import { useThemeEffects } from "~/composables/useThemeEffects";
import { usePlayerStore } from "~/stores/player";
import { useProfileStore } from "~/stores/profile";

useThemeEffects();
const player = usePlayerStore();
const profile = useProfileStore();
onMounted(profile.restore);
watch(
	() =>
		profile.status === "authenticated" && profile.profileComplete ? profile.profile?.id : null,
	(signedIn) => {
		player.dispose();
		if (signedIn) player.connect();
	},
	{ flush: "sync" },
);
onBeforeUnmount(player.dispose);
</script>

<template>
	<UApp>
		<NuxtLoadingIndicator />
		<div
			v-if="!profile.ready"
			class="grid min-h-dvh place-items-center text-muted"
			role="status"
		>
			Loading NaHörMaar…
		</div>
		<AuthWelcome v-else-if="profile.status !== 'authenticated'" />
		<ProfileWelcome v-else-if="!profile.profileComplete" />
		<div
			v-show="profile.status === 'authenticated' && profile.profileComplete"
			class="contents"
		>
			<NuxtLayout>
				<NuxtPage />
			</NuxtLayout>
		</div>
	</UApp>
</template>

<style>
@layer base {
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
