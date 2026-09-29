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
const loadingPeople = ref(false);
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
const discoveryItems = computed(() =>
	availableResults.value.map((contributor) => ({
		id: contributor.user_id,
		displayName: contributor.display_name,
		username: contributor.username,
		avatarUrl: contributor.avatar_url,
	})),
);
const contributorById = computed(
	() => new Map(availableResults.value.map((contributor) => [contributor.user_id, contributor])),
);
const visibilityHint = computed(() => {
	if (props.playlist.visibility === "public")
		return "Everyone signed in to NaHörMaar can find and play it. Only invited people can edit.";
	if (props.playlist.visibility === "collaborators")
		return "Only you and invited people can find and edit it.";
	return "Only you can find or edit it.";
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

async function loadPeople() {
	if (loadingPeople.value) return;
	loadingPeople.value = true;
	error.value = null;
	try {
		results.value = (await core.client.library.contributors(undefined, 100)).items;
	} catch (failure) {
		error.value = failureMessage(failure);
	} finally {
		loadingPeople.value = false;
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

function invite(userId: string) {
	const contributor = contributorById.value.get(userId);
	if (contributor) void add(contributor);
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

watch(
	() => props.open,
	(open) => {
		if (open) void loadPeople();
	},
	{ immediate: true },
);
</script>

<template>
	<UModal
		:open="open"
		title="Playlist access"
		description="Choose who can find this playlist and invite people to edit it with you."
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
				<p class="-mt-4 text-xs text-muted">{{ visibilityHint }}</p>

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

				<div>
					<div class="mb-3 flex items-center justify-between gap-3">
						<h3 class="text-sm font-medium text-highlighted">Invite someone</h3>
						<span class="text-xs text-muted"
							>{{ availableResults.length }} available</span
						>
					</div>
					<SharedUserDiscovery
						v-model:query="query"
						:items="discoveryItems"
						:loading="loadingPeople"
						search-placeholder="Search people"
						empty-title="No people available"
						empty-description="Everyone else is already invited."
					>
						<template #action="{ item }">
							<UButton
								label="Invite"
								:icon="icons.userPlus"
								color="neutral"
								variant="soft"
								size="sm"
								:loading="pendingUserId === item.id"
								:disabled="Boolean(pendingUserId)"
								@click="invite(item.id)"
							/>
						</template>
					</SharedUserDiscovery>
				</div>

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
