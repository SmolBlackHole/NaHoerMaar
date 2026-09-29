<script setup lang="ts">
import type { ProfileUpdate, StatisticsPeriod } from "~/core/models/account";

useSeoMeta({ title: "Your profile | NaHörMaar" });
const core = useNuxtApp().$backendCore;
const session = core.stores.useSessionStore();
const account = core.stores.useAccountStore();
const profile = core.workflows.profile();
const period = ref<StatisticsPeriod>("30d");
const section = useProfileSection();
const details = computed(() => profile.profile.data.value);
const { icons } = useTheme();
const toast = useToast();
const consent = useConsentStore();

const discordAvatar = computed(() => details.value?.discord.avatar_url ?? undefined);

async function load() {
	await profile.loadMine(period.value);
}
async function save(value: ProfileUpdate) {
	const updated = await account.update({ profile: value });
	if (!updated) return;
	profile.mergeAccount(updated);
	toast.add({
		title: "Profile saved",
		description: "Your updated name is visible to the group.",
		icon: icons.value.success,
		color: "success",
	});
}

watch(period, load);
onMounted(load);
onScopeDispose(profile.dispose);
</script>

<template>
	<div class="flex min-h-0 min-w-0 flex-1 flex-col">
		<UDashboardPanel id="profile" class="min-w-0" :ui="{ body: 'pt-4 sm:pt-4' }">
			<template #header>
				<UDashboardNavbar title="Your profile">
					<template #leading><UDashboardSidebarCollapse /></template>
				</UDashboardNavbar>
			</template>
			<template #body>
				<div class="w-full pb-4 sm:pb-6">
					<ProfileSkeleton
						v-if="profile.profile.loading.value && !details"
						aria-label="Loading your profile"
						:view="section"
						editable
					/>

					<div v-else-if="!details" class="grid min-h-80 place-items-center">
						<div class="max-w-sm text-center">
							<UIcon :name="icons.user" class="mx-auto size-10 text-muted" />
							<h1 class="mt-4 text-lg font-semibold text-highlighted">
								Your profile is unavailable
							</h1>
							<p class="mt-2 text-sm leading-relaxed text-muted" role="alert">
								{{
									profile.profile.error.value ??
									"The profile could not be loaded."
								}}
							</p>
							<UButton
								class="mt-5"
								label="Try again"
								:icon="icons.reload"
								color="neutral"
								variant="outline"
								@click="load"
							/>
						</div>
					</div>

					<ProfileOverview
						v-else
						v-model:period="period"
						v-model:view="section"
						:value="details"
					>
						<template #collection>
							<ProfileLibraryCollection
								:user-id="details.id"
								:display-name="
									details.profile.display_name ??
									details.discord.display_name ??
									details.discord.username ??
									'Listener'
								"
								:view="section === 'overview' ? 'liked' : section"
							/>
						</template>
						<template #details>
							<div
								class="mt-8 grid gap-8 lg:grid-cols-[minmax(0,1.35fr)_minmax(18rem,0.65fr)] lg:items-start"
							>
								<section aria-labelledby="identity-heading">
									<h2
										id="identity-heading"
										class="text-lg font-semibold text-highlighted"
									>
										How friends see you
									</h2>
									<p class="mt-1 text-sm text-muted">
										Your name and Discord profile picture appear beside requests
										and radio sessions.
									</p>
									<div
										class="mt-5 rounded-2xl border border-default bg-elevated/30 p-6 sm:p-8"
									>
										<ProfileForm
											:profile="details.profile"
											:complete="true"
											:busy="account.saving"
											:error="account.error"
											@save="save"
										/>
									</div>
								</section>

								<aside class="space-y-7">
									<section aria-labelledby="discord-account-heading">
										<h2
											id="discord-account-heading"
											class="text-lg font-semibold text-highlighted"
										>
											Discord account
										</h2>
										<div class="mt-4 flex items-center gap-3">
											<UAvatar
												:src="discordAvatar"
												:alt="details.discord.username ?? 'Discord account'"
												size="lg"
											/>
											<div class="min-w-0">
												<p
													class="truncate text-sm font-medium text-highlighted"
												>
													{{ details.discord.username ?? "Discord user" }}
												</p>
												<p class="truncate text-xs text-muted">
													{{ details.discord.id }}
												</p>
											</div>
										</div>
									</section>

									<section
										class="border-t border-default pt-6"
										aria-labelledby="account-heading"
									>
										<h2
											id="account-heading"
											class="text-lg font-semibold text-highlighted"
										>
											Account
										</h2>
										<p class="mt-1 text-sm leading-relaxed text-muted">
											Manage privacy choices or end this browser session.
										</p>
										<div class="mt-4 grid gap-2">
											<UButton
												label="Cookie settings"
												color="neutral"
												variant="outline"
												block
												@click="consent.open = true"
											/>
											<UButton
												label="Sign out"
												:icon="icons.logOut"
												color="neutral"
												variant="ghost"
												block
												:loading="session.busy"
												@click="session.logout"
											/>
										</div>
										<p class="mt-3 text-xs text-muted">
											Music keeps playing in Discord.
										</p>
										<p
											v-if="session.error"
											class="mt-3 text-sm text-error"
											role="alert"
										>
											{{ session.error }}
										</p>
									</section>
								</aside>
							</div>
						</template>
					</ProfileOverview>
				</div>
			</template>
		</UDashboardPanel>
	</div>
</template>
