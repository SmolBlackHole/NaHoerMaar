// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { SessionAuthority } from "../api/transport";
import type { BackendClient } from "../client";
import type { Lyrics } from "../models/lyrics";
import { createQueryState } from "./queryState";

export function createLyricsWorkflow(client: BackendClient, authority: SessionAuthority) {
	const lyrics = createQueryState<Lyrics>(authority);
	return {
		lyrics,
		load: (trackId: string, refresh = false) =>
			lyrics.load((signal) => client.lyrics.get(trackId, refresh, signal)),
		dispose: () => lyrics.dispose(),
	};
}
