// SPDX-FileCopyrightText: 2026 SmolBlackHole
//
// SPDX-License-Identifier: MPL-2.0

import assert from "node:assert/strict";
import test from "node:test";

import { projectName } from "../src/index.js";

test("exports the project name", () => {
	assert.equal(projectName, "NaHörMaar Frontend");
});
