export interface LyricsEditToken {
	id: string;
	text: string;
}

export interface LyricsEditVerseLine {
	id: number;
	text: string;
}

export interface LyricsEditProps {
	tokens: LyricsEditToken[];
	activeWordIndex: number;
	lineIndex: number;
	progress?: number;
	contextTokens?: LyricsEditToken[];
	verseLines?: LyricsEditVerseLine[];
}
