<script setup lang="ts">
useSeoMeta({ title: "Listener profile | NaHörMaar" });
const route = useRoute();
const core = useNuxtApp().$backendCore;
const session = core.stores.useSessionStore();
const profile = core.workflows.profile();
const details = computed(() => profile.profile.data.value);
const userId = computed(() => String(route.params.userId));
const { icons } = useTheme();

async function load(id: string) {
	if (id === session.account?.user_id) {
		await navigateTo("/profile", { replace: true });
		return;
	}
	await profile.loadUser(id);
}

onMounted(() => load(userId.value));
watch(userId, load);
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
					<div
						v-if="profile.profile.loading.value && !details"
						aria-label="Loading listener profile"
					>
						<div class="flex flex-wrap items-center gap-5 border-b border-default pb-7">
							<USkeleton class="size-20 rounded-2xl" />
							<div class="min-w-64 space-y-3">
								<USkeleton class="h-7 w-52" />
								<USkeleton class="h-4 w-36" />
							</div>
						</div>
						<div
							class="mt-7 grid overflow-hidden rounded-2xl border border-default sm:grid-cols-2 xl:grid-cols-4"
						>
							<div v-for="index in 4" :key="index" class="space-y-3 p-5">
								<USkeleton class="h-4 w-28" />
								<USkeleton class="h-8 w-20" />
							</div>
						</div>
						<div class="mt-10 grid gap-8 border-t border-default pt-8 xl:grid-cols-2">
							<USkeleton class="h-72 rounded-2xl" />
							<USkeleton class="h-72 rounded-2xl" />
						</div>
					</div>

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

					<ProfileOverview v-else :value="details" />
				</div>
			</template>
		</UDashboardPanel>
	</div>
</template>
