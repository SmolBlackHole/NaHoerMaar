<script setup lang="ts">
import { useProfileStore } from "~/stores/profile";
const emit = defineEmits<{ saved: [] }>();
const profile = useProfileStore();
const { icons } = useTheme();
const name = ref(profile.profile?.name ?? "");
const avatar = ref(profile.profile?.avatar ?? profile.randomAvatar());
const error = ref("");
const inputId = useId();
function save() {
	if (profile.save(name.value, avatar.value)) emit("saved");
	else error.value = "Choose a name between 1 and 32 characters.";
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
			:label="profile.profile ? 'Save profile' : 'Enter the player'"
			:trailing-icon="icons.arrowRight"
			size="xl"
			block
			:disabled="!name.trim()"
		/>
	</form>
</template>
