<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
import { failureMessage } from "~/core/errors";
import type { Playlist } from "~/core/models/library";

defineOptions({ inheritAttrs: false });

const props = defineProps<{ playlist: Playlist }>();
const emit = defineEmits<{ changed: [playlist: Playlist] }>();
const core = useNuxtApp().$backendCore;
const { icons } = useTheme();
const toast = useToast();
const pending = ref<"sync" | "detach" | null>(null);
const detachOpen = ref(false);
const error = ref<string | null>(null);
const providerName = computed(() =>
	props.playlist.source?.provider_key === "youtube" ? "YouTube" : "the source",
);

function formatted(value: string) {
	return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(
		new Date(value),
	);
}

function errorLabel(value: string) {
	return value.replaceAll("_", " ");
}

async function synchronize() {
	if (pending.value || !props.playlist.source) return;
	pending.value = "sync";
	error.value = null;
	try {
		const updated = await core.client.library.syncPlaylist(props.playlist.playlist_id);
		emit("changed", updated);
		if (updated.source?.last_error_code) {
			toast.add({
				title: "Playlist could not be refreshed",
				description: "The last working tracks were kept.",
				color: "warning",
			});
		} else {
			toast.add({ title: "Playlist refreshed", color: "success" });
		}
	} catch (failure) {
		error.value = failureMessage(failure);
	} finally {
		pending.value = null;
	}
}

async function detach() {
	if (pending.value || !props.playlist.source) return;
	pending.value = "detach";
	error.value = null;
	try {
		const updated = await core.client.library.detachPlaylistSource(
			props.playlist.playlist_id,
			props.playlist.revision,
		);
		detachOpen.value = false;
		emit("changed", updated);
		toast.add({
			title: "Playlist detached",
			description: "Its current tracks can now be edited manually.",
			color: "success",
		});
	} catch (failure) {
		error.value = failureMessage(failure);
	} finally {
		pending.value = null;
	}
}
</script>

<template>
	<div v-if="playlist.source" v-bind="$attrs" class="source-card">
		<UIcon :name="icons.reload" class="mt-0.5 size-4 shrink-0 text-primary" />
		<div class="source-details min-w-0 flex-1">
			<div class="flex flex-wrap items-center gap-x-2 gap-y-1">
				<p class="text-sm font-medium text-highlighted">Synced from {{ providerName }}</p>
				<UBadge v-if="playlist.source.truncated" color="warning" variant="soft">
					Import limit reached
				</UBadge>
				<UBadge
					v-if="playlist.source.unavailable_entry_count"
					color="warning"
					variant="soft"
				>
					{{ playlist.source.unavailable_entry_count }} unavailable
				</UBadge>
			</div>
			<div class="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
				<span>Last refreshed {{ formatted(playlist.source.last_successful_sync_at) }}</span>
				<span aria-hidden="true">·</span>
				<span>Source order</span>
				<span aria-hidden="true">·</span>
				<a
					:href="playlist.source.canonical_url"
					target="_blank"
					rel="noreferrer"
					class="inline-flex items-center gap-1 font-medium hover:text-highlighted hover:underline"
				>
					Open source
					<UIcon :name="icons.external" class="size-3.5" />
				</a>
			</div>
			<p
				v-if="playlist.source.last_error_code"
				class="mt-2 text-sm text-warning"
				role="status"
			>
				The latest refresh failed ({{ errorLabel(playlist.source.last_error_code) }}).
				Cached tracks are still available.
			</p>
			<p v-if="error" class="mt-2 text-sm text-warning" role="alert">{{ error }}</p>
		</div>
		<div
			v-if="playlist.access === 'owner'"
			class="source-actions flex shrink-0 flex-wrap gap-1"
		>
			<UButton
				label="Refresh"
				:icon="icons.reload"
				color="neutral"
				variant="soft"
				:loading="pending === 'sync'"
				:disabled="Boolean(pending)"
				@click="synchronize"
			/>
			<UButton
				label="Stop sync"
				:icon="icons.close"
				color="neutral"
				variant="ghost"
				:disabled="Boolean(pending)"
				@click="detachOpen = true"
			/>
		</div>
	</div>

	<UModal
		:open="detachOpen"
		title="Stop syncing this playlist?"
		description="The current tracks stay in place and become editable. Future source changes will no longer appear here."
		@update:open="detachOpen = $event"
	>
		<template #body>
			<div class="flex justify-end gap-2">
				<UButton
					label="Cancel"
					color="neutral"
					variant="ghost"
					:disabled="Boolean(pending)"
					@click="detachOpen = false"
				/>
				<UButton
					label="Detach playlist"
					:icon="icons.close"
					color="warning"
					:loading="pending === 'detach'"
					@click="detach"
				/>
			</div>
		</template>
	</UModal>
</template>

<style scoped>
.source-card {
	display: flex;
	align-items: flex-start;
	gap: 0.625rem;
	padding-block: 0.875rem;
	border-block: 1px solid color-mix(in srgb, var(--ui-border) 72%, transparent);
}
@container workspace (max-width: 640px) {
	.source-card {
		flex-wrap: wrap;
	}
	.source-details {
		min-width: calc(100% - 2.25rem);
	}
	.source-actions {
		margin-left: 1.625rem;
	}
}
</style>
