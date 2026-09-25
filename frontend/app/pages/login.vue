<script setup lang="ts">
useSeoMeta({ title: "Sign in | NaHörMaar" });
const session = useNuxtApp().$backendCore.stores.useSessionStore();

watch(
	() => session.status === "authenticated" && session.account?.profile_complete,
	(ready) => {
		if (ready) void navigateTo("/", { replace: true });
	},
	{ immediate: true },
);
</script>

<template><div /></template>
