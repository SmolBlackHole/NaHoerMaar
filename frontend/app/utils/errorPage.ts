import type { IconKey } from "~/config/icons";

export interface ErrorPageContent {
	code: number;
	title: string;
	description: string;
	icon: IconKey;
	retryable: boolean;
}

const errors: Record<number, Omit<ErrorPageContent, "code">> = {
	400: {
		title: "That request had bad vibes.",
		description: "Something in it did not make sense. Head back and give it another shot.",
		icon: "caution",
		retryable: false,
	},

	401: {
		title: "Who are you again?",
		description:
			"Your session wandered off. Return to the player and sign in with Discord again.",
		icon: "user",
		retryable: false,
	},

	402: {
		title: "Payment required. Apparently.",
		description: "Nobody here actually wants your money. Something else went wrong.",
		icon: "info",
		retryable: false,
	},

	403: {
		title: "Nice try. You're not allowed in there.",
		description: "This part of NaHörMaar is reserved for someone with more keys than you.",
		icon: "key",
		retryable: false,
	},

	404: {
		title: "And where the fuck do you think you're going?",
		description: "There is no track, page or secret tunnel here. You took a wrong turn.",
		icon: "search",
		retryable: false,
	},

	405: {
		title: "Wrong door, wrong knock.",
		description: "That route exists. It just absolutely does not accept what you tried to do.",
		icon: "caution",
		retryable: false,
	},

	408: {
		title: "This request wandered off.",
		description: "It took too long and missed its cue. Try it again when you're ready.",
		icon: "clock",
		retryable: true,
	},

	409: {
		title: "Everyone touched the queue at once.",
		description:
			"The player changed while this request was still catching up. Reload and try again.",
		icon: "reload",
		retryable: true,
	},

	410: {
		title: "This one is gone gone.",
		description: "Whatever used to be here has left the building and is not coming back.",
		icon: "error",
		retryable: false,
	},

	418: {
		title: "I'm a teapot. Apparently.",
		description: "You found the legally required stupid HTTP joke. Congratulations.",
		icon: "tip",
		retryable: false,
	},

	422: {
		title: "Technically valid. Practically nonsense.",
		description: "The request reached us intact, then immediately failed the vibe check.",
		icon: "caution",
		retryable: false,
	},

	423: {
		title: "Hands off. It's busy.",
		description: "Something is currently locked by another operation. Give it a moment.",
		icon: "key",
		retryable: true,
	},

	425: {
		title: "Bit early, champ.",
		description: "You managed to ask before the system was ready for the answer.",
		icon: "clock",
		retryable: true,
	},

	429: {
		title: "Easy there, DJ.",
		description: "That's a lot of requests at once. Give the bot a second to breathe.",
		icon: "warning",
		retryable: true,
	},

	451: {
		title: "This got bureaucratic.",
		description:
			"The server has been informed that this content should not be served. Very exciting paperwork.",
		icon: "file",
		retryable: false,
	},

	500: {
		title: "Well, fuck. We (you) broke something.",
		description:
			"The problem is on our side but you made sure that it happened. Try once more, then check the logs if it keeps happening. If you don't have access to the logs, please contact the server administrator.",
		icon: "error",
		retryable: true,
	},

	501: {
		title: "We haven't built that bit yet.",
		description: "The idea exists. The code, regrettably, does not.",
		icon: "settings",
		retryable: false,
	},

	502: {
		title: "The browser and the bot aren't speaking.",
		description: "One of them left the conversation. A retry usually gets them talking again.",
		icon: "radio",
		retryable: true,
	},

	503: {
		title: "The party is temporarily on hold.",
		description: "NaHörMaar is starting up, restarting or taking a very short nap.",
		icon: "radio",
		retryable: true,
	},

	504: {
		title: "The bot took the scenic route.",
		description: "It did not answer in time. Try again before sending out a search party.",
		icon: "clock",
		retryable: true,
	},

	507: {
		title: "We are out of room.",
		description: "Storage is full, which is impressive in all the wrong ways.",
		icon: "warning",
		retryable: true,
	},
};

const fallback: Omit<ErrorPageContent, "code"> = {
	title: "How did you even get here?",
	description:
		"If you can see this, well, Hi! :D This is a fallback error page. It means that the server did not know what to do with the request and could not find a more specific error page for it. If you are a user, please contact the server administrator. If you are the server administrator, well, either contact the developer or check the logs. If you are the developer, well, there goes your free time. I can already hear the 'ThiS shoUldnt taKe tOo lOng to FIx' coming from the back of your head. Good luck with that. Go ahead and fix it. Thanks. ~Developer from the past :D",
	icon: "caution",
	retryable: true,
};

export function errorPageContent(statusCode: number | undefined): ErrorPageContent {
	const code =
		typeof statusCode === "number" && Number.isInteger(statusCode) && statusCode >= 400
			? statusCode
			: 500;
	return { code, ...(errors[code] ?? fallback) };
}
