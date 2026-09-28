<script setup lang="ts">
import type { LyricsEditProps } from "./types";

const props = defineProps<LyricsEditProps>();

function spiralPoint(position: number) {
	const turn = 2.7 * (Math.sqrt(position + 1) - 1);
	const angle = -Math.PI / 2 + turn;
	const radius = 86 + turn * 42;
	return {
		x: Math.cos(angle) * radius,
		y: Math.sin(angle) * radius,
	};
}

function spiralPath(end: number) {
	if (end < 1) return "";
	const segments = Math.ceil(end * 12);
	return Array.from({ length: segments + 1 }, (_, step) => {
		const point = spiralPoint((step / segments) * end);
		return `${step === 0 ? "M" : "L"}${point.x.toFixed(1)} ${point.y.toFixed(1)}`;
	}).join(" ");
}

const fullTrace = computed(() => spiralPath(props.tokens.length - 1));
const playedTrace = computed(() =>
	spiralPath(Math.min(props.activeWordIndex, props.tokens.length - 1)),
);

const layout = computed(() =>
	props.tokens.map((token, index) => {
		const distance = index - props.activeWordIndex;
		const magnitude = Math.abs(distance);
		const current = distance === 0;
		const point = spiralPoint(index);
		const angles = [0, 90, -22, 0, -90, 24];
		const angle = current ? 0 : angles[index % angles.length];
		return {
			token,
			current,
			heard: distance < 0,
			style: {
				"--word-x": `${point.x}px`,
				"--word-y": `${point.y}px`,
				"--word-angle": `${angle}deg`,
				"--word-scale": `${current ? 1.52 : magnitude === 1 ? 0.98 : 0.84}`,
				"--word-opacity": `${current ? 1 : Math.max(distance < 0 ? 0.3 : 0.38, 0.82 - magnitude * 0.055)}`,
				"--word-blur": `${distance < -3 ? Math.min(1.5, (magnitude - 3) * 0.18) : 0}px`,
			},
		};
	}),
);

const cameraStyle = computed(() => {
	const current = spiralPoint(
		Math.min(props.activeWordIndex, Math.max(0, props.tokens.length - 1)),
	);
	const previous = spiralPoint(Math.max(0, props.activeWordIndex - 1));
	const next = spiralPoint(
		Math.min(props.activeWordIndex + 1, Math.max(0, props.tokens.length - 1)),
	);
	const progress = Math.min(1, Math.max(0, (props.progress ?? 0) / 100));
	const zooms = [1.02, 1.22, 0.82, 1.38, 0.94, 1.3, 0.78];
	const zoom = zooms[props.activeWordIndex % zooms.length] ?? 1;
	return {
		"--camera-x": `${current.x * 0.68 + previous.x * 0.18 + next.x * 0.14 + Math.sin(progress * Math.PI * 2) * 14}px`,
		"--camera-y": `${current.y * 0.68 + previous.y * 0.18 + next.y * 0.14 + Math.cos(progress * Math.PI * 2) * 10}px`,
		"--camera-zoom": `${zoom + Math.sin(progress * Math.PI) * 0.045}`,
		"--focus-x": `${current.x}px`,
		"--focus-y": `${current.y}px`,
	};
});
</script>

<template>
	<div class="spiral-frame" :style="cameraStyle">
		<div class="spiral-camera" aria-hidden="true">
			<svg class="spiral-trace" width="1" height="1" aria-hidden="true">
				<path class="spiral-trace-all" :d="fullTrace" />
				<path class="spiral-trace-played" :d="playedTrace" />
			</svg>
			<span class="spiral-focus" />
			<span
				v-for="item in layout"
				:key="item.token.id"
				class="spiral-word"
				:class="{ 'is-current': item.current, 'is-heard': item.heard }"
				:style="item.style"
				>{{ item.token.text }}</span
			>
		</div>
		<p class="sr-only">{{ tokens.map((token) => token.text).join(" ") }}</p>
	</div>
</template>

<style scoped>
.spiral-frame {
	position: relative;
	width: 100%;
	min-height: 30rem;
	overflow: hidden;
	isolation: isolate;
	--mobile-zoom: 1;
}
.spiral-camera {
	position: absolute;
	left: 50%;
	top: 50%;
	width: 1px;
	height: 1px;
	transform: scale(calc(var(--camera-zoom) * var(--mobile-zoom)))
		translate(calc(var(--camera-x) * -1), calc(var(--camera-y) * -1));
	transform-origin: 0 0;
	transition: transform 620ms cubic-bezier(0.16, 1, 0.3, 1);
}
.spiral-trace {
	position: absolute;
	left: 0;
	top: 0;
	overflow: visible;
	fill: none;
	stroke-linecap: round;
	stroke-linejoin: round;
	pointer-events: none;
}
.spiral-trace-all {
	stroke: color-mix(in srgb, var(--ui-primary) 28%, transparent);
	stroke-width: 1.5;
}
.spiral-trace-played {
	stroke: color-mix(in srgb, var(--ui-primary) 64%, transparent);
	stroke-width: 2.5;
}
.spiral-focus {
	position: absolute;
	left: var(--focus-x);
	top: var(--focus-y);
	width: 13rem;
	aspect-ratio: 1;
	border-radius: 50%;
	background: radial-gradient(
		circle,
		color-mix(in srgb, var(--ui-primary) 24%, transparent),
		transparent 68%
	);
	transform: translate(-50%, -50%);
	pointer-events: none;
}
.spiral-word {
	position: absolute;
	left: var(--word-x);
	top: var(--word-y);
	max-width: min(16ch, 68vw);
	font-size: clamp(1.25rem, 3.2vw, 2.5rem);
	font-weight: 720;
	line-height: 0.95;
	letter-spacing: -0.03em;
	text-align: center;
	text-transform: uppercase;
	overflow-wrap: anywhere;
	color: color-mix(in srgb, var(--player-foreground) 76%, var(--ui-primary) 24%);
	opacity: var(--word-opacity);
	filter: blur(var(--word-blur));
	transform: translate(-50%, -50%) rotate(var(--word-angle)) scale(var(--word-scale));
	transition:
		opacity 280ms ease-out,
		filter 420ms cubic-bezier(0.16, 1, 0.3, 1),
		transform 520ms cubic-bezier(0.16, 1, 0.3, 1),
		color 280ms ease-out;
}
.spiral-word.is-heard {
	color: color-mix(in srgb, var(--player-foreground) 56%, var(--ui-primary) 44%);
}
.spiral-word.is-current {
	z-index: 2;
	font-weight: 850;
	color: color-mix(in srgb, var(--ui-primary) 72%, var(--player-foreground) 28%);
	text-shadow: 0 0.35rem 1.6rem color-mix(in srgb, #000 52%, transparent);
}
@media (max-width: 40rem) {
	.spiral-frame {
		min-height: 26rem;
		--mobile-zoom: 0.92;
	}
}
@media (prefers-reduced-motion: reduce) {
	.spiral-camera,
	.spiral-word {
		transition: none;
	}
}
</style>
