<script setup lang="ts">
import type { Account, ProfileUpdate } from "~/core/models/account";

const props = withDefaults(
	defineProps<{
		profile: Account["profile"] | null;
		complete?: boolean;
		busy?: boolean;
		error?: string | null;
	}>(),
	{ complete: false, busy: false, error: null },
);
const emit = defineEmits<{ save: [value: ProfileUpdate] }>();
const { icons } = useTheme();
const name = ref("");
const validationError = ref("");
const inputId = useId();
const changed = computed(() => name.value.trim() !== props.profile?.display_name);
watch(
	() => props.profile,
	(value) => {
		if (!value) return;
		name.value = value.display_name ?? "";
		validationError.value = "";
	},
	{ immediate: true },
);
function save() {
	if (!name.value.trim() || name.value.trim().length > 32) {
		validationError.value = "Choose a name between 1 and 32 characters.";
		return;
	}
	emit("save", { display_name: name.value.trim() });
}
</script>

<template>
	<form class="space-y-6" @submit.prevent="save">
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
			:trailing-icon="complete && !changed ? icons.check : icons.arrowRight"
			:color="complete && !changed ? 'neutral' : 'primary'"
			:variant="complete && !changed ? 'soft' : 'solid'"
			size="xl"
			block
			:disabled="!name.trim() || (complete && !changed)"
			:loading="busy"
		/>
	</form>
</template>
