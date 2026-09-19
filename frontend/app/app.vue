<script setup lang="ts">
import { useThemeEffects } from "~/composables/useThemeEffects";
import { usePlayerStore } from "~/stores/player";
import { useProfileStore } from "~/stores/profile";

useThemeEffects();
const player = usePlayerStore();
const profile = useProfileStore();
onMounted(profile.restore);
watch(
	() => !!profile.profile,
	(signedIn) => {
		if (signedIn) player.connect();
		else player.dispose();
	},
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
		<ProfileWelcome v-else-if="!profile.profile" />
		<div v-show="profile.ready && profile.profile" class="contents">
			<NuxtLayout>
				<NuxtPage />
			</NuxtLayout>
		</div>
	</UApp>
</template>
