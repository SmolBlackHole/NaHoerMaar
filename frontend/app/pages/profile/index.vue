<script setup lang="ts">
import type { components } from "#shared/api.generated";
import { useRepositories } from "~/repositories";
import { useProfileStore } from "~/stores/profile";

type ProfileView = components["schemas"]["ProfileView"];

useSeoMeta({ title: "Your profile | NaHörMaar" });
const profile = useProfileStore();
const { account } = useRepositories();
const { icons } = useTheme();
const toast = useToast();
const consent = useConsentStore();
const details = shallowRef<ProfileView | null>(null);
const directoryUnavailable = ref(false);
const member = computed(() => details.value?.members[0] ?? null);
const guilds = computed(() => [
	...new Set(details.value?.members.map((entry) => entry.guild_name) ?? []),
]);
const role = computed(() => {
	if (profile.session?.role === "owner") return "Owner";
	if (profile.session?.role === "admin") return "Admin";
	return "Listener";
});

async function load() {
	const discordId = profile.session?.discord_id;
	if (!discordId) return;
	directoryUnavailable.value = false;
	try {
		details.value = await account.profile(discordId);
	} catch {
		directoryUnavailable.value = true;
	}
}

function savedProfile() {
	toast.add({
		title: "Profile saved",
		description: "Your updated name and Pixabot are visible to the group.",
		icon: "i-lucide-circle-check",
		color: "success",
	});
}

onMounted(async () => {
	await profile.restore();
	await load();
});
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
				<div class="mx-auto w-full max-w-5xl space-y-7 pb-4 sm:pb-6">
					<header class="flex flex-wrap items-end justify-between gap-4">
						<div class="max-w-2xl">
							<p class="text-primary text-xs font-medium uppercase tracking-wide">
								Your space
							</p>
							<h1 class="text-highlighted mt-2 text-2xl font-semibold sm:text-3xl">
								Make yourself recognizable
							</h1>
							<p class="text-muted mt-2 text-sm leading-relaxed">
								Your name and Pixabot appear beside the tracks you add and the radio
								sessions you start.
							</p>
						</div>
						<UBadge :label="role" color="primary" variant="subtle" size="lg" />
					</header>

					<div class="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem] lg:items-start">
						<section
							aria-labelledby="identity-heading"
							class="rounded-2xl border border-default bg-elevated/30 p-6 sm:p-8"
						>
							<h2
								id="identity-heading"
								class="text-highlighted text-lg font-semibold"
							>
								How friends see you
							</h2>
							<p class="text-muted mt-1 mb-7 text-sm">
								Change either value whenever you feel like it.
							</p>
							<ProfileForm v-if="profile.profile" @saved="savedProfile" />
						</section>

						<aside class="space-y-4">
							<section
								aria-labelledby="discord-account-heading"
								class="rounded-2xl border border-default p-5"
							>
								<div class="flex items-center gap-3">
									<UAvatar
										:src="member?.avatar_url ?? undefined"
										:alt="
											member?.display_name ??
											profile.profile?.name ??
											'Discord account'
										"
										size="lg"
									/>
									<div class="min-w-0">
										<h2
											id="discord-account-heading"
											class="text-highlighted truncate text-sm font-semibold"
										>
											{{ member?.display_name ?? "Discord account" }}
										</h2>
										<p v-if="member" class="text-muted truncate text-xs">
											@{{ member.name }}
										</p>
										<p v-else class="text-muted text-xs">Linked to NaHörMaar</p>
									</div>
								</div>

								<dl class="mt-5 space-y-4">
									<div>
										<dt class="text-muted text-xs">Discord ID</dt>
										<dd
											class="text-highlighted mt-1 break-all text-sm font-medium"
										>
											{{ profile.session?.discord_id }}
										</dd>
									</div>
									<div>
										<dt class="text-muted text-xs">Connected servers</dt>
										<dd class="text-highlighted mt-1 text-sm font-medium">
											{{
												guilds.length
													? guilds.join(", ")
													: "Not currently visible"
											}}
										</dd>
									</div>
								</dl>
								<p
									v-if="directoryUnavailable"
									class="text-muted mt-4 text-xs leading-relaxed"
									role="status"
								>
									Live Discord details are temporarily unavailable. Profile
									editing still works.
								</p>
							</section>

							<section
								aria-labelledby="account-heading"
								class="rounded-2xl border p-5"
							>
								<h2
									id="account-heading"
									class="text-highlighted text-sm font-semibold"
								>
									Account
								</h2>
								<p class="text-muted mt-1 mb-4 text-xs leading-relaxed">
									Privacy choices and the current browser session.
								</p>
								<div class="grid gap-2">
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
										:loading="profile.busy"
										@click="profile.signOut"
									/>
								</div>
								<p class="text-muted mt-4 text-xs">
									Music keeps playing in Discord.
								</p>
								<p
									v-if="profile.error"
									role="alert"
									class="text-error mt-3 text-sm"
								>
									{{ profile.error }}
								</p>
							</section>
						</aside>
					</div>
				</div>
			</template>
		</UDashboardPanel>
	</div>
</template>
