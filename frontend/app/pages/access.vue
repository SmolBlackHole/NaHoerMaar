<script setup lang="ts">
import type { components } from "#shared/api.generated";
import { useRepositories } from "~/repositories";

type AccessState = components["schemas"]["AccessView"];
type AccessGrant = components["schemas"]["AccessGrantView"];
type DiscordMember = components["schemas"]["DiscordMemberView"];
type DirectoryMember = DiscordMember & { guild_names: string[] };

useSeoMeta({ title: "Access | NaHörMaar" });
const profile = useProfileStore();
const { access } = useRepositories();
const toast = useToast();
const state = shallowRef<AccessState | null>(null);
const members = shallowRef<DiscordMember[]>([]);
const query = ref("");
const directId = ref("");
const busy = ref("");
const loading = ref(false);
const error = ref("");
const operatorsExpanded = ref(false);

const directoryMembers = computed<DirectoryMember[]>(() => {
	const unique = new Map<string, DirectoryMember>();
	for (const member of members.value) {
		const existing = unique.get(member.discord_id);
		if (existing) {
			if (!existing.guild_names.includes(member.guild_name))
				existing.guild_names.push(member.guild_name);
			continue;
		}
		unique.set(member.discord_id, { ...member, guild_names: [member.guild_name] });
	}
	return [...unique.values()].sort((left, right) =>
		left.display_name.localeCompare(right.display_name, undefined, { sensitivity: "base" }),
	);
});
const memberById = computed(
	() => new Map(directoryMembers.value.map((member) => [member.discord_id, member])),
);
const grantedIds = computed(
	() =>
		new Set([
			...(state.value?.grants.map((grant) => grant.discord_id) ?? []),
			...(state.value?.admin_ids ?? []),
			...(state.value ? [state.value.owner_id] : []),
		]),
);
const ungrantedMembers = computed(() =>
	directoryMembers.value.filter((member) => !grantedIds.value.has(member.discord_id)),
);
const availableMembers = computed(() => {
	const needle = query.value.trim().toLocaleLowerCase();
	return ungrantedMembers.value.filter(
		(member) =>
			!needle ||
			member.display_name.toLocaleLowerCase().includes(needle) ||
			member.name.toLocaleLowerCase().includes(needle) ||
			member.discord_id.includes(needle) ||
			member.guild_names.some((guild) => guild.toLocaleLowerCase().includes(needle)),
	);
});
const ownRole = computed(() => {
	if (profile.session?.role === "owner") return "Owner";
	if (profile.session?.role === "admin") return "Admin";
	return "Listener";
});
const metrics = computed(() => [
	{ label: "Your role", value: ownRole.value, icon: "i-lucide-shield-check" },
	{
		label: "Allowed listeners",
		value: state.value?.grants.length ?? 0,
		icon: "i-lucide-user-round-check",
	},
	{
		label: "Available members",
		value: ungrantedMembers.value.length,
		icon: "i-lucide-users",
	},
	{
		label: "Operators",
		value: state.value ? state.value.admin_ids.length + 1 : 0,
		icon: "i-lucide-key-round",
	},
]);
const operators = computed(() => {
	if (!state.value) return [];
	return [
		{ discordId: state.value.owner_id, role: "Owner" },
		...state.value.admin_ids.map((discordId) => ({ discordId, role: "Admin" })),
	];
});
const visibleOperators = computed(() =>
	operatorsExpanded.value ? operators.value : operators.value.slice(0, 4),
);
const canRevoke = (grant: AccessGrant) =>
	profile.session?.role === "owner" || grant.granted_by === profile.session?.discord_id;
const displayName = (discordId: string, fallback?: string | null) =>
	memberById.value.get(discordId)?.display_name ?? fallback ?? discordId;
const avatar = (discordId: string) => memberById.value.get(discordId)?.avatar_url ?? undefined;
const guilds = (member: DirectoryMember) => member.guild_names.join(", ");
const time = (value: string) =>
	new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(
		new Date(value),
	);
const isBusy = (discordId: string) => Boolean(discordId) && busy.value === discordId;

async function load() {
	if (!profile.session?.is_admin) return;
	loading.value = true;
	error.value = "";
	try {
		const [accessState, directory] = await Promise.all([access.state(), access.members()]);
		state.value = accessState;
		members.value = directory.members;
	} catch {
		error.value = "Access settings could not be loaded. Try refreshing the page.";
	} finally {
		loading.value = false;
	}
}

async function grant(discordId: string) {
	const identifier = discordId.trim();
	if (!identifier || busy.value) return;
	const name = displayName(identifier);
	busy.value = identifier;
	error.value = "";
	try {
		state.value = await access.grant(identifier);
		directId.value = "";
		toast.add({
			title: "Listener added",
			description: `${name} can now use NaHörMaar.`,
			icon: "i-lucide-user-check",
			color: "success",
		});
	} catch {
		error.value = "Access could not be granted. Check the Discord ID and try again.";
	} finally {
		busy.value = "";
	}
}

async function revoke(discordId: string) {
	if (busy.value) return;
	const name = displayName(discordId);
	busy.value = discordId;
	error.value = "";
	try {
		state.value = await access.revoke(discordId);
		toast.add({
			title: "Listener removed",
			description: `${name} can no longer use NaHörMaar.`,
			icon: "i-lucide-user-minus",
			color: "neutral",
		});
	} catch {
		error.value = "Access could not be removed. Admins can remove only their own grants.";
	} finally {
		busy.value = "";
	}
}

watch(
	() => profile.session?.is_admin,
	(isAdmin) => {
		if (isAdmin && !state.value) void load();
	},
	{ immediate: true },
);
</script>

<template>
	<div class="flex min-h-0 min-w-0 flex-1 flex-col">
		<UDashboardPanel id="access" class="min-w-0" :ui="{ body: 'pt-4 sm:pt-4' }">
			<template #header>
				<UDashboardNavbar title="Access">
					<template #leading><UDashboardSidebarCollapse /></template>
					<template #right>
						<PlayerConnection />
						<UButton
							label="Refresh"
							icon="i-lucide-refresh-cw"
							color="neutral"
							variant="outline"
							:loading="loading"
							@click="load"
						/>
					</template>
				</UDashboardNavbar>
			</template>
			<template #body>
				<div v-if="!profile.session?.is_admin" class="p-6 text-muted" role="alert">
					Only owners and admins can manage listener access.
				</div>
				<div v-else class="space-y-8 pb-4 sm:pb-6">
					<div class="flex flex-wrap items-end justify-between gap-4">
						<div>
							<h1 class="text-highlighted text-2xl font-semibold">Listener access</h1>
							<p class="text-muted mt-2 max-w-2xl text-sm">
								Choose who can open the dashboard, request tracks and control the
								bot.
							</p>
						</div>
					</div>

					<div v-if="error" class="access-error" role="alert">
						<UIcon name="i-lucide-circle-alert" class="size-4 shrink-0" />
						<span>{{ error }}</span>
					</div>

					<div v-if="loading && !state" role="status" class="text-muted py-12">
						Loading listener access…
					</div>
					<template v-else-if="state">
						<dl class="grid grid-cols-2 gap-4 xl:grid-cols-4">
							<div
								v-for="metric in metrics"
								:key="metric.label"
								class="rounded-xl border border-default p-5"
							>
								<dt class="text-muted flex items-center gap-2 text-sm">
									<UIcon :name="metric.icon" class="size-4 shrink-0" />
									{{ metric.label }}
								</dt>
								<dd
									class="text-highlighted mt-4 text-3xl font-semibold tabular-nums"
								>
									{{ metric.value }}
								</dd>
							</div>
						</dl>

						<section aria-labelledby="operators-title">
							<div class="flex items-end justify-between gap-4">
								<div>
									<h2
										id="operators-title"
										class="text-highlighted text-lg font-semibold"
									>
										Operators
									</h2>
									<p class="text-muted mt-1 text-xs">
										Owner and admin roles are managed in access.toml
									</p>
								</div>
								<UButton
									v-if="operators.length > 4"
									:label="
										operatorsExpanded
											? 'Show fewer'
											: `Show all ${operators.length}`
									"
									:icon="
										operatorsExpanded
											? 'i-lucide-chevron-up'
											: 'i-lucide-chevron-down'
									"
									color="neutral"
									variant="ghost"
									size="sm"
									:aria-expanded="operatorsExpanded"
									aria-controls="operator-list"
									@click="operatorsExpanded = !operatorsExpanded"
								/>
							</div>
							<div
								id="operator-list"
								class="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4"
							>
								<NuxtLink
									v-for="operator in visibleOperators"
									:key="operator.discordId"
									:to="`/profile/${operator.discordId}`"
									class="operator-row group"
									:aria-label="`Open ${displayName(operator.discordId)}'s profile`"
								>
									<UAvatar
										:src="avatar(operator.discordId)"
										:alt="displayName(operator.discordId)"
										size="sm"
									/>
									<div class="min-w-0 flex-1">
										<p class="truncate text-sm font-medium text-highlighted">
											{{ displayName(operator.discordId) }}
										</p>
										<p class="text-muted truncate text-xs">
											{{ operator.discordId }}
										</p>
									</div>
									<UBadge
										:label="operator.role"
										color="primary"
										variant="subtle"
										size="sm"
									/>
									<UIcon
										name="i-lucide-chevron-right"
										class="text-muted size-4 shrink-0 transition-transform group-hover:translate-x-0.5"
									/>
								</NuxtLink>
							</div>
						</section>

						<div class="grid gap-8 xl:grid-cols-[minmax(0,1.5fr)_minmax(18rem,1fr)]">
							<section aria-labelledby="members-heading" class="min-w-0">
								<div class="flex items-start justify-between gap-4">
									<div>
										<h2
											id="members-heading"
											class="text-highlighted text-lg font-semibold"
										>
											Available on Discord
										</h2>
										<p class="text-muted mt-1 text-xs">
											People from every connected server
										</p>
									</div>
									<span class="text-muted text-xs tabular-nums">
										{{ availableMembers.length }} available
									</span>
								</div>
								<UInput
									v-model="query"
									icon="i-lucide-search"
									placeholder="Search people or servers"
									aria-label="Search Discord members"
									class="mt-5 w-full"
								>
									<template v-if="query" #trailing>
										<UButton
											icon="i-lucide-x"
											aria-label="Clear search"
											color="neutral"
											variant="link"
											size="xs"
											@click="query = ''"
										/>
									</template>
								</UInput>
								<div class="access-list mt-3" aria-live="polite">
									<UTooltip
										v-for="member in availableMembers"
										:key="member.discord_id"
										:text="`Discord ID: ${member.discord_id}`"
									>
										<div class="access-row">
											<UAvatar
												:src="member.avatar_url ?? undefined"
												:alt="member.display_name"
												size="sm"
											/>
											<div class="min-w-0 flex-1">
												<p
													class="truncate text-sm font-medium text-highlighted"
												>
													{{ member.display_name }}
												</p>
												<p class="text-muted truncate text-xs">
													@{{ member.name }} · {{ guilds(member) }}
												</p>
											</div>
											<UButton
												:label="
													isBusy(member.discord_id) ? 'Adding…' : 'Allow'
												"
												:icon="
													isBusy(member.discord_id)
														? undefined
														: 'i-lucide-user-plus'
												"
												color="neutral"
												variant="soft"
												size="sm"
												:disabled="Boolean(busy)"
												@click="grant(member.discord_id)"
											/>
										</div>
									</UTooltip>
									<div v-if="!availableMembers.length" class="empty-state">
										<UIcon name="i-lucide-users" class="size-5" />
										<p class="text-sm font-medium text-highlighted">
											{{
												query
													? "No matching people"
													: "Everyone here already has access"
											}}
										</p>
										<p class="text-muted text-xs">
											{{
												query
													? "Try another name or server."
													: "There is nobody left to add."
											}}
										</p>
									</div>
								</div>
							</section>

							<section aria-labelledby="listeners-heading" class="min-w-0">
								<div class="flex items-start justify-between gap-4">
									<div>
										<h2
											id="listeners-heading"
											class="text-highlighted text-lg font-semibold"
										>
											Allowed listeners
										</h2>
										<p class="text-muted mt-1 text-xs">
											People who can use the player now
										</p>
									</div>
									<span class="text-muted text-xs tabular-nums">
										{{ state.grants.length }} allowed
									</span>
								</div>
								<form class="mt-5 flex gap-2" @submit.prevent="grant(directId)">
									<UInput
										v-model="directId"
										placeholder="Discord user ID"
										inputmode="numeric"
										aria-label="Discord user ID"
										class="min-w-0 flex-1"
									/>
									<UButton
										type="submit"
										:label="isBusy(directId.trim()) ? 'Adding…' : 'Add'"
										icon="i-lucide-user-plus"
										:disabled="!directId.trim() || Boolean(busy)"
									/>
								</form>
								<div class="access-list mt-3" aria-live="polite">
									<div
										v-for="grantItem in state.grants"
										:key="grantItem.discord_id"
										class="access-row"
									>
										<UAvatar
											:src="avatar(grantItem.discord_id)"
											:alt="displayName(grantItem.discord_id, grantItem.name)"
											size="sm"
										/>
										<div class="min-w-0 flex-1">
											<p
												class="truncate text-sm font-medium text-highlighted"
											>
												{{
													displayName(
														grantItem.discord_id,
														grantItem.name,
													)
												}}
											</p>
											<UTooltip :text="time(grantItem.granted_at)">
												<p class="text-muted truncate text-xs">
													Added by {{ displayName(grantItem.granted_by) }}
												</p>
											</UTooltip>
										</div>
										<UTooltip
											:text="
												canRevoke(grantItem)
													? 'Remove listener access'
													: 'Only the granting admin or owner can remove this listener'
											"
										>
											<UButton
												icon="i-lucide-user-minus"
												aria-label="Remove listener access"
												color="error"
												variant="ghost"
												size="sm"
												:disabled="Boolean(busy) || !canRevoke(grantItem)"
												@click="revoke(grantItem.discord_id)"
											/>
										</UTooltip>
									</div>
									<div v-if="!state.grants.length" class="empty-state">
										<UIcon name="i-lucide-user-round-check" class="size-5" />
										<p class="text-sm font-medium text-highlighted">
											No listeners yet
										</p>
										<p class="text-muted text-xs">
											Choose someone from Discord to get started.
										</p>
									</div>
								</div>
							</section>
						</div>

						<section
							v-if="state.history.length"
							class="border-t border-default pt-6"
							aria-labelledby="history-title"
						>
							<div class="mb-3">
								<h2
									id="history-title"
									class="text-highlighted text-lg font-semibold"
								>
									Recent changes
								</h2>
								<p class="text-muted mt-1 text-xs">
									The latest listener access decisions
								</p>
							</div>
							<div class="grid gap-x-8 xl:grid-cols-2">
								<div
									v-for="event in state.history"
									:key="event.id"
									class="history-row"
								>
									<UIcon
										:name="
											event.action === 'granted'
												? 'i-lucide-user-plus'
												: 'i-lucide-user-minus'
										"
										:class="
											event.action === 'granted'
												? 'text-success'
												: 'text-error'
										"
										class="mt-0.5 size-4 shrink-0"
									/>
									<p class="min-w-0 flex-1 text-sm">
										<span class="font-medium text-highlighted">
											{{ displayName(event.actor_id) }}
										</span>
										<span class="text-muted">
											{{
												event.action === "granted"
													? " allowed "
													: " removed "
											}}
										</span>
										<span class="font-medium text-highlighted">
											{{ displayName(event.discord_id) }}
										</span>
									</p>
									<time
										class="text-muted shrink-0 text-xs tabular-nums"
										:datetime="event.occurred_at"
									>
										{{ time(event.occurred_at) }}
									</time>
								</div>
							</div>
						</section>
					</template>
				</div>
			</template>
		</UDashboardPanel>
	</div>
</template>

<style scoped>
.access-error {
	display: flex;
	align-items: center;
	gap: 0.625rem;
	border-radius: 0.75rem;
	background: color-mix(in srgb, var(--ui-error) 10%, transparent);
	padding: 0.75rem 0.875rem;
	color: var(--ui-error);
	font-size: 0.875rem;
}
.access-list {
	max-height: 30rem;
	overflow: auto;
}
.access-row {
	display: flex;
	min-height: 4rem;
	align-items: center;
	gap: 0.75rem;
	padding: 0.625rem 0.5rem;
}
.access-row + .access-row {
	border-top: 1px solid color-mix(in srgb, var(--ui-border) 70%, transparent);
}
.access-row:hover {
	background: color-mix(in srgb, var(--ui-bg-elevated) 55%, transparent);
}
.empty-state {
	display: flex;
	min-height: 12rem;
	align-items: center;
	justify-content: center;
	flex-direction: column;
	gap: 0.375rem;
	padding: 1.5rem;
	text-align: center;
}
.operator-row {
	display: flex;
	align-items: center;
	gap: 0.75rem;
	border-radius: 0.75rem;
	background: color-mix(in srgb, var(--ui-bg-elevated) 55%, transparent);
	padding: 0.75rem;
	transition:
		background-color 150ms ease,
		transform 150ms ease;
}
.operator-row:hover {
	background: color-mix(in srgb, var(--ui-bg-elevated) 90%, transparent);
	transform: translateY(-1px);
}
.operator-row:focus-visible {
	outline: 2px solid var(--ui-primary);
	outline-offset: 2px;
}
.history-row {
	display: flex;
	align-items: flex-start;
	gap: 0.625rem;
	border-bottom: 1px solid color-mix(in srgb, var(--ui-border) 70%, transparent);
	padding: 0.75rem 0;
}
@media (max-width: 639px) {
	.access-row {
		padding-inline: 0;
	}
	.history-row {
		flex-wrap: wrap;
	}
	.history-row time {
		width: 100%;
		padding-left: 1.625rem;
	}
}
</style>
