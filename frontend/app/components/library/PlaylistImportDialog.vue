<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
import { failureMessage } from "~/core/errors";
import type { Playlist } from "~/core/models/library";

defineProps<{ open: boolean }>();
const emit = defineEmits<{
	"update:open": [open: boolean];
	imported: [playlist: Playlist];
}>();
const core = useNuxtApp().$backendCore;
const { icons } = useTheme();
const toast = useToast();
const sourceUrl = ref("");
const name = ref("");
const pending = ref(false);
const error = ref<string | null>(null);

async function submit() {
	const url = sourceUrl.value.trim();
	if (!url || pending.value) return;
	pending.value = true;
	error.value = null;
	try {
		const imported = await core.client.library.importPlaylist(
			url,
			name.value.trim() || undefined,
		);
		toast.add({
			title: "Playlist imported",
			description: `${imported.entry_count} tracks linked to ${imported.name}.`,
			color: "success",
		});
		sourceUrl.value = "";
		name.value = "";
		emit("imported", imported);
	} catch (failure) {
		error.value = failureMessage(failure);
	} finally {
		pending.value = false;
	}
}

function updateOpen(open: boolean) {
	if (!open && !pending.value) error.value = null;
	emit("update:open", open);
}
</script>

<template>
	<UModal
		:open="open"
		title="Import a playlist"
		description="Link a YouTube playlist. NaHörMaar keeps its order and refreshes it automatically."
		@update:open="updateOpen"
	>
		<template #body>
			<form class="space-y-5" @submit.prevent="submit">
				<UFormField label="YouTube playlist URL" required>
					<UInput
						v-model="sourceUrl"
						type="url"
						autofocus
						:icon="icons.external"
						placeholder="https://www.youtube.com/playlist?list=…"
						class="w-full"
					/>
				</UFormField>
				<UFormField label="Name" hint="Optional">
					<UInput
						v-model="name"
						maxlength="100"
						placeholder="Use the source name"
						class="w-full"
					/>
				</UFormField>
				<p v-if="error" class="text-sm text-warning" role="alert">{{ error }}</p>
				<div class="flex justify-end gap-2">
					<UButton
						label="Cancel"
						color="neutral"
						variant="ghost"
						:disabled="pending"
						@click="updateOpen(false)"
					/>
					<UButton
						type="submit"
						label="Import"
						:icon="icons.upload"
						:loading="pending"
						:disabled="!sourceUrl.trim()"
					/>
				</div>
			</form>
		</template>
	</UModal>
</template>
