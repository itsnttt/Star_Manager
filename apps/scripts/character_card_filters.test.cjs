const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const source = fs.readFileSync(path.join(__dirname, "../src/App.vue"), "utf8");

// Execute the renderer's actual handlers without mounting the full app shell.
function block(start, end) {
  const from = source.indexOf(start);
  const to = source.indexOf(end, from);
  assert.ok(from >= 0 && to > from);
  return source.slice(from, to);
}

function harness() {
  const requests = [];
  const scrollResets = [];
  const catalogLoads = [];
  const rows = [
    { id: "a", tags: ["Sample"], favorite: true, missingCount: 1 },
    { id: "b", tags: ["Other"], favorite: false, missingCount: 0 },
    { id: "c", tags: ["sample"], favorite: false, missingCount: 0 }
  ];
  const state = vm.createContext({
    computed: fn => ({ get value() { return fn(); } }),
    cards: { value: rows },
    cardTagFilter: { scope: "library", open: true, search: "" },
    cardGenderFilter: { value: "all" },
    cardDependencyFilter: { value: "all" },
    cardFavoriteFilter: { value: false },
    cardTagOptions: { value: ["Sample", "Other"] },
    cardTagFilterSuggestions: { value: ["Sample", "Other"] },
    uniqueCardTags: tags => tags,
    loadCardTagCatalogForPrompt: async prompt => catalogLoads.push(prompt),
    characterSideMode: { value: "detail" },
    selectedCardFolder: { value: "female" },
    selectedCards: { value: new Set() },
    selectedCardDetailPath: { value: "" },
    selectedCardProfile: { value: null },
    selectedCardDependencies: { value: [] },
    selectedCardProfileError: { value: "" },
    cardBulkMode: { value: false },
    cardLibrary: { checked: true, validGameDir: true, hasMore: false },
    cardTree: { value: null },
    cardListRequestId: 0,
    backendReady: { value: true },
    paths: { gameDir: "test-game" },
    CHARACTER_CARD_PAGE_SIZE: 96,
    resetCardLibraryScrollPosition: mode => scrollResets.push(mode),
    resetCardFilterSelectionState: () => {},
    performance: { now: () => 0 },
    encodeQuery: encodeURIComponent,
    mapCharacterCardRows: data => data,
    formatDurationMs: () => "0ms",
    log: () => {},
    window: { desktopApi: { backendRequest: async url => {
      requests.push(new URL(url, "http://localhost"));
      return { ok: true, is_valid_game_dir: true, cards: rows, total: rows.length, has_more: false };
    } } }
  });
  vm.runInContext([
    block("const baseVisibleCards =", "const cardBrowserCountText ="),
    block("async function selectCardFolder(", "function handleCharacterCardGridScroll("),
    block("async function openCardTagFilter(", "function toggleTagPromptValue("),
    block("function setCardDependencyFilter(", "function resetCardFilterSelectionState(")
    , block("function setCardGenderFilter(", "async function exportSelectedCardCoordinate(")
  ].join("\n"), state);
  return { state, requests, scrollResets, catalogLoads, rows, visible: () => vm.runInContext("visibleCards.value", state) };
}

test("library tag changes filter existing rows without requests", () => {
  const h = harness();
  h.state.setCardDependencyFilter("tag:SAMPLE");
  assert.equal(h.state.cards.value, h.rows);
  assert.equal(h.state.cardTagFilter.search, "SAMPLE");
  assert.equal(h.state.cardTagFilter.open, false);
  assert.equal(h.visible().map(row => row.id).join(","), "a,c");
  assert.deepEqual(h.scrollResets, ["character"]);
  h.state.cardFavoriteFilter.value = true;
  assert.equal(h.visible().map(row => row.id).join(","), "a");
  h.state.cardFavoriteFilter.value = false;
  h.state.setCardDependencyFilter("tag:unknown");
  assert.equal(h.visible().length, 0);
  h.state.setCardDependencyFilter("missing");
  assert.equal(h.visible().map(row => row.id).join(","), "a");
  h.state.setCardDependencyFilter("all");
  assert.equal(h.visible().length, 3);
  assert.equal(h.requests.length, 0);
});

test("library entry and refresh fetch all rows even with an active tag", async () => {
  const h = harness();
  h.state.cardTagFilter.scope = "directory";
  h.state.cardDependencyFilter.value = "tag:Sample";
  h.state.setCardTagScope("library");
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.requests.length, 1);
  assert.equal(h.requests[0].searchParams.get("scope"), "library");
  assert.equal(h.requests[0].searchParams.has("tag"), false);
  assert.equal(h.requests[0].searchParams.has("limit"), false);
  assert.equal(h.state.cards.value.length, 3);
  assert.equal(h.visible().length, 2);
  await h.state.selectCardFolder("");
  assert.equal(h.requests.length, 2);
  assert.equal(h.requests[1].searchParams.has("tag"), false);
});

test("directory entry restores tree and retains 96-row pagination", async () => {
  const h = harness();
  h.state.setCardTagScope("directory");
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.state.characterSideMode.value, "tree");
  assert.equal(h.requests.length, 1);
  assert.equal(h.requests[0].searchParams.get("limit"), "96");
  h.state.characterSideMode.value = "detail";
  h.state.setCardTagScope("directory");
  assert.equal(h.state.characterSideMode.value, "detail");
  assert.equal(h.requests.length, 1);
});

test("tag changes during library loading do not discard the pending full list", async () => {
  const h = harness();
  const pending = h.state.selectCardFolder("");
  h.state.setCardDependencyFilter("tag:Other");
  await pending;
  assert.equal(h.requests.length, 1);
  assert.equal(h.state.cards.value.length, 3);
  assert.equal(h.visible().map(row => row.id).join(","), "b");
});

test("library scrolling never appends a redundant page", async () => {
  const h = harness();
  h.state.cardLibrary.hasMore = true;
  await h.state.loadMoreCharacterCards();
  assert.equal(h.requests.length, 0);
});

test("library gender filter uses the female/male path segment and composes with tags", () => {
  const h = harness();
  h.rows[0].relativePath = "female/one.png";
  h.rows[1].relativePath = "male/two.png";
  h.rows[2].relativePath = "female/three.png";
  h.state.setCardGenderFilter("female");
  assert.equal(h.visible().map(row => row.id).join(","), "a,c");
  h.state.setCardDependencyFilter("tag:Other");
  assert.equal(h.visible().length, 0);
  h.state.setCardGenderFilter("male");
  assert.equal(h.visible().map(row => row.id).join(","), "b");
  h.state.setCardGenderFilter("invalid");
  assert.equal(h.state.cardGenderFilter.value, "all");
  assert.equal(h.visible().length, 1);
});

test("clearing an active tag immediately opens the complete catalog", () => {
  const h = harness();
  h.state.selectCardTagFilter("Sample");
  assert.equal(h.state.cardTagFilter.open, false);
  h.state.clearCardTagFilter();
  assert.equal(h.state.cardTagFilter.search, "");
  assert.equal(h.state.cardDependencyFilter.value, "all");
  assert.equal(h.state.cardTagFilter.open, true);
  assert.equal(h.visible().length, 3);
  assert.equal(h.catalogLoads.length, 1);
  assert.equal(h.requests.length, 0);
});

test("keyboard deletion to empty or whitespace clears the tag and shows the catalog", () => {
  const h = harness();
  for (const empty of ["", "   "]) {
    h.state.selectCardTagFilter("Sample");
    h.state.updateCardTagFilterSearch(empty);
    assert.equal(h.state.cardTagFilter.search, "");
    assert.equal(h.state.cardDependencyFilter.value, "all");
    assert.equal(h.state.cardTagFilter.open, true);
  }
  assert.equal(h.requests.length, 0);
});

test("clearing search preserves non-tag filters and explicit dismissal still works", () => {
  const h = harness();
  h.state.setCardDependencyFilter("missing");
  h.state.updateCardTagFilterSearch("Other");
  h.state.clearCardTagFilter();
  assert.equal(h.state.cardDependencyFilter.value, "missing");
  assert.equal(h.state.cardTagFilter.open, true);
  h.state.closeCardTagFilter();
  assert.equal(h.state.cardTagFilter.open, false);
  h.state.selectCardTagFilter("Other");
  assert.equal(h.state.cardTagFilter.open, false);
  assert.equal(h.state.cardDependencyFilter.value, "tag:Other");
});
