<script setup lang="ts">
const consent = useConsentStore();
const { icons } = useTheme();
const details = ref(false);
</script>

<template>
	<Transition name="consent">
		<section v-if="consent.visible" class="consent-notice" aria-labelledby="consent-title">
			<div class="flex items-start justify-between gap-3">
				<h2 id="consent-title" class="text-base font-semibold text-highlighted">
					Cookies &amp; external media
				</h2>
				<UButton
					v-if="consent.decided"
					:icon="icons.close"
					aria-label="Close cookie settings"
					color="neutral"
					variant="ghost"
					size="sm"
					@click="consent.open = false"
				/>
			</div>
			<p class="mt-2 text-sm text-muted">
				We use cookies for sign-in and to remember this choice. Your appearance is saved to
				your account. Allow YouTube to load covers and video previews from Google. Discord
				audio works either way.
			</p>
			<button
				type="button"
				class="consent-details"
				:aria-expanded="details"
				aria-controls="consent-details"
				@click="details = !details"
			>
				{{ details ? "Hide details" : "What is stored?" }}
				<UIcon
					:name="icons.chevronDown"
					class="size-4"
					:class="{ 'rotate-180': details }"
				/>
			</button>
			<dl
				v-if="details"
				id="consent-details"
				class="space-y-3 text-xs leading-relaxed text-muted"
			>
				<div>
					<dt class="font-medium text-highlighted">Necessary cookies</dt>
					<dd>
						Sign-in lasts up to 7 days. Login verification lasts 10 minutes. This choice
						stays on this browser for 6 months.
					</dd>
				</div>
				<div>
					<dt class="font-medium text-highlighted">Your account</dt>
					<dd>
						Name, avatar and appearance are saved with your Discord account, including
						your colours, font and text size.
					</dd>
				</div>
				<div>
					<dt class="font-medium text-highlighted">YouTube / Google</dt>
					<dd>
						Covers and previews send requests to Google. The embedded player may use
						cookies or local storage. You can change your choice here at any time;
						blocking stops further media loading but cannot remove storage already set
						by Google.
					</dd>
				</div>
				<div>
					<dt class="font-medium text-highlighted">Analytics</dt>
					<dd>We do not use analytics or advertising trackers.</dd>
				</div>
			</dl>
			<div class="consent-actions">
				<UButton
					label="Necessary only"
					color="neutral"
					variant="outline"
					@click="consent.choose(false)"
				/>
				<UButton
					label="Allow YouTube"
					color="neutral"
					variant="outline"
					@click="consent.choose(true)"
				/>
			</div>
		</section>
	</Transition>
</template>

<style scoped>
.consent-notice {
	position: fixed;
	z-index: 60;
	right: 1rem;
	bottom: 6.5rem;
	width: min(28rem, calc(100vw - 2rem));
	max-height: calc(100dvh - 9rem);
	overflow-y: auto;
	padding: 1.25rem;
	border-radius: 0.75rem;
	background: var(--ui-bg-elevated);
	box-shadow: 0 12px 40px rgb(0 0 0 / 24%);
}
.consent-details {
	display: inline-flex;
	align-items: center;
	gap: 0.375rem;
	min-height: 2.75rem;
	font-size: 0.75rem;
	color: var(--ui-text);
}
.consent-details > span {
	transition: transform 150ms ease-out;
}
.consent-actions {
	display: flex;
	justify-content: flex-end;
	flex-wrap: wrap;
	gap: 0.5rem;
	margin-top: 0.5rem;
}
.consent-actions :deep(button) {
	min-height: 2.75rem;
}
.consent-enter-active {
	transition: opacity 160ms ease-out;
}
.consent-leave-active {
	transition: opacity 100ms ease-out;
}
.consent-enter-from,
.consent-leave-to {
	opacity: 0;
}
@media (max-width: 640px) {
	.consent-notice {
		bottom: max(1rem, env(safe-area-inset-bottom));
		max-height: calc(100dvh - 6rem);
	}
}
@media (prefers-reduced-motion: reduce) {
	.consent-details > span {
		transition: none;
	}
	.consent-enter-active,
	.consent-leave-active {
		transition-duration: 60ms;
	}
}
</style>
