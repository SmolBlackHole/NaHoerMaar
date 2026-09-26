<!-- SPDX-FileCopyrightText: 2026 SmolBlackHole -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

<script setup lang="ts">
import type { StatisticsPeriod } from "~/core/models/account";

const period = defineModel<StatisticsPeriod>({ required: true });
const items = [
	{ label: "Last 7 days", value: "7d" },
	{ label: "Last 30 days", value: "30d" },
	{ label: "This year", value: "year" },
	{ label: "All time", value: "all" },
];
</script>

<template>
	<USelect
		v-model="period"
		:items="items"
		value-key="value"
		aria-label="Statistics period"
		class="w-40"
	/>
</template>
