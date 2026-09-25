// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { SessionAuthority } from "../api/transport";
import type { BackendClient } from "../client";
import { createAccessWorkflow } from "./access";
import { createDiscoveryWorkflow } from "./discovery";
import { createLogsWorkflow } from "./logs";
import { createProfileWorkflow } from "./profile";
import { createRecentWorkflow } from "./recent";
import { createStatisticsWorkflow } from "./statistics";

/** Factories keep query state local to the page that owns its lifecycle. */
export function createBackendWorkflows(client: BackendClient, authority: SessionAuthority) {
	return {
		access: () => createAccessWorkflow(client, authority),
		discovery: () => createDiscoveryWorkflow(client, authority),
		logs: () => createLogsWorkflow(client, authority),
		profile: () => createProfileWorkflow(client, authority),
		recent: () => createRecentWorkflow(client, authority),
		statistics: () => createStatisticsWorkflow(client, authority),
	};
}
