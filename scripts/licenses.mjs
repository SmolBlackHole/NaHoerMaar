// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { basename, join } from "node:path";
import { fileURLToPath } from "node:url";
import { init } from "license-checker-rseidelsohn";

const root = fileURLToPath(new URL("../", import.meta.url));
const output = join(root, "frontend/public/licenses/generated");
const python = join(
	root,
	".venv",
	process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
);
const project = JSON.parse(await readFile(join(root, "package.json"), "utf8"));

function projectUrl(value) {
	if (typeof value !== "string") return null;
	try {
		const url = new URL(value);
		return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password
			? url.href
			: null;
	} catch {
		return null;
	}
}

const dependencies = await new Promise((resolve, reject) =>
	init(
		{ start: root, customFormat: { name: "", version: "", licenseText: "" } },
		(error, packages) => (error ? reject(error) : resolve(packages)),
	),
);
const records = await Promise.all(
	Object.values(dependencies)
		.filter((item) => !["nahormaar", "nahormaar-frontend"].includes(item.name))
		.map(async (item) => ({
			name: item.name,
			version: item.version,
			license: Array.isArray(item.licenses) ? item.licenses.join(" OR ") : item.licenses,
			category: "JavaScript",
			url: item.repository,
			// Some packages ship no license; a README is not a license text.
			text: [
				/^readme/i.test(basename(item.licenseFile || "")) ? "" : item.licenseText,
				item.noticeFile ? await readFile(item.noticeFile, "utf8") : "",
			]
				.filter(Boolean)
				.join("\n\n"),
		})),
);
records.push(
	...JSON.parse(
		execFileSync(python, ["-X", "utf8", join(root, "scripts/python_licenses.py")], {
			cwd: root,
			encoding: "utf8",
			maxBuffer: 20 * 1024 * 1024,
		}),
	),
);

async function localNotice(name, path, license, url, category = "Fonts & artwork", version = "") {
	records.push({
		name,
		version,
		license,
		category,
		url,
		text: await readFile(join(root, path), "utf8"),
	});
}

await localNotice(
	"NaHörMaar",
	"LICENSE",
	"MPL-2.0",
	"https://github.com/SmolBlackHole/NaHoerMaar",
	"Project",
	project.version,
);
await localNotice(
	"Nuxt UI dashboard template",
	"frontend/public/licenses/THIRD_PARTY.txt",
	"MIT",
	"https://github.com/nuxt-ui-templates/dashboard",
	"Project",
);
await localNotice(
	"Pixabots by Pablo Stanley",
	"frontend/public/licenses/pixabots.txt",
	"Pixabots Free Pack license",
	"https://pixabots.com",
);
for (const [name, file] of Object.entries({
	"DM Sans": "dmsans",
	Geist: "geist",
	Inter: "inter",
	Outfit: "outfit",
	"Public Sans": "publicsans",
	Poppins: "poppins",
	Raleway: "raleway",
})) {
	await localNotice(
		name,
		`frontend/public/licenses/fonts/${file}.txt`,
		"OFL-1.1",
		`https://github.com/google/fonts/tree/main/ofl/${file}`,
	);
}

await mkdir(output, { recursive: true });
const packages = [];
const notices = [];
for (const record of records.sort(
	(a, b) => a.name.localeCompare(b.name) || a.version.localeCompare(b.version),
)) {
	const text = record.text?.trim();
	let textUrl = null;
	if (text) {
		const filename = `${createHash("sha256").update(text).digest("hex")}.txt`;
		await writeFile(join(output, filename), `${text}\n`, "utf8");
		textUrl = `/licenses/generated/${filename}`;
	}
	const item = {
		name: record.name,
		version: record.version,
		category: record.category,
		license: record.license || "Not declared",
		url: projectUrl(record.url),
		textUrl,
	};
	packages.push(item);
	notices.push(
		`${item.name} ${item.version}\n${item.license}\n${item.url || ""}\n\n${text || "No license text shipped in this package. See the project source."}`,
	);
}
await writeFile(
	join(output, "notices.txt"),
	`${notices.join("\n\n----------------------------------------\n\n")}\n`,
	"utf8",
);
await writeFile(join(output, "index.json"), `${JSON.stringify({ packages })}\n`, "utf8");
console.log(
	`License inventory: ${packages.length} components, ${packages.filter((p) => !p.textUrl).length} without a bundled license text.`,
);
