<script setup lang="ts">
import type { AccessState, DiscordMembers } from "~/core/models/access";

type AccessGrant = AccessState["grants"][number];
type AccessUser = AccessState["operators"][number];
type DirectoryMember = DiscordMembers["members"][number] & { guildNames: string[] };

useSeoMeta({ title: "Access | NaHÃ¶rMaar" });
const core = useNuxtApp().$backendCore;
const session = core.stores.useSessionStore();
const access = core.workflows.access();
const toast = useToast();
const query = ref("");
const directId = ref("");
const operatorsExpanded = ref(false);

const page = computed(() => access.page.data.value);
const state = computed(() => page.value?.access ?? null);
const members = computed(() => page.value?.members ?? []);
const isAdmin = computed(
	() => session.account?.role === "owner" || session.account?.role === "admin",
);
const directoryMembers = computed<DirectoryMember[]>(() => {
	const unique = new Map<string, DirectoryMember>();
	for (const member of members.value) {
		const existing = unique.get(member.discord_id);
		if (existing) {
			if (!existing.guildNames.includes(member.guild_name)) {
				existing.guildNames.push(member.guild_name);
			}
			continue;
		}
		unique.set(member.discord_id, { ...member, guildNames: [member.guild_name] });
	}
	return [...unique.values()].sort((left, right) =>
		left.display_name.localeCompare(right.display_name, undefined, {
			sensitivity: "base",
		}),
	);
});
const memberByDiscordId = computed(
	() => new Map(directoryMembers.value.map((member) => [member.discord_id, member])),
);
const knownUsers = computed(() => [
	...(state.value?.operators ?? []),
	...(state.value?.grants.map((grant) => grant.user) ?? []),
]);
const userById = computed(() => new Map(knownUsers.value.map((user) => [user.id, user])));
const grantedDiscordIds = computed(
	() =>
		new Set([
			...(state.value?.operators.map((user) => user.discord.id) ?? []),
			...(state.value?.grants.map((grant) => grant.user.discord.id) ?? []),
		]),
);
const availableMembers = computed(() => {
	const needle = query.value.trim().toLocaleLowerCase();
	return directoryMembers.value.filter(
		(member) =>
			!grantedDiscordIds.value.has(member.discord_id) &&
			(!needle ||
				member.display_name.toLocaleLowerCase().includes(needle) ||
				member.username.toLocaleLowerCase().includes(needle) ||
				member.discord_id.includes(needle) ||
				member.guildNames.some((guild) => guild.toLocaleLowerCase().includes(needle))),
	);
});
const operators = computed(() => state.value?.operators ?? []);
const visibleOperators = computed(() =>
	operatorsExpanded.value ? operators.value : operators.value.slice(0, 4),
);
const metrics = computed(() => [
	{
		label: "Your role",
		value: roleLabel(session.account?.role),
		icon: "i-lucide-shield-check",
	},
	{
		label: "Allowed listeners",
		value: state.value?.grants.length ?? 0,
		icon: "i-lucide-user-round-check",
	},
	{
		label: "Available members",
		value: availableMembers.value.length,
		icon: "i-lucide-users",
	},
	{
		label: "Operators",
		value: operators.value.length,
		icon: "i-lucide-key-round",
	},
]);
const error = computed(() => access.mutationError.value ?? access.page.error.value);

function roleLabel(role: AccessUser["role"] | undefined) {
	if (role === "owner") return "Owner";
	if (role === "admin") return "Admin";
	return "Listener";
}
function displayUser(user: AccessUser) {
	return user.profile.display_name ?? user.discord.username ?? user.discord.id;
}
function displayUserId(userId: string | null) {
	if (!userId) return "System";
	const user = userById.value.get(userId);
	return user ? displayUser(user) : `User ${userId.slice(0, 8)}`;
}
function avatar(user: AccessUser) {
	const directoryAvatar = memberByDiscordId.value.get(user.discord.id)?.avatar_url;
	if (directoryAvatar) return directoryAvatar;
	if (!user.discord.avatar_hash) return undefined;
	const extension = user.discord.avatar_hash.startsWith("a_") ? "gif" : "png";
	return `https://cdn.discordapp.com/avatars/${user.discord.id}/${user.discord.avatar_hash}.${extension}?size=64`;
}
function profileLink(user: AccessUser) {
	return user.id === session.account?.user_id ? "/profile" : `/profile/${user.id}`;
}
function canRevoke(grant: AccessGrant) {
	return (
		session.account?.role === "owner" || grant.granted_by_user_id === session.account?.user_id
	);
}
function isPending(discordId: string) {
	return access.pendingDiscordIds.value.includes(discordId);
}
function historyIcon(action: AccessState["history"][number]["action"]) {
	if (action === "granted") return "i-lucide-user-plus";
	if (action === "revoked") return "i-lucide-user-minus";
	return "i-lucide-shield-check";
}
function historyTone(action: AccessState["history"][number]["action"]) {
	if (action === "granted") return "text-success";
	if (action === "revoked") return "text-error";
	return "text-primary";
}
function historyVerb(action: AccessState["history"][number]["action"]) {
	if (action === "granted") return " allowed ";
	if (action === "revoked") return " removed ";
	return " changed the operator role for ";
}
function formatTime(value: string) {
	return new Intl.DateTimeFormat(undefined, {
		dateStyle: "medium",
		timeStyle: "short",
	}).format(new Date(value));
}

async function load() {
	if (isAdmin.value) await access.load();
}
async function grant(discordId: string) {
	const identifier = discordId.trim();
	if (!identifier || isPending(identifier)) return;
	const member = memberByDiscordId.value.get(identifier);
	if (!(await access.grant(identifier))) return;
	directId.value = "";
	toast.add({
		title: "Listener added",
		description: `${member?.display_name ?? identifier} can now use NaHÃ¶rMaar.`,
		icon: "i-lucide-user-check",
		color: "success",
	});
}
async function revoke(grant: AccessGrant) {
	const discordId = grant.user.discord.id;
	if (!(await access.revoke(discordId))) return;
	toast.add({
		title: "Listener removed",
		description: `${displayUser(grant.user)} can no longer use NaHÃ¶rMaar.`,
		icon: "i-lucide-user-minus",
		color: "neutral",
	});
}

watch(
	isAdmin,
	(allowed) => {
		if (allowed && !state.value) void load();
	},
	{ immediate: true },
);
onScopeDispose(access.dispose);
</script>

<template>
	<div class="flex min-h-0 min-w-0 flex-1 flex-col">
		<UDashboardPanel id="access" class="min-w-0" :ui="{ body: 'pt-4 sm:pt-4' }">
			<template #header>
				<UDashboardNavbar title="Access">
					<template #leading><UDashboardSidebarCollapse /></template>
					<template #right>
						<UButton
							label="Refresh"
							icon="i-lucide-refresh-cw"
							color="neutral"
							variant="outline"
							:loading="access.page.loading.value"
							@click="load"
						/>
					</template>
				</UDashboardNavbar>
			</template>
			<template #body>
				<div v-if="!isAdmin" class="grid min-h-80 place-items-center" role="alert">
					<div class="max-w-sm text-center">
						<UIcon name="i-lucide-shield-x" class="mx-auto size-10 text-muted" />
						<h1 class="mt-4 text-lg font-semibold text-highlighted">
							Admin access required
						</h1>
						<p class="mt-2 text-sm leading-relaxed text-muted">
							Only owners and admins can manage listener access.
						</p>
					</div>
				</div>

				<div v-else class="mx-auto w-full max-w-7xl space-y-8 pb-4 sm:pb-6">
					<header>
						<h1 class="text-2xl font-semibold text-highlighted">Listener access</h1>
						<p class="mt-2 max-w-2xl text-sm text-muted">
							Choose who can open the dashboard, request tracks and control the bot.
						</p>
					</header>

					<div
						v-if="error"
						class="flex items-center gap-2.5 rounded-xl bg-error/10 px-3.5 py-3 text-sm text-error"
						role="alert"
					>
						<UIcon name="i-lucide-circle-alert" class="size-4 shrink-0" />
						<span>{{ error }}</span>
					</div>

					<div
						v-if="access.page.loading.value && !state"
						aria-label="Loading listener access"
					>
						<div
							class="grid overflow-hidden rounded-2xl border border-default sm:grid-cols-2 xl:grid-cols-4"
						>
							<div v-for="index in 4" :key="index" class="space-y-3 p-5">
								<USkeleton class="h-4 w-28" />
								<USkeleton class="h-8 w-20" />
							</div>
						</div>
						<div class="mt-8 grid gap-8 xl:grid-cols-2">
							<USkeleton class="h-96 rounded-2xl" />
							<USkeleton class="h-96 rounded-2xl" />
						</div>
					</div>

					<template v-else-if="state">
						<dl
							class="grid overflow-hidden rounded-2xl border border-default sm:grid-cols-2 xl:grid-cols-4"
						>
							<div
								v-for="(metric, index) in metrics"
								:key="metric.label"
								class="p-5"
								:class="[
									index > 0 && 'border-t border-default sm:border-t-0',
									index % 2 === 1 && 'sm:border-l sm:border-default',
									index === 2 &&
										'sm:border-l-0 sm:border-t xl:border-l xl:border-t-0',
									index === 3 && 'sm:border-t xl:border-t-0',
								]"
							>
								<dt class="flex items-center gap-2 text-sm text-muted">
									<UIcon :name="metric.icon" class="size-4 shrink-0" />
									{{ metric.label }}
								</dt>
								<dd
									class="mt-3 text-2xl font-semibold tabular-nums text-highlighted"
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
										class="text-lg font-semibold text-highlighted"
									>
										Operators
									</h2>
									<p class="mt-1 text-xs text-muted">
										Owner and admin roles are managed in config/access.toml.
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
									:key="operator.id"
									:to="profileLink(operator)"
									class="operator-row group"
									:aria-label="`Open ${displayUser(operator)}'s profile`"
								>
									<UAvatar
										:src="avatar(operator)"
										:alt="displayUser(operator)"
										size="sm"
									/>
									<div class="min-w-0 flex-1">
										<p class="truncate text-sm font-medium text-highlighted">
											{{ displayUser(operator) }}
										</p>
										<p class="truncate text-xs text-muted">
											@{{ operator.discord.username ?? operator.discord.id }}
										</p>
									</div>
									<UBadge
										:label="roleLabel(operator.role)"
										color="primary"
										variant="subtle"
										size="sm"
									/>
									<UIcon
										name="i-lucide-chevron-right"
										class="size-4 shrink-0 text-muted transition-transform group-hover:translate-x-0.5"
									/>
								</NuxtLink>
							</div>
						</section>

						<div class="grid gap-8 xl:grid-cols-2">
							<section aria-labelledby="members-heading" class="min-w-0">
								<div class="flex items-start justify-between gap-4">
									<div>
										<h2
											id="members-heading"
											class="text-lg font-semibold text-highlighted"
										>
											Available on Discord
										</h2>
										<p class="mt-1 text-xs text-muted">
											People from every connected server
										</p>
									</div>
									<span class="text-xs tabular-nums text-muted"
										>{{ availableMembers.length }} available</span
									>
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
												<p class="truncate text-xs text-muted">
													@{{ member.username }} Â·
													{{ member.guildNames.join(", ") }}
												</p>
											</div>
											<UButton
												:label="
													isPending(member.discord_id)
														? 'Addingâ€¦'
														: 'Allow'
												"
												:icon="
													isPending(member.discord_id)
														? undefined
														: 'i-lucide-user-plus'
												"
												color="neutral"
												variant="soft"
												size="sm"
												:disabled="
													access.pendingDiscordIds.value.length > 0
												"
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
										<p class="text-xs text-muted">
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
											class="text-lg font-semibold text-highlighted"
										>
											Allowed listeners
										</h2>
										<p class="mt-1 text-xs text-muted">
											People who can use the player now
										</p>
									</div>
									<span class="text-xs tabular-nums text-muted"
										>{{ state.grants.length }} allowed</span
									>
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
										:label="isPending(directId.trim()) ? 'Addingâ€¦' : 'Add'"
										icon="i-lucide-user-plus"
										:disabled="
											!directId.trim() ||
											access.pendingDiscordIds.value.length > 0
										"
									/>
								</form>
								<div class="access-list mt-3" aria-live="polite">
									<NuxtLink
										v-for="grantItem in state.grants"
										:key="grantItem.user.id"
										:to="profileLink(grantItem.user)"
										class="access-row group"
									>
										<UAvatar
											:src="avatar(grantItem.user)"
											:alt="displayUser(grantItem.user)"
											size="sm"
										/>
										<div class="min-w-0 flex-1">
											<p
												class="truncate text-sm font-medium text-highlighted"
											>
												{{ displayUser(grantItem.user) }}
											</p>
											<UTooltip :text="formatTime(grantItem.granted_at)">
												<p class="truncate text-xs text-muted">
													Added by
													{{
														displayUserId(grantItem.granted_by_user_id)
													}}
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
												:disabled="
													access.pendingDiscordIds.value.length > 0 ||
													!canRevoke(grantItem)
												"
												@click.prevent.stop="revoke(grantItem)"
											/>
										</UTooltip>
										<UIcon
											name="i-lucide-chevron-right"
											class="size-4 shrink-0 text-muted transition-transform group-hover:translate-x-0.5"
										/>
									</NuxtLink>
									<div v-if="!state.grants.length" class="empty-state">
										<UIcon name="i-lucide-user-round-check" class="size-5" />
										<p class="text-sm font-medium text-highlighted">
											No listeners yet
										</p>
										<p class="text-xs text-muted">
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
							<h2 id="history-title" class="text-lg font-semibold text-highlighted">
								Recent changes
							</h2>
							<p class="mt-1 text-xs text-muted">
								The latest listener access decisions
							</p>
							<div class="mt-3 grid gap-x-8 xl:grid-cols-2">
								<div
									v-for="event in state.history"
									:key="event.id"
									class="history-row"
								>
									<UIcon
										:name="historyIcon(event.action)"
										:class="historyTone(event.action)"
										class="mt-0.5 size-4 shrink-0"
									/>
									<p class="min-w-0 flex-1 text-sm">
										<span class="font-medium text-highlighted">{{
											displayUserId(event.actor_user_id)
										}}</span>
										<span class="text-muted">{{
											historyVerb(event.action)
										}}</span>
										<span class="font-medium text-highlighted">{{
											displayUserId(event.subject_user_id)
										}}</span>
									</p>
									<time
										class="shrink-0 text-xs tabular-nums text-muted"
										:datetime="event.occurred_at"
									>
										{{ formatTime(event.occurred_at) }}
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
	border-radius: 0.75rem;
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
.operator-row:focus-visible,
.access-row:focus-visible {
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
