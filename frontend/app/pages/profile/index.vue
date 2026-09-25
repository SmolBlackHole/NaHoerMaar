<script setup lang="ts">
import type { ProfileUpdate } from "~/core/models/account";

useSeoMeta({ title: "Your profile | NaHörMaar" });
const core = useNuxtApp().$backendCore;
const session = core.stores.useSessionStore();
const profile = core.workflows.profile();
const details = computed(() => profile.profile.data.value);
const { icons } = useTheme();
const toast = useToast();
const consent = useConsentStore();

const discordAvatar = computed(() => {
	const discord = details.value?.discord;
	if (!discord?.avatar_hash) return undefined;
	const extension = discord.avatar_hash.startsWith("a_") ? "gif" : "png";
	return `https://cdn.discordapp.com/avatars/${discord.id}/${discord.avatar_hash}.${extension}?size=128`;
});

async function load() {
	await profile.loadMine();
}
async function save(value: ProfileUpdate) {
	if (!(await profile.updateProfile(value))) return;
	toast.add({
		title: "Profile saved",
		description: "Your updated name and Pixabot are visible to the group.",
		icon: "i-lucide-circle-check",
		color: "success",
	});
}

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
				<div class="mx-auto w-full max-w-6xl pb-4 sm:pb-6">
					<div
						v-if="profile.profile.loading.value && !details"
						aria-label="Loading your profile"
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
						<div
							class="mt-7 grid gap-7 lg:grid-cols-[minmax(0,1.35fr)_minmax(18rem,0.65fr)]"
						>
							<USkeleton class="h-96 rounded-2xl" />
							<USkeleton class="h-72 rounded-2xl" />
						</div>
					</div>

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

					<ProfileOverview v-else :value="details">
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
										Your name and Pixabot appear beside requests and radio
										sessions.
									</p>
									<div
										class="mt-5 rounded-2xl border border-default bg-elevated/30 p-6 sm:p-8"
									>
										<ProfileForm
											:profile="details.profile"
											:complete="true"
											:busy="profile.saving.value"
											:error="profile.mutationError.value"
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
