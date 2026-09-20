<script setup lang="ts">
import { useProfileStore } from "~/stores/profile";
useSeoMeta({ title: "Sign in | NaHörMaar" });
const profile = useProfileStore();
watch(
	() => profile.status === "authenticated" && profile.profileComplete,
	(ready) => {
		if (ready) void navigateTo("/", { replace: true });
	},
	{ immediate: true },
);
</script>

<template><div /></template>
