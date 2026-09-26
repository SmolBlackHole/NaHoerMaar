<script setup lang="ts">
import type { ProfileUpdate } from "~/core/models/account";

const core = useNuxtApp().$backendCore;
const session = core.stores.useSessionStore();
const profile = core.stores.useProfileStore();
const currentProfile = computed(() => profile.profile?.profile ?? null);
const { icons } = useTheme();

onMounted(() => void profile.load());

async function save(value: ProfileUpdate) {
	if (!(await profile.updateProfile(value))) return;
	await session.refresh();
}
</script>

<template>
	<main class="flex min-h-dvh flex-col bg-default">
		<div class="flex flex-1 items-center justify-center px-6 py-12">
			<section aria-labelledby="welcome-title" class="w-full max-w-sm">
				<div class="mb-10 flex items-center gap-3 text-highlighted">
					<UIcon :name="icons.headphones" class="size-8 text-primary" /><span
						class="text-2xl font-semibold"
						>NaHörMaar</span
					>
				</div>
				<h1 id="welcome-title" class="text-highlighted text-3xl font-semibold">
					Make yourself at home
				</h1>
				<p class="text-muted mt-3 mb-8 text-sm leading-relaxed">
					Pick a name, then put something on.
				</p>
				<ProfileForm
					:profile="currentProfile"
					:busy="profile.saving"
					:error="profile.error ?? session.error"
					@save="save"
				/>
				<p class="text-muted mt-5 text-xs leading-relaxed">
					Your name and avatar follow your Discord account.
				</p>
				<UButton
					label="Sign out"
					variant="link"
					color="neutral"
					class="mt-4 px-0"
					:loading="session.busy"
					@click="session.logout"
				/>
				<p v-if="profile.error" role="alert" class="text-error mt-3 text-sm">
					{{ profile.error }}
				</p>
			</section>
		</div>
		<LayoutAppFooter class="px-6 py-5" />
	</main>
</template>
