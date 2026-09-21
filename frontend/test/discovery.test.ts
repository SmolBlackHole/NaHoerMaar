import { effectScope, nextTick, reactive } from "vue";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useDiscovery } from "../app/composables/useDiscovery";
import { repositoryFixture } from "./repository-fixture";
import { discovery, view } from "./engine-fixtures";

const stores = vi.hoisted(() => ({ player: vi.fn(), profile: vi.fn(), radio: vi.fn() }));
vi.mock("../app/stores/player", () => ({ usePlayerStore: stores.player }));
vi.mock("../app/stores/profile", () => ({ useProfileStore: stores.profile }));
vi.mock("../app/stores/radioPreview", () => ({ useRadioPreviewStore: stores.radio }));

const url = "https://music.youtube.com/playlist?list=PL12345678901234";
function playlist(version: string, ids: string[]) {
	const page = discovery(version);
	page.playlist = {
		title: version,
		reference: {
			kind: "playlist",
			identity: { namespace: "youtube", external_id: "PL12345678901234" },
			source_url: url,
		},
	};
	page.entries = ids.map((id, position) => ({ ...page.entries[0]!, track_id: id, position }));
	page.total = ids.length;
	return page;
}
const scopes: ReturnType<typeof effectScope>[] = [];
afterEach(() => {
	scopes.splice(0).forEach((scope) => scope.stop());
	vi.unstubAllGlobals();
});
function setup() {
	const request = vi.fn<typeof fetch>();
	const fixture = repositoryFixture(request);
	const profile = reactive({ status: "authenticated" });
	const player = reactive({
		snapshot: view(),
		enabled: true,
		pending: false,
		isAdding: () => false,
		addMany: vi.fn().mockResolvedValue(true),
	});
	stores.profile.mockReturnValue(profile);
	stores.player.mockReturnValue(player);
	stores.radio.mockReturnValue(reactive({ source: null, version: 0 }));
	vi.stubGlobal("useToast", () => ({ add: vi.fn() }));
	const scope = effectScope();
	scopes.push(scope);
	return {
		request,
		player,
		profile,
		flow: fixture.app.runWithContext(() => scope.run(useDiscovery))!,
	};
}

describe("discovery workflow", () => {
	it("cancels a playlist response when its panel closes", async () => {
		const { flow, request } = setup();
		let resolve!: (result: Response) => void;
		request.mockReturnValue(
			new Promise((done) => {
				resolve = done;
			}),
		);
		const opening = flow.openPlaylist(url);
		flow.panelOpen.value = false;
		expect(request.mock.lastCall?.[1]?.signal?.aborted).toBe(true);
		resolve(Response.json(playlist("late", ["a"])));
		await opening;
		expect(flow.preview.value).toBeNull();
		expect(flow.previewPending.value).toBe(false);
	});
	it("clears displayed private results and selections on sign-out", async () => {
		const { flow, request, profile } = setup();
		request.mockImplementation(async () => Response.json(playlist("old", ["a", "b"])));
		await flow.openPlaylist(url);
		await nextTick();
		expect(flow.selected.value.size).toBe(2);
		profile.status = "signed_out";
		expect(flow.preview.value).toBeNull();
		expect(flow.panelOpen.value).toBe(false);
		expect(flow.selected.value.size).toBe(0);
	});
	it("checks cached search results when returning from radio in the open panel", async () => {
		const { flow, request } = setup();
		request.mockImplementation(async () => Response.json(discovery("cached")));
		flow.source.value = "song";
		await flow.submit();
		await nextTick();
		flow.view.value = "radio";
		await nextTick();
		request.mockClear();
		flow.view.value = "search";
		await nextTick();
		expect(request).toHaveBeenCalledWith(
			"/api/catalog/search/cached?offset=0&limit=20",
			expect.anything(),
		);
	});
	it("preserves explicit selections after refresh and leaves new tracks unselected", async () => {
		const { flow, request } = setup();
		request
			.mockResolvedValueOnce(Response.json(playlist("old", ["a", "b"])))
			.mockResolvedValueOnce(Response.json(playlist("new", ["new", "b", "a"])));
		await flow.openPlaylist(url);
		await nextTick();
		expect([...flow.selected.value]).toEqual([0, 1]);
		flow.toggle(1);
		await flow.openPlaylist(url);
		await nextTick();
		expect([...flow.selected.value]).toEqual([0]);
		expect(flow.preview.value?.version).toBe("old");
		flow.applyUpdate();
		await nextTick();
		expect(flow.preview.value?.version).toBe("new");
		expect([...flow.selected.value]).toEqual([2]);
	});
	it("imports occurrences in displayed order and does not clear a new playlist after a late reply", async () => {
		const { flow, request, player } = setup();
		request
			.mockResolvedValueOnce(Response.json(playlist("old", ["a", "a"])))
			.mockResolvedValueOnce(Response.json(playlist("new", ["b"])));
		await flow.openPlaylist(url);
		await nextTick();
		let resolve!: (result: boolean) => void;
		player.addMany.mockReturnValue(
			new Promise((done) => {
				resolve = done;
			}),
		);
		const adding = flow.importSelection();
		expect(player.addMany).toHaveBeenCalledWith(["a", "a"], false);
		await flow.openPlaylist(url + "new");
		await nextTick();
		resolve(true);
		await adding;
		expect(flow.preview.value?.version).toBe("new");
		expect([...flow.selected.value]).toEqual([0]);
		expect(flow.panelOpen.value).toBe(true);
	});
	it("discards an in-flight playlist after sign-out", async () => {
		const { flow, request, profile } = setup();
		let resolve!: (result: Response) => void;
		request.mockReturnValue(
			new Promise((done) => {
				resolve = done;
			}),
		);
		const opening = flow.openPlaylist(url);
		profile.status = "signed_out";
		await nextTick();
		resolve(Response.json(playlist("late", ["a"])));
		await opening;
		expect(flow.preview.value).toBeNull();
		expect(flow.selected.value.size).toBe(0);
	});
});
