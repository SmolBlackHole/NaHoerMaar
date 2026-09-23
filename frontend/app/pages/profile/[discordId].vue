<script setup lang="ts">
import type { components } from "#shared/api.generated";
import { useRepositories } from "~/repositories";
import { ApiFailure } from "~/repositories/transport";
import { useProfileStore } from "~/stores/profile";

type ProfileView = components["schemas"]["ProfileView"];

useSeoMeta({ title: "Listener profile | NaHörMaar" });
const route = useRoute();
const { account } = useRepositories();
const profile = useProfileStore();
const details = shallowRef<ProfileView | null>(null);
const loading = ref(true);
const error = ref("");
const discordId = computed(() => String(route.params.discordId));

function currentProfile(id: string): ProfileView | null {
	if (!profile.session || profile.session.discord_id !== id) return null;
	return {
		discord_id: profile.session.discord_id,
		profile: profile.session.profile,
		profile_complete: profile.session.profile_complete,
		role: profile.session.role,
		members: [],
	};
}

async function load(id: string) {
	loading.value = true;
	error.value = "";
	details.value = null;
	await profile.restore();
	try {
		details.value = await account.profile(id);
	} catch (failure) {
		const ownProfile = currentProfile(id);
		if (ownProfile) details.value = ownProfile;
		else if (
			failure instanceof ApiFailure &&
			"code" in failure.data &&
			failure.data.code === "profile_not_found"
		)
			error.value = "This listener has not set up a NaHörMaar profile yet.";
		else error.value = "This listener profile could not be loaded.";
	} finally {
		loading.value = false;
	}
}

onMounted(() => load(discordId.value));
watch(discordId, (id) => load(id));
</script>

<template>
	<div class="flex min-h-0 min-w-0 flex-1 flex-col">
		<UDashboardPanel id="listener-profile" class="min-w-0" :ui="{ body: 'pt-4 sm:pt-4' }">
			<template #header>
				<UDashboardNavbar title="Listener profile">
					<template #leading><UDashboardSidebarCollapse /></template>
					<template #right>
						<UButton
							to="/access"
							label="Back to access"
							icon="i-lucide-arrow-left"
							color="neutral"
							variant="outline"
						/>
					</template>
				</UDashboardNavbar>
			</template>
			<template #body>
				<div class="mx-auto w-full max-w-3xl pb-4 sm:pb-6">
					<div v-if="loading" role="status" class="text-muted py-12">
						Loading listener profile…
					</div>
					<div v-else-if="error" role="alert" class="text-error py-12">
						{{ error }}
					</div>
					<ProfileOverview v-else-if="details" :value="details" />
				</div>
			</template>
		</UDashboardPanel>
	</div>
</template>
