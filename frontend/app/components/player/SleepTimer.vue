<script setup lang="ts">
const player = useNuxtApp().$backendCore.stores.usePlayerStore();
const { icons } = useTheme();
const now = useNow({ interval: 1000 });
const open = ref(false);
const presets = [15, 30, 45, 60] as const;

const expiresAt = computed(() => {
	const value = player.state?.sleep_timer_expires_at;
	if (!value) return null;
	const timestamp = Date.parse(value);
	return Number.isFinite(timestamp) ? timestamp : null;
});
const remainingSeconds = computed(() =>
	expiresAt.value === null
		? 0
		: Math.max(0, Math.ceil((expiresAt.value - now.value.getTime()) / 1000)),
);
const remainingLabel = computed(() => {
	const seconds = remainingSeconds.value;
	if (seconds < 60) return `${seconds}s`;
	const minutes = Math.ceil(seconds / 60);
	return minutes < 60 ? `${minutes}m` : `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
});
const localTime = computed(() =>
	expiresAt.value === null
		? null
		: new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" }).format(
				expiresAt.value,
			),
);
const pending = computed(
	() => player.isPending("sleep_timer.set") || player.isPending("sleep_timer.cancelled"),
);
</script>

<template>
	<UPopover v-model:open="open" :ui="{ content: 'w-64 max-w-[calc(100vw-2rem)] p-4' }">
		<template #anchor>
			<UButton
				:aria-label="
					expiresAt ? `Sleep timer, ${remainingLabel} remaining` : 'Set sleep timer'
				"
				color="neutral"
				variant="ghost"
				class="min-h-10 justify-center"
				:class="expiresAt ? 'px-2' : 'size-10'"
				:loading="pending"
				:disabled="!player.canControl"
				@click="open = !open"
			>
				<UTooltip
					:text="
						expiresAt ? `Sleep timer: ${remainingLabel} remaining` : 'Set sleep timer'
					"
				>
					<span class="inline-flex items-center justify-center gap-1.5">
						<UIcon :name="icons.clock" class="size-5" />
						<span v-if="expiresAt" class="tabular-nums">{{ remainingLabel }}</span>
					</span>
				</UTooltip>
			</UButton>
		</template>
		<template #content>
			<div class="space-y-4">
				<div>
					<p class="text-sm font-semibold text-highlighted">Sleep timer</p>
					<p class="mt-1 text-xs leading-relaxed text-muted">
						Pause playback and leave voice when the timer ends.
					</p>
				</div>

				<div v-if="expiresAt" class="flex items-end justify-between gap-3">
					<div>
						<p class="text-2xl font-semibold tabular-nums text-highlighted">
							{{ remainingLabel }}
						</p>
						<p class="text-xs text-muted">Until {{ localTime }}</p>
					</div>
					<UButton
						label="Cancel"
						color="neutral"
						variant="outline"
						:loading="player.isPending('sleep_timer.cancelled')"
						:disabled="!player.canControl"
						@click="player.cancelSleepTimer()"
					/>
				</div>

				<div class="grid grid-cols-2 gap-2" aria-label="Sleep timer duration">
					<UButton
						v-for="minutes in presets"
						:key="minutes"
						:label="`${minutes}m`"
						color="neutral"
						variant="soft"
						class="justify-center"
						:disabled="!player.canControl || pending"
						@click="player.setSleepTimer(minutes * 60)"
					/>
				</div>
			</div>
		</template>
	</UPopover>
</template>
