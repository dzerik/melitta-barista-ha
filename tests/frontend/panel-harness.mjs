import { readFile } from "node:fs/promises";
import vm from "node:vm";

const www = new URL("../../custom_components/melitta_barista/www/", import.meta.url);
export async function harness() {
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
