<script setup lang="ts">
import type { LyricsEditProps } from "./types";

const props = defineProps<LyricsEditProps>();

function tokenSeed(id: string) {
	let seed = 0;
	for (const character of id) seed = (seed * 31 + character.charCodeAt(0)) >>> 0;
	return seed;
}

const currentToken = computed(() => props.tokens[props.activeWordIndex] ?? null);
const field = computed(() =>
	props.tokens.map((token, index) => {
		const relative = index - props.activeWordIndex;
		const distance = Math.abs(relative);
		const seed = tokenSeed(token.id);
		return {
			token,
			current: relative === 0,
			past: relative < 0,
			near: distance <= 3,
			style: {
				"--field-x": `${8 + (seed % 84)}%`,
				"--field-y": `${8 + ((seed * 17) % 80)}%`,
				"--field-angle": `${((seed % 19) - 9) * (index % 2 === 0 ? 1 : -1)}deg`,
				"--field-scale": `${Math.max(0.58, 1.04 - distance * 0.035)}`,
				"--field-opacity": `${Math.max(0.1, relative < 0 ? 0.48 - distance * 0.035 : 0.24 - distance * 0.012)}`,
				"--field-blur": `${Math.min(0.22, Math.max(0, distance - 2) * 0.018)}rem`,
			},
		};
	}),
);
const stageStyle = computed(() => {
	const cut = (props.activeWordIndex + props.lineIndex) % 4;
	const angle = [-5, 3.5, -2.5, 5][cut];
	const align = ["left", "right", "center", "left"][cut];
	const direction = cut % 2 === 0 ? 1 : -1;
	return {
		"--impact-angle": `${angle}deg`,
		"--impact-align": align,
		"--impact-origin": align,
		"--impact-scale": `${0.94 + ((props.progress ?? 0) / 100) * 0.1}`,
		"--echo-near": `${direction * 0.08}em`,
		"--echo-far": `${direction * -0.16}em`,
		"--echo-near-start": `${direction * 0.18}em`,
		"--echo-far-start": `${direction * -0.3}em`,
	};
});
</script>

<template>
	<div class="impact-frame" :style="stageStyle">
		<div class="impact-field" aria-hidden="true">
			<span
				v-for="item in field"
				:key="item.token.id"
				class="impact-field-word"
				:class="{
					'is-current': item.current,
					'is-past': item.past,
					'is-near': item.near,
				}"
				:style="item.style"
				>{{ item.token.text }}</span
			>
		</div>
		<div class="impact-slash" aria-hidden="true" />
		<div
			v-if="currentToken"
			:key="currentToken.id"
			class="impact-hero"
			:class="{
				'is-long': currentToken.text.length > 11,
				'is-very-long': currentToken.text.length > 18,
			}"
			aria-hidden="true"
		>
			<span class="impact-echo impact-echo-far">{{ currentToken.text }}</span>
			<span class="impact-echo impact-echo-near">{{ currentToken.text }}</span>
			<span class="impact-current">{{ currentToken.text }}</span>
		</div>
		<p class="sr-only">{{ tokens.map((token) => token.text).join(" ") }}</p>
	</div>
</template>

<style scoped>
.impact-frame {
	position: relative;
	width: 100%;
	min-height: 30rem;
	overflow: hidden;
	isolation: isolate;
}
.impact-field {
	position: absolute;
	inset: 0;
	mask-image: radial-gradient(circle at center, #000 15%, rgb(0 0 0 / 82%) 54%, transparent 100%);
}
.impact-field-word {
	position: absolute;
	left: var(--field-x);
	top: var(--field-y);
	width: max-content;
	max-width: min(30vw, 18ch);
	font-size: clamp(0.8rem, 1.65vw, 1.5rem);
	font-weight: 720;
	line-height: 0.94;
	letter-spacing: -0.03em;
	text-transform: uppercase;
	color: color-mix(in srgb, var(--ui-primary) 38%, var(--player-foreground));
	opacity: var(--field-opacity);
	filter: blur(var(--field-blur));
	transform: translate(-50%, -50%) rotate(var(--field-angle)) scale(var(--field-scale));
	transition:
		opacity 320ms ease-out,
		filter 420ms cubic-bezier(0.16, 1, 0.3, 1),
		transform 460ms cubic-bezier(0.16, 1, 0.3, 1),
		color 260ms ease-out;
}
.impact-field-word.is-near {
	color: color-mix(in srgb, var(--ui-primary) 56%, var(--player-foreground));
}
.impact-field-word.is-past {
	text-shadow: 0 0.2rem 1rem color-mix(in srgb, #000 38%, transparent);
}
.impact-field-word.is-current {
	opacity: 0;
}
.impact-slash {
	position: absolute;
	left: 7%;
	right: 7%;
	top: 50%;
	height: 2px;
	background: linear-gradient(
		90deg,
		transparent,
		color-mix(in srgb, var(--ui-primary) 76%, var(--player-foreground)),
		transparent
	);
	box-shadow: 0 0 1.5rem color-mix(in srgb, var(--ui-primary) 32%, transparent);
	transform: rotate(var(--impact-angle)) scaleX(var(--impact-scale));
	transform-origin: var(--impact-origin) center;
	transition: transform 400ms cubic-bezier(0.16, 1, 0.3, 1);
}
.impact-hero {
	position: absolute;
	z-index: 1;
	left: 6%;
	right: 6%;
	top: 50%;
	display: grid;
	align-items: center;
	font-size: clamp(3.25rem, 10vw, 8rem);
	font-weight: 890;
	line-height: 0.88;
	letter-spacing: -0.04em;
	text-align: var(--impact-align);
	text-transform: uppercase;
	text-wrap: balance;
	overflow-wrap: anywhere;
	transform: translateY(-50%) rotate(var(--impact-angle)) scale(var(--impact-scale));
	transform-origin: var(--impact-origin) center;
	animation: impact-cut-in 460ms cubic-bezier(0.16, 1, 0.3, 1) both;
}
.impact-hero.is-long {
	font-size: clamp(2.75rem, 7.5vw, 6rem);
}
.impact-hero.is-very-long {
	font-size: clamp(2rem, 5.5vw, 4.5rem);
}
.impact-hero > span {
	grid-area: 1 / 1;
	min-width: 0;
}
.impact-current {
	position: relative;
	background: linear-gradient(
		110deg,
		var(--player-foreground) 28%,
		color-mix(in srgb, var(--ui-primary) 72%, var(--player-foreground)) 66%,
		var(--player-foreground)
	);
	background-clip: text;
	color: transparent;
	filter: drop-shadow(0 0.45rem 1.4rem color-mix(in srgb, #000 55%, transparent));
}
.impact-echo {
	color: color-mix(in srgb, var(--ui-primary) 58%, var(--player-foreground));
	pointer-events: none;
}
.impact-echo-near {
	opacity: 0.32;
	filter: blur(0.13rem);
	transform: translate(var(--echo-near), -0.1em) scale(1.035);
	animation: impact-echo-near 460ms cubic-bezier(0.16, 1, 0.3, 1) both;
}
.impact-echo-far {
	opacity: 0.15;
	filter: blur(0.38rem);
	transform: translate(var(--echo-far), 0.13em) scale(1.09);
	animation: impact-echo-far 540ms cubic-bezier(0.16, 1, 0.3, 1) both;
}
@keyframes impact-cut-in {
	from {
		opacity: 0.45;
		filter: blur(0.65rem);
		transform: translateY(-50%) rotate(calc(var(--impact-angle) - 7deg)) scale(1.2);
	}
}
@keyframes impact-echo-near {
	from {
		opacity: 0.55;
		transform: translate(var(--echo-near-start), -0.16em) scale(1.12);
	}
}
@keyframes impact-echo-far {
	from {
		opacity: 0.34;
		transform: translate(var(--echo-far-start), 0.22em) scale(1.18);
	}
}
@container workspace (max-width: 600px) {
	.impact-frame {
		min-height: 23rem;
	}
	.impact-hero {
		left: 5%;
		right: 5%;
		font-size: clamp(2.6rem, 12vw, 5.5rem);
	}
	.impact-hero.is-long {
		font-size: clamp(2.15rem, 9vw, 4.25rem);
	}
	.impact-hero.is-very-long {
		font-size: clamp(1.75rem, 7vw, 3.25rem);
	}
	.impact-field-word {
		max-width: 42vw;
		font-size: clamp(0.7rem, 3.5vw, 1.25rem);
	}
}
@media (prefers-reduced-motion: reduce) {
	.impact-field-word,
	.impact-slash,
	.impact-hero,
	.impact-echo {
		animation: none;
		transition: none;
	}
	.impact-echo {
		display: none;
	}
}
</style>
