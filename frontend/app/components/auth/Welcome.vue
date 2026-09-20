<script setup lang="ts">
import { useProfileStore } from "~/stores/profile";
const profile = useProfileStore();
const route = useRoute();
const { icons } = useTheme();
const retrying = ref(false);
const failures: Record<string, { title: string; text: string }> = {
	access_denied: {
		title: "You're not on the list yet",
		text: "Ask the bot owner to add your Discord account, then sign in again.",
	},
	login_cancelled: {
		title: "No rush",
		text: "Discord sign-in was cancelled. You can try again whenever you're ready.",
	},
	login_expired: {
		title: "Let's try that again",
		text: "That sign-in link has expired or was already used. Start a new sign-in below.",
	},
	login_failed: {
		title: "Discord couldn't sign you in",
		text: "Something went wrong while connecting your account. Please try again.",
	},
	login_unavailable: {
		title: "Sign-in isn't ready yet",
		text: "The bot owner still needs to configure Discord sign-in.",
	},
	login_busy: {
		title: "Give it a moment",
		text: "Too many sign-ins are in progress. Try again shortly.",
	},
	access_unavailable: {
		title: "Access couldn't be checked",
		text: "The bot owner needs to check the access list. Please try again later.",
	},
};
const message = computed(() => {
	if (profile.status === "forbidden") return failures.access_denied!;
	if (profile.status === "unavailable")
		return {
			title: "Can't reach the bot",
			text: "Your sign-in couldn't be checked. Try again when the connection is back.",
		};
	return (
		failures[String(route.query.error ?? "")] ?? {
			title: "Put something on.",
			text: "Sign in with Discord to join the queue.",
		}
	);
});
async function retry() {
	retrying.value = true;
	try {
		await profile.restore();
	} finally {
		retrying.value = false;
	}
}
</script>

<template>
	<main class="login-page">
		<header class="login-brand">
			<UIcon :name="icons.headphones" /><span>NaHörMaar</span>
		</header>
		<div class="login-body">
			<section aria-labelledby="login-title" class="login-content">
				<h1 id="login-title">{{ message.title }}</h1>
				<p class="login-description" role="status">{{ message.text }}</p>
				<UButton
					v-if="profile.status === 'unavailable'"
					label="Try again"
					:icon="icons.reload"
					size="xl"
					color="neutral"
					:loading="retrying"
					@click="retry"
				/>
				<UButton
					v-else
					to="/api/auth/discord"
					external
					label="Continue with Discord"
					:trailing-icon="icons.arrowRight"
					size="xl"
					color="neutral"
				/>
				<p class="login-note">A shared player for invited listeners.</p>
			</section>
			<UIcon :name="icons.headphones" class="login-art" aria-hidden="true" />
		</div>
		<LayoutAppFooter class="login-footer" />
	</main>
</template>

<style scoped>
.login-page {
	min-height: 100dvh;
	display: flex;
	flex-direction: column;
	background: var(--room-sidebar);
}
.login-brand {
	display: flex;
	align-items: center;
	gap: 0.65rem;
	padding: 2rem clamp(1.5rem, 6vw, 6rem);
	font-size: 1.1rem;
	font-weight: 650;
	color: var(--ui-text-highlighted);
}
.login-brand .iconify {
	width: 1.6rem;
	height: 1.6rem;
	color: var(--ui-primary);
}
.login-body {
	display: flex;
	align-items: center;
	justify-content: space-between;
	flex: 1;
	gap: 3rem;
	padding: 4rem clamp(1.5rem, 10vw, 10rem) 6rem;
}
.login-content {
	max-width: 34rem;
	position: relative;
	z-index: 1;
}
.login-content h1 {
	font-size: clamp(2.5rem, 4.6vw, 4.5rem);
	font-weight: 650;
	line-height: 1.08;
	letter-spacing: -0.035em;
	text-wrap: balance;
	color: var(--ui-text-highlighted);
}
.login-description {
	margin: 1.5rem 0 2.25rem;
	color: var(--ui-text-muted);
	max-width: 36ch;
	line-height: 1.7;
	font-size: 1rem;
}
.login-note {
	margin-top: 1.25rem;
	font-size: 0.8rem;
	color: var(--ui-text-muted);
}
.login-art {
	width: clamp(10rem, 22vw, 25rem);
	height: clamp(10rem, 22vw, 25rem);
	flex-shrink: 0;
	color: var(--ui-text);
	opacity: 0.04;
}
.login-footer {
	padding: 1.5rem clamp(1.5rem, 6vw, 6rem);
}
@media (max-width: 700px) {
	.login-art {
		display: none;
	}
	.login-body {
		padding-top: 2rem;
	}
}
</style>
