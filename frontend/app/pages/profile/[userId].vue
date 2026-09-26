<script setup lang="ts">
import type { StatisticsPeriod } from "~/core/models/account";

useSeoMeta({ title: "Listener profile | NaHörMaar" });
const route = useRoute();
const core = useNuxtApp().$backendCore;
const session = core.stores.useSessionStore();
const profile = core.workflows.profile();
const details = computed(() => profile.profile.data.value);
const userId = computed(() => String(route.params.userId));
const period = ref<StatisticsPeriod>("30d");
const { icons } = useTheme();

async function load(id: string) {
	if (id === session.account?.user_id) {
		await navigateTo("/profile", { replace: true });
		return;
	}
	await profile.loadUser(id, period.value);
}

onMounted(() => load(userId.value));
watch(userId, load);
watch(period, () => load(userId.value));
onScopeDispose(profile.dispose);
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
				<div class="w-full pb-4 sm:pb-6">
					<ProfileSkeleton
						v-if="profile.profile.loading.value && !details"
						aria-label="Loading listener profile"
					/>

					<div v-else-if="!details" class="grid min-h-80 place-items-center">
						<div class="max-w-sm text-center">
							<UIcon :name="icons.user" class="mx-auto size-10 text-muted" />
							<h1 class="mt-4 text-lg font-semibold text-highlighted">
								Listener profile unavailable
							</h1>
							<p class="mt-2 text-sm leading-relaxed text-muted" role="alert">
								{{
									profile.profile.error.value ??
									"This profile could not be loaded."
								}}
							</p>
							<UButton
								class="mt-5"
								label="Try again"
								:icon="icons.reload"
								color="neutral"
								variant="outline"
								@click="load(userId)"
							/>
						</div>
					</div>

					<ProfileOverview v-else v-model:period="period" :value="details" />
				</div>
			</template>
		</UDashboardPanel>
	</div>
</template>
