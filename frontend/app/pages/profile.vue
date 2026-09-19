<script setup lang="ts">
import { useProfileStore } from "~/stores/profile";
useSeoMeta({ title: "Your profile | NaHörMaar" });
const profile = useProfileStore();
const { icons } = useTheme();
const saved = ref(false);
</script>

<template>
	<UDashboardPanel id="profile">
		<template #header
			><UDashboardNavbar title="Your profile"
				><template #leading><UDashboardSidebarCollapse /></template></UDashboardNavbar
		></template>
		<template #body>
			<section class="mx-auto w-full max-w-md py-4 sm:py-10">
				<h1 class="text-highlighted mb-3 text-2xl font-semibold">Name and avatar</h1>
				<p class="text-muted mb-8 text-sm">This profile belongs to this browser.</p>
				<ProfileForm v-if="profile.profile" @saved="saved = true" />
				<p v-if="saved" role="status" class="text-success mt-4 text-sm">Profile saved.</p>
				<p v-if="profile.storageUnavailable" role="status" class="text-muted mt-4 text-sm">
					Browser storage is unavailable. Your profile lasts until this page closes.
				</p>
				<div class="mt-10 border-t border-default pt-6">
					<UButton
						label="Leave this profile"
						:icon="icons.logOut"
						color="neutral"
						variant="outline"
						@click="profile.signOut"
					/>
					<p class="text-muted mt-3 text-xs">Music keeps playing in Discord.</p>
				</div>
			</section>
		</template>
	</UDashboardPanel>
</template>
