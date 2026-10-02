/** Execute the shipped components with only Lit/DOM rendering stubbed. */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const www = new URL("../../custom_components/melitta_barista/www/", import.meta.url);

async function harness(path, storage = new Map()) {
  const elements = new Map();
  const context = vm.createContext({
    console, URL, setTimeout, clearTimeout,
    localStorage: {
      getItem: (key) => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, value),
      removeItem: (key) => storage.delete(key),
    },
    customElements: {
      define: (name, cls) => elements.set(name, cls), get: (name) => elements.get(name),
    },
  });
  const modules = new Map();
  async function load(url, root = false) {
    if (modules.has(url)) return modules.get(url);
    let module;
    if (root || url.endsWith("/panel-state.js")) {
      module = new vm.SourceTextModule(await readFile(fileURLToPath(url), "utf8"), {
        context, identifier: url,
        initializeImportMeta: (meta) => { meta.url = url; },
        importModuleDynamically: async (specifier) => {
          const dep = await load(new URL(specifier, url).href);
          if (dep.status !== "evaluated") await dep.evaluate();
          return dep;
        },
      });
    } else {
      const exports = {
        LitElement: class {
          connectedCallback() {}
          disconnectedCallback() {}
          updated() {}
          requestUpdate() {}
        },
        html: () => "", css: () => "", t: (key) => key,
        displayNameFor: () => "", labelFor: () => "", freeFormLabel: () => "",
        serverString: () => null, setServerStrings: () => {}, sharedStyles: [],
      };
      module = new vm.SyntheticModule(Object.keys(exports), function () {
        for (const [key, value] of Object.entries(exports)) this.setExport(key, value);
      }, { context, identifier: url });
    }
    modules.set(url, module);
    await module.link((specifier) => load(new URL(specifier, url).href));
    return module;
  }
  const root = await load(new URL(path, www).href, true);
  await root.evaluate();
  return { Class: [...elements.values()][0], context, storage };
}

const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
const update = async (element, ...keys) => {
  element.updated?.(new Map(keys.map((key) => [key, undefined])));
  await flush();
};

function backend(user = "alice") {
  const calls = [];
  return {
    user: { id: user }, calls,
    callService: async (...args) => { calls.push(args); },
    callWS: async (args) => {
      calls.push(args);
      switch (args.type) {
        case "melitta_barista/entries": return { entries: [
          { entry_id: "a", brand: "melitta" }, { entry_id: "b", brand: "melitta" },
        ] };
        case "melitta_barista/status": return { connected: true, active_profile: 1 };
        case "melitta_barista/recipes/list": return { directkey: [
          { profile_id: 1, recipes: [] }, { profile_id: 2, recipes: [] },
        ] };
        case "melitta_barista/syrups/list": return { syrups: [{ name: "Vanilla" }] };
        case "melitta_barista/toppings/list": return { toppings: [{ name: "Cocoa" }] };
        case "melitta_barista/sommelier/milk/get": return { milk_types: ["Oat", "Cow"] };
        default: return {};
      }
    },
  };
}

test("panel restores machine/tab and isolates HA users", async () => {
  const { Class } = await harness("melitta-panel.js");
  const first = new Class(); first.hass = backend();
  await update(first, "hass");
  first._activeEntry = "b"; first._tab = "recipes";
  await update(first, "_activeEntry", "_tab");
  const second = new Class(); second.hass = backend();
  await update(second, "hass");
  assert.equal(second._activeEntry, "b");
  assert.equal(second._tab, "recipes");
  second.hass = backend("bob");
  await update(second, "hass");
  assert.equal(second._activeEntry, "a");
  assert.equal(second._tab, "sommelier");
});

test("recipe profile survives reload and is isolated by entry", async () => {
  const { Class } = await harness("components/melitta-recipes.js");
  const first = new Class(); first.hass = backend(); first.entryId = "a";
  await update(first, "entryId", "hass");
  first._profileId = 2;
  await update(first, "_profileId");
  const next = new Class(); next.hass = backend(); next.entryId = "a";
  await update(next, "entryId", "hass");
  assert.equal(next._profileId, 2);
  next.entryId = "b";
  await update(next, "entryId");
  assert.equal(next._profileId, 1);
  next.entryId = "a";
  await update(next, "entryId");
  assert.equal(next._profileId, 2);
  assert.equal(next._editing, null);
});

test("sommelier restores form choices, including deliberately empty add-ins", async () => {
  const { Class } = await harness("components/melitta-sommelier.js");
  const first = new Class(); first.hass = backend(); first.entryId = "a";
  first.connectedCallback(); await update(first, "hass", "entryId");
  first._mode = "custom"; first._preference = "Something light";
  first._cupSize = "cup"; first._count = 2; first._temperature = "iced";
  first._allowMilk = []; first._moods = ["relaxing"]; first._dietary = ["vegan"];
  await update(first, "_mode", "_preference", "_cupSize", "_count", "_temperature",
    "_allowMilk", "_moods", "_dietary");
  const next = new Class(); next.hass = backend(); next.entryId = "a";
  next.connectedCallback(); await update(next, "hass", "entryId");
  assert.equal(next._preference, "Something light");
  assert.equal(next._cupSize, "cup");
  assert.equal(next._count, 2);
  assert.equal(next._temperature, "iced");
  assert.deepEqual(Array.from(next._allowMilk), []);
  assert.deepEqual(Array.from(next._dietary), ["vegan"]);
  assert.equal(next._session, null);
  assert.equal(next._wizardRecipe, null);
  assert.equal(next.hass.calls.some((call) => /generate|brew|save_directkey/.test(call.type || "")), false);
  next.entryId = "b"; await update(next, "entryId");
  assert.equal(next._preference, "");
  assert.equal(next._cupSize, "mug");
  next.entryId = "a"; await update(next, "entryId");
  assert.equal(next._preference, "Something light");
  next.hass = backend("bob"); await update(next, "hass");
  assert.equal(next._preference, "");
});

test("broken browser storage never prevents opening the panel", async () => {
  const storage = new Map();
  storage.get = () => { throw new Error("blocked"); };
  storage.set = () => { throw new Error("quota"); };
  const { Class } = await harness("melitta-panel.js", storage);
  const panel = new Class(); panel.hass = backend();
  await update(panel, "hass");
  assert.equal(panel._activeEntry, "a");
  panel._tab = "recipes"; await update(panel, "_tab");
  assert.equal(panel._tab, "recipes");
});

test("corrupt JSON, deleted machines and hidden tabs fall back safely", async () => {
  for (const saved of ["{broken", "null", "[]",
    JSON.stringify({ entry: "deleted", tab: "removed" }),
    JSON.stringify({ entry: "b", tab: "recipes" })]) {
    const storage = new Map(); storage.get = () => saved;
    const { Class } = await harness("melitta-panel.js", storage);
    const panel = new Class(); panel.hass = backend();
    const callWS = panel.hass.callWS;
    panel.hass.callWS = async (args) => args.type.endsWith("/entries")
      ? { entries: [{ entry_id: "b", brand: "nivona" }] } : callWS(args);
    await update(panel, "hass");
    assert.equal(panel._activeEntry, "b");
    assert.equal(panel._tab, "sommelier");
  }
});

test("invalid preferences are validated and removed ingredients stay removed", async () => {
  const storage = new Map();
  storage.get = () => JSON.stringify({
    mode: "obsolete", cup_size: {}, count: 99, preference: [],
    moods: ["obsolete", "relaxing", "relaxing"], dietary: "vegan",
    temperature: "lava", allow_milk: ["Deleted milk", "Oat", 7],
    allow_syrups: [], allow_toppings: [], show_addins: false,
  });
  const { Class } = await harness("components/melitta-sommelier.js", storage);
  const form = new Class(); form.hass = backend(); form.entryId = "a";
  form.connectedCallback(); await update(form, "entryId", "hass");
  assert.equal(form._mode, "surprise_me");
  assert.equal(form._cupSize, "mug");
  assert.equal(form._count, 3);
  assert.equal(form._preference, "");
  assert.equal(form._temperature, "auto");
  assert.equal(form._showAddins, false);
  assert.deepEqual(Array.from(form._moods), ["relaxing"]);
  assert.deepEqual(Array.from(form._dietary), []);
  assert.deepEqual(Array.from(form._allowMilk), ["Oat"]);
  form.vocab = { mood: { tokens: ["classic"] } };
  await update(form, "vocab");
  assert.deepEqual(Array.from(form._moods), []);
});

test("recipe profile waits for warm caches and falls back if deleted", async () => {
  const storage = new Map(); storage.get = () => JSON.stringify({ profile: 2 });
  const { Class } = await harness("components/melitta-recipes.js", storage);
  const editor = new Class(); editor.hass = backend(); editor.entryId = "a";
  const normal = editor.hass.callWS;
  let profiles = [];
  editor.hass.callWS = (args) => args.type.endsWith("/recipes/list")
    ? Promise.resolve({ directkey: profiles }) : normal(args);
  await update(editor, "entryId", "hass");
  assert.equal(editor._profileId, 2);
  profiles = [{ profile_id: 1, recipes: [] }];
  await editor._load();
  assert.equal(editor._profileId, 1);
});

test("recipe responses from the previous machine cannot replace the active data", async () => {
  const { Class } = await harness("components/melitta-recipes.js");
  const editor = new Class(); editor.hass = backend(); editor.entryId = "a";
  const normal = editor.hass.callWS;
  let resolveOld;
  editor.hass.callWS = (args) => args.entry_id === "a" && args.type.endsWith("/recipes/list")
    ? new Promise((resolve) => { resolveOld = resolve; }) : normal(args);
  await update(editor, "entryId", "hass");
  editor.entryId = "b"; await update(editor, "entryId");
  resolveOld({ directkey: [{ profile_id: 9 }] }); await flush();
  assert.equal(editor._profileId, 1);
  assert.equal(editor._profiles().length, 2);
});

test("previous user responses and generation cannot populate the new form", async () => {
  const { Class } = await harness("components/melitta-sommelier.js");
  const form = new Class(); form.hass = backend(); form.entryId = "a";
  const normal = form.hass.callWS;
  let resolveMilk, resolveGeneration;
  form.hass.callWS = (args) => {
    if (args.type.endsWith("/milk/get")) return new Promise((resolve) => { resolveMilk = resolve; });
    if (args.type.endsWith("/generate")) return new Promise((resolve) => { resolveGeneration = resolve; });
    return normal(args);
  };
  await update(form, "entryId", "hass");
  const generating = form._generate();
  form.hass = backend("bob"); await update(form, "hass");
  resolveMilk({ milk_types: ["Old user milk"] });
  resolveGeneration({ session: { id: "old-session" } });
  await generating; await flush();
  assert.deepEqual(Array.from(form._availableMilk), ["Oat", "Cow"]);
  assert.equal(form._session, null);
  assert.equal(form._generating, false);
});
