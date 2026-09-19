export interface YouTubePlayer {
	mute(): void;
	playVideo(): void;
	pauseVideo(): void;
	seekTo(seconds: number, allowSeekAhead: boolean): void;
	destroy(): void;
	getIframe(): HTMLIFrameElement;
}

interface PlayerEvent {
	target: YouTubePlayer;
}
interface YouTubeApi {
	Player: new (
		element: HTMLElement,
		options: {
			videoId: string;
			width: string;
			height: string;
			playerVars: { origin: string; playsinline: number; autoplay: number; start: number };
			events: {
				onReady(event: PlayerEvent): void;
				onError(event: PlayerEvent & { data: number }): void;
				onAutoplayBlocked(): void;
			};
		},
	) => YouTubePlayer;
}

declare global {
	interface Window {
		YT?: YouTubeApi;
		onYouTubeIframeAPIReady?: () => void;
	}
}

let loading: Promise<YouTubeApi> | undefined;
export function loadYouTube(): Promise<YouTubeApi> {
	if (window.YT?.Player) return Promise.resolve(window.YT);
	if (loading) return loading;
	loading = new Promise<YouTubeApi>((resolve, reject) => {
		const script = document.createElement("script");
		const fail = () => {
			clearTimeout(timeout);
			script.remove();
			loading = undefined;
			reject(new Error("YouTube could not be loaded."));
		};
		const timeout = setTimeout(fail, 15000);
		window.onYouTubeIframeAPIReady = () => {
			clearTimeout(timeout);
			if (window.YT?.Player) resolve(window.YT);
			else fail();
		};
		script.src = "https://www.youtube.com/iframe_api";
		script.async = true;
		script.onerror = fail;
		document.head.append(script);
	});
	return loading;
}
