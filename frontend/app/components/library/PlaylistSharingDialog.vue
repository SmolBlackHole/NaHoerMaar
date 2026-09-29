<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
import { failureMessage } from "~/core/errors";
import type { LibraryContributor, Playlist, PlaylistVisibility } from "~/core/models/library";

const props = defineProps<{ open: boolean; playlist: Playlist }>();
const emit = defineEmits<{
	"update:open": [open: boolean];
	changed: [playlist: Playlist];
}>();
const core = useNuxtApp().$backendCore;
const { icons } = useTheme();
const toast = useToast();
const query = ref("");
const results = ref<LibraryContributor[]>([]);
const searching = ref(false);
const pendingUserId = ref<string | null>(null);
const visibilityPending = ref(false);
const error = ref<string | null>(null);
const visibilityOptions: { label: string; value: PlaylistVisibility }[] = [
	{ label: "Private", value: "private" },
	{ label: "Collaborators only", value: "collaborators" },
	{ label: "Public", value: "public" },
];

const availableResults = computed(() => {
	const collaborators = new Set(props.playlist.collaborators.map(({ user_id }) => user_id));
	return results.value.filter(({ user_id }) => !collaborators.has(user_id));
});

async function updateVisibility(value: string | number | boolean | undefined) {
	if (typeof value !== "string" || value === props.playlist.visibility || visibilityPending.value)
		return;
	visibilityPending.value = true;
	error.value = null;
	try {
		const updated = await core.client.library.updatePlaylist(
			props.playlist.playlist_id,
			{ visibility: value as PlaylistVisibility },
			props.playlist.revision,
		);
		emit("changed", updated);
		toast.add({ title: "Playlist visibility updated", color: "success" });
	} catch (failure) {
		error.value = failureMessage(failure);
	} finally {
		visibilityPending.value = false;
	}
}

async function search() {
	const value = query.value.trim();
	if (!value || searching.value) return;
	searching.value = true;
	error.value = null;
	try {
		results.value = (await core.client.library.contributors(value)).items;
	} catch (failure) {
		error.value = failureMessage(failure);
	} finally {
		searching.value = false;
	}
}

async function add(contributor: LibraryContributor) {
	if (pendingUserId.value) return;
	pendingUserId.value = contributor.user_id;
	error.value = null;
	try {
		const updated = await core.client.library.addPlaylistCollaborator(
			props.playlist.playlist_id,
			contributor.user_id,
			props.playlist.revision,
		);
		emit("changed", updated);
		results.value = results.value.filter(({ user_id }) => user_id !== contributor.user_id);
		toast.add({
			title: "Collaborator added",
			description: contributor.display_name,
			color: "success",
		});
	} catch (failure) {
		error.value = failureMessage(failure);
	} finally {
		pendingUserId.value = null;
	}
}

async function remove(contributor: LibraryContributor) {
	if (pendingUserId.value) return;
	pendingUserId.value = contributor.user_id;
	error.value = null;
	try {
		const updated = await core.client.library.removePlaylistCollaborator(
			props.playlist.playlist_id,
			contributor.user_id,
			props.playlist.revision,
		);
		emit("changed", updated);
		toast.add({
			title: "Collaborator removed",
			description: contributor.display_name,
			color: "success",
		});
	} catch (failure) {
		error.value = failureMessage(failure);
	} finally {
		pendingUserId.value = null;
	}
}

function updateOpen(open: boolean) {
	if (!open) {
		query.value = "";
		results.value = [];
		error.value = null;
	}
	emit("update:open", open);
}
</script>

<template>
	<UModal
		:open="open"
		title="Share playlist"
		description="Choose who can find this playlist and who can edit its internal tracks."
		@update:open="updateOpen"
	>
		<template #body>
			<div class="space-y-6">
				<UFormField label="Visibility">
					<USelect
						:model-value="playlist.visibility"
						:items="visibilityOptions"
						value-key="value"
						label-key="label"
						class="w-full"
						:disabled="visibilityPending"
						@update:model-value="updateVisibility"
					/>
				</UFormField>

				<div>
					<div class="flex items-center justify-between gap-3">
						<h3 class="text-sm font-medium text-highlighted">Collaborators</h3>
						<span class="text-xs text-muted">{{ playlist.collaborators.length }}</span>
					</div>
					<ul v-if="playlist.collaborators.length" class="mt-3 space-y-2">
						<li
							v-for="contributor in playlist.collaborators"
							:key="contributor.user_id"
							class="flex items-center gap-3 rounded-xl bg-elevated px-3 py-2"
						>
							<UAvatar
								:src="contributor.avatar_url ?? undefined"
								:alt="contributor.display_name"
								size="sm"
							/>
							<span
								class="min-w-0 flex-1 truncate text-sm font-medium text-highlighted"
								>{{ contributor.display_name }}</span
							>
							<UButton
								:icon="icons.userMinus"
								:aria-label="`Remove ${contributor.display_name}`"
								color="neutral"
								variant="ghost"
								:loading="pendingUserId === contributor.user_id"
								:disabled="Boolean(pendingUserId)"
								@click="remove(contributor)"
							/>
						</li>
					</ul>
					<p v-else class="mt-3 text-sm text-muted">Only you can edit this playlist.</p>
				</div>

				<form class="space-y-3" @submit.prevent="search">
					<UFormField label="Add someone">
						<div class="flex gap-2">
							<UInput
								v-model="query"
								:icon="icons.search"
								placeholder="Search by Discord name"
								class="min-w-0 flex-1"
							/>
							<UButton
								type="submit"
								label="Search"
								:loading="searching"
								:disabled="!query.trim()"
							/>
						</div>
					</UFormField>
					<ul v-if="availableResults.length" class="space-y-2">
						<li
							v-for="contributor in availableResults"
							:key="contributor.user_id"
							class="flex items-center gap-3 rounded-xl border border-default px-3 py-2"
						>
							<UAvatar
								:src="contributor.avatar_url ?? undefined"
								:alt="contributor.display_name"
								size="sm"
							/>
							<span
								class="min-w-0 flex-1 truncate text-sm font-medium text-highlighted"
								>{{ contributor.display_name }}</span
							>
							<UButton
								label="Add"
								:icon="icons.userPlus"
								color="neutral"
								variant="soft"
								:loading="pendingUserId === contributor.user_id"
								:disabled="Boolean(pendingUserId)"
								@click="add(contributor)"
							/>
						</li>
					</ul>
					<p v-else-if="query.trim() && !searching" class="text-sm text-muted">
						No new people found.
					</p>
				</form>

				<p v-if="error" class="text-sm text-warning" role="alert">{{ error }}</p>
				<div class="flex justify-end">
					<UButton
						label="Done"
						color="neutral"
						variant="soft"
						@click="updateOpen(false)"
					/>
				</div>
			</div>
		</template>
	</UModal>
</template>
