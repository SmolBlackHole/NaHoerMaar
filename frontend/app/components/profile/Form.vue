<script setup lang="ts">
import { useProfileStore } from "~/stores/profile";
const emit = defineEmits<{ saved: [] }>();
const profile = useProfileStore();
const { icons } = useTheme();
const name = ref("");
const avatar = ref(profile.randomAvatar());
const error = ref("");
const inputId = useId();
const changed = computed(
	() => name.value.trim() !== profile.profile?.name || avatar.value !== profile.profile?.avatar,
);
watch(
	() => profile.profile?.id,
	() => {
		const suggestion = !profile.profileComplete ? profile.suggestion : null;
		name.value = suggestion?.name ?? profile.profile?.name ?? "";
		avatar.value = suggestion?.avatar ?? profile.profile?.avatar ?? profile.randomAvatar();
		error.value = "";
	},
	{ immediate: true },
);
async function save() {
	if (!name.value.trim() || name.value.trim().length > 32) {
		error.value = "Choose a name between 1 and 32 characters.";
		return;
	}
	if (await profile.save(name.value.trim(), avatar.value)) emit("saved");
	else error.value = profile.error;
}
</script>

<template>
	<form class="space-y-6" @submit.prevent="save">
		<div class="flex items-center gap-5">
			<img
				:src="`/avatars/${avatar}.png`"
				alt="Your Pixabot avatar"
				width="96"
				height="96"
				class="size-24 rounded-2xl bg-elevated [image-rendering:pixelated]"
			/>
			<div class="space-y-2">
				<p class="text-sm font-medium">Your little bot</p>
				<UButton
					label="Try another"
					:icon="icons.reload"
					variant="outline"
					color="neutral"
					@click="avatar = profile.randomAvatar(avatar)"
				/>
			</div>
		</div>
		<div class="space-y-2">
			<label :for="inputId" class="block text-sm font-medium">What should we call you?</label>
			<UInput
				:id="inputId"
				v-model="name"
				autocomplete="nickname"
				placeholder="Your name"
				:maxlength="32"
				size="xl"
				class="w-full"
				:aria-invalid="!!error"
				:aria-describedby="error ? `${inputId}-error` : undefined"
				@update:model-value="error = ''"
			/>
			<p v-if="error" :id="`${inputId}-error`" class="text-error text-sm" role="alert">
				{{ error }}
			</p>
		</div>
		<UButton
			type="submit"
			:label="
				profile.profileComplete
					? changed
						? 'Save profile'
						: 'Profile up to date'
					: 'Enter the player'
			"
			:trailing-icon="
				profile.profileComplete && !changed ? 'i-lucide-check' : icons.arrowRight
			"
			:color="profile.profileComplete && !changed ? 'neutral' : 'primary'"
			:variant="profile.profileComplete && !changed ? 'soft' : 'solid'"
			size="xl"
			block
			:disabled="!name.trim() || (profile.profileComplete && !changed)"
			:loading="profile.busy"
		/>
	</form>
</template>
