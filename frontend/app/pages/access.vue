<script setup lang="ts">
import type { components } from "#shared/api.generated";
import { useRepositories } from "~/repositories";

type AccessState = components["schemas"]["AccessView"];
type DiscordMember = components["schemas"]["DiscordMemberView"];

useSeoMeta({ title: "Access | NaHörMaar" });
const profile = useProfileStore();
const { access } = useRepositories();
const state = shallowRef<AccessState | null>(null);
const members = shallowRef<DiscordMember[]>([]);
const query = ref("");
const directId = ref("");
const busy = ref("");
const loading = ref(false);
const error = ref("");

const memberNames = computed(
	() => new Map(members.value.map((member) => [member.discord_id, member.display_name])),
);
const grantedIds = computed(
	() =>
		new Set([
			...(state.value?.grants.map((grant) => grant.discord_id) ?? []),
			...(state.value?.admin_ids ?? []),
			...(state.value ? [state.value.owner_id] : []),
		]),
);
const availableMembers = computed(() => {
	const needle = query.value.trim().toLocaleLowerCase();
	return members.value.filter(
		(member) =>
			!grantedIds.value.has(member.discord_id) &&
			(!needle ||
				member.display_name.toLocaleLowerCase().includes(needle) ||
				member.name.toLocaleLowerCase().includes(needle) ||
				member.discord_id.includes(needle) ||
				member.guild_name.toLocaleLowerCase().includes(needle)),
	);
});
const canRevoke = (grant: components["schemas"]["AccessGrantView"]) =>
	profile.session?.role === "owner" || grant.granted_by === profile.session?.discord_id;
const displayName = (discordId: string, fallback?: string | null) =>
	memberNames.value.get(discordId) ?? fallback ?? discordId;
const time = (value: string) =>
	new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(
		new Date(value),
	);

async function load() {
	if (!profile.session?.is_admin) return;
	loading.value = true;
	error.value = "";
	try {
		const [accessState, directory] = await Promise.all([access.state(), access.members()]);
		state.value = accessState;
		members.value = directory.members;
	} catch {
		error.value = "Access settings could not be loaded.";
	} finally {
		loading.value = false;
	}
}

async function grant(discordId: string) {
	const identifier = discordId.trim();
	if (!identifier || busy.value) return;
	busy.value = identifier;
	error.value = "";
	try {
		state.value = await access.grant(identifier);
		directId.value = "";
	} catch {
		error.value = "Access could not be granted.";
	} finally {
		busy.value = "";
	}
}

async function revoke(discordId: string) {
	if (busy.value) return;
	busy.value = discordId;
	error.value = "";
	try {
		state.value = await access.revoke(discordId);
	} catch {
		error.value = "Access could not be revoked. Admins can remove only their own grants.";
	} finally {
		busy.value = "";
	}
}

onMounted(load);
</script>

<template>
	<UDashboardPanel id="access" class="min-h-0 min-w-0">
		<template #header>
			<UDashboardNavbar title="Access">
				<template #leading><UDashboardSidebarCollapse /></template>
			</UDashboardNavbar>
		</template>
		<template #body>
			<div v-if="!profile.session?.is_admin" class="p-6 text-muted" role="alert">
				Only owners and admins can manage listener access.
			</div>
			<div v-else class="access-page py-4 sm:py-6">
				<div class="mb-8 flex flex-wrap items-start justify-between gap-4">
					<div>
						<h1 class="text-highlighted text-2xl font-semibold">
							Who can join the room?
						</h1>
						<p class="text-muted mt-2 max-w-2xl text-sm">
							Grant dashboard and bot access to people from a connected Discord
							server, or enter an ID directly.
						</p>
					</div>
					<UButton
						label="Refresh"
						icon="i-lucide-refresh-cw"
						color="neutral"
						variant="outline"
						:loading="loading"
						@click="load"
					/>
				</div>

				<p v-if="error" class="text-error mb-4 text-sm" role="alert">{{ error }}</p>

				<section class="mb-10" aria-labelledby="operators-title">
					<h2 id="operators-title" class="text-highlighted text-lg font-semibold">
						Operators
					</h2>
					<p class="text-muted mt-1 text-sm">
						Owner and admin roles come from the server configuration and cannot be
						removed here.
					</p>
					<div v-if="state" class="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
						<div class="rounded-lg bg-elevated/50 p-4">
							<p class="text-xs font-medium uppercase tracking-wide text-primary">
								Owner
							</p>
							<p class="mt-1 font-medium text-highlighted">
								{{ displayName(state.owner_id) }}
							</p>
							<p class="text-muted mt-1 text-xs">{{ state.owner_id }}</p>
						</div>
						<div
							v-for="adminId in state.admin_ids"
							:key="adminId"
							class="rounded-lg bg-elevated/50 p-4"
						>
							<p class="text-xs font-medium uppercase tracking-wide text-primary">
								Admin
							</p>
							<p class="mt-1 font-medium text-highlighted">
								{{ displayName(adminId) }}
							</p>
							<p class="text-muted mt-1 text-xs">{{ adminId }}</p>
						</div>
					</div>
				</section>

				<section class="grid gap-8 xl:grid-cols-[minmax(0,1.2fr)_minmax(20rem,0.8fr)]">
					<div>
						<h2 class="text-highlighted text-lg font-semibold">Discord members</h2>
						<UInput
							v-model="query"
							icon="i-lucide-search"
							placeholder="Search name, server or Discord ID"
							class="mt-4 w-full"
						/>
						<div class="mt-3 max-h-112 space-y-1 overflow-auto">
							<div
								v-for="member in availableMembers"
								:key="`${member.guild_id}:${member.discord_id}`"
								class="flex items-center gap-3 rounded-lg px-3 py-2.5 hover:bg-elevated/60"
							>
								<UAvatar
									:src="member.avatar_url ?? undefined"
									:alt="member.display_name"
									size="sm"
								/>
								<div class="min-w-0 flex-1">
									<p class="truncate font-medium text-highlighted">
										{{ member.display_name }}
									</p>
									<p class="text-muted truncate text-xs">
										{{ member.guild_name }} · {{ member.discord_id }}
									</p>
								</div>
								<UButton
									label="Allow"
									icon="i-lucide-user-plus"
									color="neutral"
									variant="ghost"
									:loading="busy === member.discord_id"
									@click="grant(member.discord_id)"
								/>
							</div>
							<p
								v-if="!loading && !availableMembers.length"
								class="text-muted py-6 text-sm"
							>
								No matching Discord members. You can still enter an ID directly.
							</p>
						</div>
					</div>

					<div>
						<h2 class="text-highlighted text-lg font-semibold">Allowed listeners</h2>
						<form class="mt-4 flex gap-2" @submit.prevent="grant(directId)">
							<UInput
								v-model="directId"
								placeholder="Discord user ID"
								class="min-w-0 flex-1"
							/>
							<UButton
								type="submit"
								label="Allow"
								:loading="busy === directId.trim()"
							/>
						</form>
						<div class="mt-4 space-y-1">
							<div
								v-for="grantItem in state?.grants ?? []"
								:key="grantItem.discord_id"
								class="flex items-center gap-3 rounded-lg px-3 py-2.5 hover:bg-elevated/60"
							>
								<div class="min-w-0 flex-1">
									<p class="truncate font-medium text-highlighted">
										{{ displayName(grantItem.discord_id, grantItem.name) }}
									</p>
									<p
										class="text-muted text-xs"
										:title="time(grantItem.granted_at)"
									>
										Allowed by {{ displayName(grantItem.granted_by) }}
									</p>
								</div>
								<UButton
									icon="i-lucide-user-minus"
									aria-label="Remove listener access"
									color="error"
									variant="ghost"
									:disabled="!canRevoke(grantItem)"
									:loading="busy === grantItem.discord_id"
									@click="revoke(grantItem.discord_id)"
								/>
							</div>
						</div>
					</div>
				</section>

				<section v-if="state?.history.length" class="mt-10" aria-labelledby="history-title">
					<h2 id="history-title" class="text-highlighted text-lg font-semibold">
						Recent changes
					</h2>
					<div class="mt-3 space-y-2">
						<p
							v-for="event in state.history"
							:key="event.id"
							class="text-muted text-sm"
						>
							<span class="text-highlighted font-medium">{{
								displayName(event.actor_id)
							}}</span>
							{{ event.action }} access for
							<span class="text-highlighted font-medium">{{
								displayName(event.discord_id)
							}}</span>
							· {{ time(event.occurred_at) }}
						</p>
					</div>
				</section>
			</div>
		</template>
	</UDashboardPanel>
</template>

<style scoped>
.access-page {
	width: min(100% - 2rem, 82rem);
	margin-inline: auto;
}
</style>
