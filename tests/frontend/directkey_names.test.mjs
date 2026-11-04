/** Execute the actual panel renderer and commands with cached drink metadata. */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import vm from "node:vm";

const www = new URL("../../custom_components/melitta_barista/www/", import.meta.url);
async function harness() {
  const elements = new Map();
  const context = vm.createContext({ console, URL, setTimeout, clearTimeout,
    customElements: { define: (name, cls) => elements.set(name, cls), get: name => elements.get(name) },
  });
  const modules = new Map();
  async function load(url, real = false) {
    if (modules.has(url)) return modules.get(url);
    let module;
    if (real || url.endsWith("/i18n/server-strings.js")) {
      module = new vm.SourceTextModule(await readFile(new URL(url), "utf8"), { context, identifier: url });
    } else {
      const exports = {
        LitElement: class { requestUpdate() {} },
        html: (strings, ...values) => ({ strings, values,
          toString: () => strings.reduce((out, s, i) => out + s + (values[i] ?? ""), "") }),
        css: () => "", t: (key, _lang, params) => params ? `${key} ${JSON.stringify(params)}` : key,
        panelStateKey: () => null,
        readPanelState: () => null, writePanelState: () => {},
      };
      module = new vm.SyntheticModule(Object.keys(exports), function () {
        for (const [key, value] of Object.entries(exports)) this.setExport(key, value);
      }, { context, identifier: url });
    }
    modules.set(url, module);
    await module.link(specifier => load(new URL(specifier, url).href));
    return module;
  }
  const root = await load(new URL("components/melitta-recipes.js", www).href, true);
  await root.evaluate();
  const element = new (elements.get("melitta-recipes"))();
  element._connected = () => true;
  const strings = modules.get(new URL("i18n/server-strings.js", www).href).namespace;
  strings.setServerStrings({ "values.directkey_category.espresso": "Espresso" });
  return { element, strings };
}
const identity = { type: 2, name: "Lungo", name_key: "lungo" };
const row = configured_recipe => ({ id: 312, name: "Espresso", type: 2,
  category: "espresso", configured_recipe, components: [null, null] });
const title = template => String(template).match(/class="drink-name">([^<]*)<\/span>/)?.[1];

test("configured drink title preserves the actual slot and category on click", async () => {
  const { element } = await harness();
  const rendered = element._renderCategoryCard(row(identity), 0);
  assert.equal(title(rendered), "Lungo");
  rendered.values.find(value => typeof value === "function")();
  assert.equal(element._editing.category, "espresso");
  assert.equal(element._editing.recipeId, 312);
  assert.match(String(element._renderEditor()), /Lungo/);
});

test("locale changes use recipe-name strings, then server English fallback", async () => {
  const { element, strings } = await harness();
  strings.setServerStrings({ "recipes.name.lungo": "Лунго", "values.directkey_category.espresso": "Эспрессо" });
  assert.equal(title(element._renderCategoryCard(row(identity), 0)), "Лунго");
  strings.setServerStrings({ "values.directkey_category.espresso": "Эспрессо" });
  assert.equal(title(element._renderCategoryCard(row(identity), 0)), "Lungo");
});

for (const metadata of [undefined, null, {}, {type:24,name:"Freestyle",name_key:"freestyle"},
  {...identity,type:"2"}, {...identity,type:true}, {...identity,name:""}, {...identity,name_key:""}]) {
  test(`legacy fallback for missing/invalid identity ${JSON.stringify(metadata)}`, async () => {
    const { element } = await harness();
    assert.equal(title(element._renderCategoryCard(row(metadata), 0)), "Espresso");
  });
}

test("profile/cache changes and metadata removal never reuse the previous label", async () => {
  const { element } = await harness();
  assert.equal(title(element._renderCategoryCard(row(identity), 0)), "Lungo");
  const second = {...row({type:1,name:"Ristretto",name_key:"ristretto"}),id:322,type:1};
  assert.equal(title(element._renderCategoryCard(second, 0)), "Ristretto");
  assert.equal(title(element._renderCategoryCard(row(null), 0)), "Espresso");
  assert.equal(title(element._renderCategoryCard(row(identity), 0)), "Lungo");
});

test("save and reset still target the profile/category/slot, never built-in Lungo", async () => {
  const { element } = await harness();
  const calls = [];
  element.hass = { callService: async (...args) => calls.push(args) };
  element._profileId = 1;
  element._findServiceEntity = () => "button.machine_brew";
  element._load = async () => {};
  element._showToast = () => {};
  element._confirmReset = async () => true;
  element._openEditor(row(identity), 0);
  await element._save();
  assert.equal(calls[0][0], "melitta_barista");
  assert.equal(calls[0][1], "save_directkey");
  assert.equal(calls[0][2].category, "espresso");
  assert.equal(calls[0][2].profile_id, 1);
  await element._reset(row(identity), 0);
  assert.equal(calls[1][1], "reset_recipe");
  assert.equal(calls[1][2].recipe_id, 312);
});
