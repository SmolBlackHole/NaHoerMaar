<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
import { failureMessage } from "~/core/errors";
import type { Playlist } from "~/core/models/library";

const props = defineProps<{ playlist: Playlist }>();
const emit = defineEmits<{ changed: [playlist: Playlist] }>();
const core = useNuxtApp().$backendCore;
const { icons } = useTheme();
const toast = useToast();
const pending = ref<"sync" | "detach" | null>(null);
const detachOpen = ref(false);
const error = ref<string | null>(null);

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
	<div v-if="playlist.source" class="source-card">
		<div class="min-w-0 flex-1">
			<div class="flex flex-wrap items-center gap-2">
				<p class="text-sm font-semibold text-highlighted">Linked playlist</p>
				<UBadge color="neutral" variant="soft">{{ playlist.source.provider_key }}</UBadge>
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
			<a
				:href="playlist.source.canonical_url"
				target="_blank"
				rel="noreferrer"
				class="mt-2 inline-flex max-w-full items-center gap-1.5 text-sm text-muted hover:text-highlighted"
			>
				<span class="truncate">{{ playlist.source.canonical_url }}</span>
				<UIcon :name="icons.external" class="size-4 shrink-0" />
			</a>
			<p class="mt-2 text-xs text-muted">
				Last successful refresh {{ formatted(playlist.source.last_successful_sync_at) }}
			</p>
			<p class="mt-1 text-xs text-muted">
				Track order follows the linked source until you detach it.
			</p>
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
		<div v-if="playlist.access === 'owner'" class="flex shrink-0 flex-wrap gap-2">
			<UButton
				label="Sync now"
				:icon="icons.reload"
				color="neutral"
				variant="soft"
				:loading="pending === 'sync'"
				:disabled="Boolean(pending)"
				@click="synchronize"
			/>
			<UButton
				label="Detach"
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
	justify-content: space-between;
	gap: 1rem;
	padding: 1rem;
	border: 1px solid var(--ui-border);
	border-radius: 0.875rem;
	background: color-mix(in srgb, var(--ui-bg-elevated) 58%, transparent);
}
@container workspace (max-width: 640px) {
	.source-card {
		flex-direction: column;
	}
}
</style>
