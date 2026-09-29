<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
import type { PlaylistTrack } from "~/core/models/library";

withDefaults(defineProps<{ track: PlaylistTrack; title: string; compact?: boolean }>(), {
	compact: true,
});
</script>

<template>
	<LibraryPlaylistPicker
		:tracks="[track]"
		:label="`Add ${title} to playlist`"
		:compact="compact"
	/>
</template>
