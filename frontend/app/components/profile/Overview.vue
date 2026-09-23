<script setup lang="ts">
import type { components } from "#shared/api.generated";

type ProfileView = components["schemas"]["ProfileView"];

const props = defineProps<{ value: ProfileView }>();
const member = computed(() => props.value.members[0] ?? null);
const guilds = computed(() => [...new Set(props.value.members.map((entry) => entry.guild_name))]);
const role = computed(() => {
	if (props.value.role === "owner") return "Owner";
	if (props.value.role === "admin") return "Admin";
	return "Listener";
});
</script>

<template>
	<section class="rounded-2xl bg-elevated/50 p-6 sm:p-8">
		<div class="flex flex-col gap-5 sm:flex-row sm:items-center">
			<img
				:src="`/avatars/${value.profile.avatar}.png`"
				:alt="`${value.profile.name}'s Pixabot`"
				width="112"
				height="112"
				class="size-28 shrink-0 rounded-2xl bg-muted [image-rendering:pixelated]"
			/>
			<div class="min-w-0 flex-1">
				<p class="text-muted text-xs font-medium uppercase tracking-wide">
					NaHörMaar profile
				</p>
				<div class="mt-2 flex flex-wrap items-center gap-3">
					<h1 class="truncate text-2xl font-semibold text-highlighted sm:text-3xl">
						{{ value.profile.name }}
					</h1>
					<UBadge :label="role" color="primary" variant="subtle" />
				</div>
			</div>
		</div>

		<div class="mt-7 rounded-xl bg-default/70 p-4 sm:p-5">
			<div class="flex items-center gap-4">
				<UAvatar
					:src="member?.avatar_url ?? undefined"
					:alt="member?.display_name ?? value.discord_id"
					size="lg"
				/>
				<div class="min-w-0 flex-1">
					<p class="text-muted text-xs">Linked Discord account</p>
					<p class="mt-1 truncate text-sm font-medium text-highlighted">
						{{ member?.display_name ?? "Discord details unavailable" }}
					</p>
					<p v-if="member" class="text-muted truncate text-xs">@{{ member.name }}</p>
				</div>
			</div>

			<dl class="mt-5 grid gap-4 sm:grid-cols-2">
				<div>
					<dt class="text-muted text-xs">Discord ID</dt>
					<dd class="mt-1 break-all text-sm font-medium text-highlighted">
						{{ value.discord_id }}
					</dd>
				</div>
				<div>
					<dt class="text-muted text-xs">Connected servers</dt>
					<dd class="mt-1 text-sm font-medium text-highlighted">
						{{ guilds.length ? guilds.join(", ") : "Not currently visible" }}
					</dd>
				</div>
			</dl>
		</div>
	</section>
</template>
