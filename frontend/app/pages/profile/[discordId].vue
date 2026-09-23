<script setup lang="ts">
import type { components } from "#shared/api.generated";
import { useRepositories } from "~/repositories";

type AccessState = components["schemas"]["AccessView"];
type DiscordMember = components["schemas"]["DiscordMemberView"];

useSeoMeta({ title: "Listener profile | NaHörMaar" });
const route = useRoute();
const { access } = useRepositories();
const state = shallowRef<AccessState | null>(null);
const members = shallowRef<DiscordMember[]>([]);
const loading = ref(true);
const error = ref("");
const discordId = computed(() => String(route.params.discordId));
const matchingMembers = computed(() =>
	members.value.filter((member) => member.discord_id === discordId.value),
);
const member = computed(() => matchingMembers.value[0] ?? null);
const role = computed(() => {
	if (state.value?.owner_id === discordId.value) return "Owner";
	if (state.value?.admin_ids.includes(discordId.value)) return "Admin";
	if (state.value?.grants.some((grant) => grant.discord_id === discordId.value))
		return "Listener";
	return null;
});
const guilds = computed(() => [...new Set(matchingMembers.value.map((entry) => entry.guild_name))]);

onMounted(async () => {
	try {
		const [accessState, directory] = await Promise.all([access.state(), access.members()]);
		state.value = accessState;
		members.value = directory.members;
	} catch {
		error.value = "This listener profile could not be loaded.";
	} finally {
		loading.value = false;
	}
});
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
				<div class="mx-auto w-full max-w-2xl pb-4 sm:pb-6">
					<div v-if="loading" role="status" class="text-muted py-12">
						Loading listener profile…
					</div>
					<div v-else-if="error" role="alert" class="text-error py-12">
						{{ error }}
					</div>
					<section v-else class="rounded-2xl border border-default p-6 sm:p-8">
						<div class="flex flex-col gap-5 sm:flex-row sm:items-center">
							<UAvatar
								:src="member?.avatar_url ?? undefined"
								:alt="member?.display_name ?? discordId"
								size="3xl"
							/>
							<div class="min-w-0 flex-1">
								<div class="flex flex-wrap items-center gap-3">
									<h1 class="truncate text-2xl font-semibold text-highlighted">
										{{ member?.display_name ?? "Unknown listener" }}
									</h1>
									<UBadge
										v-if="role"
										:label="role"
										color="primary"
										variant="subtle"
									/>
								</div>
								<p v-if="member" class="text-muted mt-1 text-sm">
									@{{ member.name }}
								</p>
							</div>
						</div>
						<dl class="mt-8 grid gap-6 border-t border-default pt-6 sm:grid-cols-2">
							<div>
								<dt class="text-muted text-xs">Discord ID</dt>
								<dd class="mt-1 break-all text-sm font-medium text-highlighted">
									{{ discordId }}
								</dd>
							</div>
							<div>
								<dt class="text-muted text-xs">Connected servers</dt>
								<dd class="mt-1 text-sm font-medium text-highlighted">
									{{
										guilds.length ? guilds.join(", ") : "Not currently visible"
									}}
								</dd>
							</div>
						</dl>
					</section>
				</div>
			</template>
		</UDashboardPanel>
	</div>
</template>
