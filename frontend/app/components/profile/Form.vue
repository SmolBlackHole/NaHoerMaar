<script setup lang="ts">
import type { ProfileUpdate, UserProfile } from "~/core/models/account";
import avatars from "~/config/avatars.json";

function randomAvatar(previous?: string): string {
	const choices = avatars.filter((id) => id !== previous);
	return choices[Math.floor(Math.random() * choices.length)]!;
}

const props = withDefaults(
	defineProps<{
		profile: UserProfile["profile"] | null;
		complete?: boolean;
		busy?: boolean;
		error?: string | null;
	}>(),
	{ complete: false, busy: false, error: null },
);
const emit = defineEmits<{ save: [value: ProfileUpdate] }>();
const { icons } = useTheme();
const name = ref("");
const avatar = ref(randomAvatar());
const validationError = ref("");
const inputId = useId();
const changed = computed(
	() =>
		name.value.trim() !== props.profile?.display_name ||
		avatar.value !== props.profile?.pixabot,
);
watch(
	() => props.profile,
	(value) => {
		if (!value) return;
		name.value = value.display_name ?? "";
		avatar.value = value.pixabot ?? randomAvatar(avatar.value);
		validationError.value = "";
	},
	{ immediate: true },
);
function save() {
	if (!name.value.trim() || name.value.trim().length > 32) {
		validationError.value = "Choose a name between 1 and 32 characters.";
		return;
	}
	emit("save", { display_name: name.value.trim(), pixabot: avatar.value });
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
					type="button"
					label="Try another"
					:icon="icons.reload"
					variant="outline"
					color="neutral"
					@click="avatar = randomAvatar(avatar)"
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
				:aria-invalid="!!validationError || !!props.error"
				:aria-describedby="validationError || props.error ? `${inputId}-error` : undefined"
				@update:model-value="validationError = ''"
			/>
			<p
				v-if="validationError || props.error"
				:id="`${inputId}-error`"
				class="text-error text-sm"
				role="alert"
			>
				{{ validationError || props.error }}
			</p>
		</div>
		<UButton
			type="submit"
			:label="
				complete ? (changed ? 'Save profile' : 'Profile up to date') : 'Enter the player'
			"
			:trailing-icon="complete && !changed ? 'i-lucide-check' : icons.arrowRight"
			:color="complete && !changed ? 'neutral' : 'primary'"
			:variant="complete && !changed ? 'soft' : 'solid'"
			size="xl"
			block
			:disabled="!name.trim() || (complete && !changed)"
			:loading="busy"
		/>
	</form>
</template>
