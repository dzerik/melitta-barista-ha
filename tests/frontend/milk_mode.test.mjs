/** Actual recipe-editor controls must send the milk choice to the saved slot. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { harness } from "./panel-harness.mjs";

function* templates(value) {
  if (value?.strings) {
    yield value;
    for (const child of value.values) yield* templates(child);
  } else if (Array.isArray(value)) {
    for (const child of value) yield* templates(child);
  }
}
const coffee = {process:"coffee", intensity:"medium", aroma:"standard", shots:"three", temperature:"normal",portion_ml:40};
async function editor(c1 = coffee, c2 = {...coffee,process:"milk"}) {
  const {element, strings} = await harness();
  strings.setServerStrings({"values.milk_mode.warm_milk":"Warm milk", "values.milk_mode.milk_froth":"Milk froth"});
  const calls = [];
  element.hass = {callService:async (...args) => calls.push(args)};
  element._profileId = 2;
  element._findServiceEntity = () => "button.machine_brew";
  element._load = async () => {};
  element._showToast = () => {};
  element._openEditor({id:322,category:"espresso",name:"Espresso",components:[c1,c2]}, 0);
  return {element, calls, strings};
}

for (const slot of ["c1", "c2"]) {
  test(`milk choice ${slot} sends froth without losing stored coffee shots`, async () => {
    const {element, calls} = await editor({...coffee,process:"milk"}, {...coffee,process:"milk"});
    const view = element._renderComponentBlock(slot, "recipes.component1");
    const select = [...templates(view)].find(t => t.values[0] === "recipes.milk_mode" && t.strings.join("").includes("<select"));
    assert.ok(select, "Milk mode control must be rendered for a milk component");
    assert.match(String(select), /Warm milk/);
    assert.match(String(select), /Milk froth/);
    select.values.find(value => typeof value === "function")({target:{value:"milk_froth"}});
    assert.equal(element._editing[slot].shots, "three");
    await element._save();
    const data = calls[0][2];
    assert.equal(data[slot === "c1" ? "shots1" : "shots2"], "none");
    assert.equal(data[slot === "c1" ? "shots2" : "shots1"], "three");
    assert.equal(data.category, "espresso");
    assert.equal(data.profile_id, 2);
    assert.ok(!Object.keys(data).some(key => key.startsWith("milk_mode")));
  });
}

test("coffee editor hides milk controls and keeps shots after process toggles", async () => {
  const {element, calls} = await editor();
  assert.doesNotMatch(String(element._renderComponentBlock("c1", "recipes.component1")), /recipes.milk_mode/);
  element._updateComponent("c1", "process", "milk");
  element._updateComponent("c1", "milk_mode", "milk_froth");
  element._updateComponent("c1", "process", "coffee");
  await element._save();
  assert.equal(calls[0][2].shots1, "three");
});

for (const shots of ["none", "one", "two", "three"]) {
  test(`untouched loaded milk component retains legacy shots ${shots}`, async () => {
    const {element, calls} = await editor({...coffee,process:"milk",shots});
    assert.ok(!Object.hasOwn(element._editing.c1, "milk_mode"));
    assert.match(String(element._renderComponentBlock("c1", "recipes.component1")), /recipes.milk_mode/);
    await element._save();
    assert.equal(calls[0][2].shots1, shots);
  });
}

test("opening another recipe clears a previous explicit milk choice", async () => {
  const {element, calls} = await editor({...coffee,process:"milk"});
  element._updateComponent("c1", "milk_mode", "milk_froth");
  element._openEditor({id:322,category:"espresso",components:[{...coffee,process:"milk",shots:"one"},null]},0);
  await element._save();
  assert.equal(calls[0][2].shots1, "one");
});
