<script setup lang="ts">
import type { LyricsEditProps } from "./types";

const props = defineProps<LyricsEditProps>();

const MAX_ORBIT_WORDS = 36;
const ORBIT_WORDS_BEHIND = 24;

const orbitTokens = computed(() => {
	const firstTokenIndex = Math.max(
		0,
		Math.min(props.activeWordIndex - ORBIT_WORDS_BEHIND, props.tokens.length - MAX_ORBIT_WORDS),
	);

	return props.tokens
		.slice(firstTokenIndex, firstTokenIndex + MAX_ORBIT_WORDS)
		.map((token, visibleIndex) => {
			const index = firstTokenIndex + visibleIndex;
			const relative = index - props.activeWordIndex;
			const distance = Math.abs(relative);
			const phase =
				(distance * 137.508 + props.lineIndex * 19 + (relative < 0 ? 180 : 0)) *
				(Math.PI / 180);
			const lane = distance === 0 ? 0 : (distance - 1) % 3;
			const radiusX = [29, 25, 20][lane] ?? 29;
			const radiusY = [28, 22, 16][lane] ?? 28;
			return {
				...token,
				current: relative === 0,
				past: relative < 0,
				near: distance < 5,
				style: {
					"--word-x": `${relative === 0 ? 0 : Math.cos(phase) * radiusX}%`,
					"--word-y": `${relative === 0 ? 0 : Math.sin(phase) * radiusY}%`,
					"--word-angle": `${relative === 0 ? 0 : Math.sin(index * 1.35) * 7}deg`,
					"--word-scale": `${Math.max(0.67, 1.04 - distance * 0.045)}`,
					"--word-opacity": `${Math.max(0.28, 0.79 - distance * 0.055)}`,
				},
			};
		});
});
const stageStyle = computed(() => {
	const phase = (props.progress ?? 0) / 100;
	return {
		"--camera-rotation": `${((props.activeWordIndex % 7) - 3) * 1.4 + (phase - 0.5) * 4}deg`,
		"--camera-zoom": `${1 + Math.sin(phase * Math.PI) * 0.045 + (props.activeWordIndex % 3) * 0.01}`,
		"--ring-rotation": `${props.activeWordIndex * 23 + phase * 12}deg`,
		"--ring-counter-rotation": `${(props.activeWordIndex * 23 + phase * 12) * -0.65}deg`,
		"--axis-rotation": `${(props.activeWordIndex * 23 + phase * 12) * 0.4}deg`,
	};
});
</script>

<template>
	<div class="orbit-frame" :style="stageStyle">
		<div class="orbit-camera">
			<div class="orbit-rings" aria-hidden="true">
				<span class="orbit-ring orbit-ring-inner" />
				<span class="orbit-ring orbit-ring-middle" />
				<span class="orbit-ring orbit-ring-outer" />
				<span class="orbit-axis" />
			</div>
			<div class="orbit-words" aria-hidden="true">
				<span
					v-for="token in orbitTokens"
					:key="token.id"
					class="orbit-word"
					:class="{
						'is-current': token.current,
						'is-past': token.past,
						'is-near': token.near,
						'is-long': token.text.length > 12,
						'is-very-long': token.text.length > 19,
					}"
					:style="token.style"
					>{{ token.text }}</span
				>
			</div>
		</div>
		<p class="sr-only">{{ tokens.map((token) => token.text).join(" ") }}</p>
	</div>
</template>

<style scoped>
.orbit-frame {
	position: relative;
	width: 100%;
	min-height: 30rem;
	overflow: hidden;
	isolation: isolate;
}
.orbit-camera {
	position: absolute;
	inset: 0;
	transform: rotate(var(--camera-rotation)) scale(var(--camera-zoom));
	transition: transform 220ms ease-out;
}
.orbit-rings,
.orbit-words {
	position: absolute;
	inset: 0;
}
.orbit-ring,
.orbit-axis {
	position: absolute;
	left: 50%;
	top: 50%;
	border-radius: 50%;
	translate: -50% -50%;
	rotate: var(--ring-rotation);
	will-change: rotate;
}
.orbit-ring {
	border: 1px solid color-mix(in srgb, var(--player-foreground) 18%, transparent);
}
.orbit-ring-inner {
	width: 40%;
	height: 32%;
	border-color: color-mix(in srgb, var(--ui-primary) 42%, transparent);
	animation: orbit-inner 18s linear infinite;
}
.orbit-ring-middle {
	width: 50%;
	height: 44%;
	border-color: color-mix(in srgb, var(--ui-primary) 24%, transparent);
	rotate: var(--ring-counter-rotation);
	animation: orbit-middle 27s linear infinite;
}
.orbit-ring-outer {
	width: 58%;
	height: 56%;
	border-color: color-mix(in srgb, var(--player-foreground) 14%, transparent);
	animation: orbit-outer 39s linear infinite;
}
.orbit-axis {
	width: 94%;
	height: 1px;
	border-radius: 0;
	background: linear-gradient(
		to right,
		transparent,
		color-mix(in srgb, var(--ui-primary) 28%, transparent) 48%,
		color-mix(in srgb, var(--ui-primary) 28%, transparent) 52%,
		transparent
	);
	rotate: var(--axis-rotation);
	animation: orbit-axis-drift 9s ease-in-out infinite alternate;
}
.orbit-word {
	position: absolute;
	left: calc(50% + var(--word-x));
	top: calc(50% + var(--word-y));
	max-width: 28%;
	font-size: clamp(0.9rem, 2vw, 1.65rem);
	font-weight: 680;
	line-height: 0.98;
	letter-spacing: -0.035em;
	text-align: center;
	text-transform: uppercase;
	text-wrap: balance;
	overflow-wrap: anywhere;
	color: color-mix(in srgb, var(--player-foreground) 64%, var(--ui-primary));
	opacity: var(--word-opacity);
	text-shadow: 0 0.2rem 1rem color-mix(in srgb, #000 48%, transparent);
	transform: translate(-50%, -50%) rotate(var(--word-angle)) scale(var(--word-scale));
	transition:
		left 460ms cubic-bezier(0.16, 1, 0.3, 1),
		top 460ms cubic-bezier(0.16, 1, 0.3, 1),
		transform 460ms cubic-bezier(0.16, 1, 0.3, 1),
		font-size 460ms cubic-bezier(0.16, 1, 0.3, 1),
		opacity 320ms ease-out,
		color 320ms ease-out;
}
.orbit-word.is-past {
	color: color-mix(in srgb, var(--player-foreground) 83%, var(--ui-primary));
}
.orbit-word.is-near {
	font-weight: 760;
}
.orbit-word.is-current {
	z-index: 2;
	max-width: 82%;
	font-size: clamp(3.25rem, 8vw, 7rem);
	font-weight: 890;
	letter-spacing: -0.04em;
	color: var(--player-foreground);
	opacity: 1;
	filter: drop-shadow(0 0.45rem 1.5rem color-mix(in srgb, #000 58%, transparent));
}
.orbit-word.is-current.is-long {
	font-size: clamp(2.5rem, 6vw, 5rem);
}
.orbit-word.is-current.is-very-long {
	font-size: clamp(2rem, 4.5vw, 3.75rem);
}
.orbit-word.is-current::after {
	position: absolute;
	left: 18%;
	right: 18%;
	bottom: -0.2em;
	height: 2px;
	content: "";
	background: color-mix(in srgb, var(--ui-primary) 76%, var(--player-foreground));
}
@keyframes orbit-inner {
	from {
		rotate: var(--ring-rotation);
	}
	to {
		rotate: calc(var(--ring-rotation) + 360deg);
	}
}
@keyframes orbit-middle {
	from {
		rotate: var(--ring-counter-rotation);
	}
	to {
		rotate: calc(var(--ring-counter-rotation) - 360deg);
	}
}
@keyframes orbit-outer {
	from {
		rotate: calc(var(--ring-rotation) + 42deg);
	}
	to {
		rotate: calc(var(--ring-rotation) - 318deg);
	}
}
@keyframes orbit-axis-drift {
	from {
		rotate: calc(var(--axis-rotation) - 10deg);
	}
	to {
		rotate: calc(var(--axis-rotation) + 10deg);
	}
}
@container workspace (max-width: 600px) {
	.orbit-frame {
		min-height: 23rem;
	}
	.orbit-word {
		max-width: 30%;
		font-size: clamp(0.72rem, 3vw, 1.1rem);
	}
	.orbit-word.is-current {
		max-width: 80%;
		font-size: clamp(2.2rem, 11vw, 4.5rem);
	}
	.orbit-word.is-current.is-long {
		font-size: clamp(1.8rem, 8vw, 3.25rem);
	}
	.orbit-word.is-current.is-very-long {
		font-size: clamp(1.5rem, 6vw, 2.5rem);
	}
}
@media (prefers-reduced-motion: reduce) {
	.orbit-camera,
	.orbit-ring,
	.orbit-axis,
	.orbit-word {
		transition: none;
		animation: none;
	}
	.orbit-ring-middle {
		rotate: var(--ring-counter-rotation);
	}
	.orbit-axis {
		rotate: var(--axis-rotation);
	}
}
</style>
