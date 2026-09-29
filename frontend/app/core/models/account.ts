// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { components } from "../api/schema.generated";

type Schema = components["schemas"];

export type AccountSession = Schema["SessionView"];
export type Account = Schema["UserView"];
export type ListenerProfile = Schema["ProfilePageView"];
export type ProfileLibrary = Schema["ProfileLibraryView"];
export type ProfileLibraryTrack = Schema["ProfileLibraryTrackView"];
export type ProfilePlaylist = Schema["ProfilePlaylistView"];
export type Appearance = Schema["AppearanceView"];
export type ProfileUpdate = Schema["ProfileUpdate"];
export type AppearanceUpdate = Schema["AppearanceUpdate"];
export type CurrentUserUpdate = Schema["CurrentUserUpdate"];
export type StatisticsPeriod = Schema["StatisticsPeriod"];

export const defaultAppearance: Readonly<Appearance> = {
	mode: "dark",
	artwork_colors: true,
	primary_color: "teal",
	neutral_color: "zinc",
	font_family: "Geist",
	icon_set: "lucide",
	text_size: "md",
};
