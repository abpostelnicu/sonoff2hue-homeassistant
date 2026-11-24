// Evaluates picker_cases.py's cases with the .NET app's own web UI module targets.js, and JavaScript's string rules for
// every code point, and writes tests/engine/fixtures/js_picker.json. Run from the repository root through generate.sh.
// Usage: node tools/js_oracle/oracle.mjs <checkout of abpostelnicu/sonoff2hue> <cases.json> <output.json> <app version>
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const [checkout, casesPath, outputPath, appVersion] = process.argv.slice(2);
if (!appVersion) {
  console.error('usage: node oracle.mjs <checkout> <cases.json> <output.json> <app version>');
  process.exit(2);
}

const ui = join(checkout, 'src', 'Sonoff2Hue.Frontend', 'wwwroot', 'js');
const targets = await import(pathToFileURL(join(ui, 'targets.js')).href);
const cases = JSON.parse(readFileSync(casesPath, 'utf8'));

// String.prototype.trim and toLowerCase for every code point.
const whiteSpace = [];
const lower = [];
for (let c = 0; c <= 0x10ffff; c++) {
  if (c >= 0xd800 && c <= 0xdfff) continue;
  const s = String.fromCodePoint(c);
  if (s.trim() === '') whiteSpace.push(c);
  const l = s.toLowerCase();
  if (l !== s) lower.push([c, l]);
}

/** A resolveLight/resolveGroup result, with the item as its index in the list it came from (identity). */
function resolution(result, listOf) {
  return Object.fromEntries(Object.entries(result).map(([key, value]) => (key === 'item' ? [key, listOf(result).indexOf(value)] : [key, value])));
}

const KINDS = ['light', 'room', 'zone'];

function describeSelection(selection) {
  return {
    ...Object.fromEntries(KINDS.map((kind) => [kind, [...selection[kind]]])),
    entries: selection.entries.map((entry) => ({ ...entry })),
  };
}

function selectionCase(scenario) {
  const { resources, current } = scenario;
  const selection = targets.createSelection(resources, current);
  const edits = scenario.edits.map((edit) => {
    const edited = targets.createSelection(resources, current);
    const open = targets.unresolvedEntries(edited);
    for (const index of edit.remove) open[index].removed = true;
    for (const kind of KINDS) {
      edited[kind].clear();
      for (const id of edit.select[kind]) edited[kind].add(id);
    }
    return {
      ...edit,
      config: targets.selectionToConfig(edited, resources),
      unresolved: targets.unresolvedEntries(edited).map((entry) => edited.entries.indexOf(entry)),
    };
  });
  return {
    ...scenario,
    selection: describeSelection(selection),
    unresolved: targets.unresolvedEntries(selection).map((entry) => selection.entries.indexOf(entry)),
    edits,
  };
}

const { lights } = cases.resolveLight;
const { rooms, zones } = cases.resolveGroup;
const output = {
  generator: 'tools/js_oracle/generate.sh',
  node: process.version,
  unicode: process.versions.unicode,
  app: appVersion,
  text: { whiteSpace, lower },
  trim: cases.trim.map((value) => [value, value.trim()]),
  lower: cases.lower.map((value) => [value, value.toLowerCase()]),
  splitKind: cases.splitKind.map((entry) => ({ entry, ...targets.splitKind(entry) })),
  resolveLight: {
    lights,
    cases: cases.resolveLight.entries.map((entry) => ({ entry, result: resolution(targets.resolveLight(entry, lights), () => lights) })),
  },
  resolveGroup: {
    rooms,
    zones,
    cases: cases.resolveGroup.entries.map((entry) => ({
      entry,
      result: resolution(targets.resolveGroup(entry, rooms, zones), (r) => (r.kind === 'room' ? rooms : zones)),
    })),
  },
  encode: cases.encode.map((items) => ({
    items,
    light: items.map((item) => targets.encodeLight(item, items)),
    room: items.map((item) => targets.encodeGroup('room', item, items)),
    zone: items.map((item) => targets.encodeGroup('zone', item, items)),
  })),
  selection: cases.selection.map(selectionCase),
  splitList: cases.splitList.map((text) => [text, targets.splitList(text)]),
};

// One case per line: arrays of objects or arrays get a line per element, and objects that hold such arrays a line per
// key; everything else stays on one line.
const isNested = (value) => Array.isArray(value) && value.some((item) => item !== null && typeof item === 'object');
const holdsNested = (value) => value !== null && typeof value === 'object' && !Array.isArray(value) && Object.values(value).some(isNested);

function layout(value, indent) {
  const inner = `${indent} `;
  if (isNested(value)) {
    return `[\n${value.map((item) => inner + (holdsNested(item) ? layout(item, inner) : JSON.stringify(item))).join(',\n')}\n${indent}]`;
  }
  if (holdsNested(value) || indent === '') {
    return `{\n${Object.entries(value).map(([key, item]) => `${inner}${JSON.stringify(key)}: ${layout(item, inner)}`).join(',\n')}\n${indent}}`;
  }
  return JSON.stringify(value);
}

writeFileSync(outputPath, `${layout(output, '')}\n`);
console.log(`Wrote ${outputPath}: ${process.version}, Unicode ${process.versions.unicode}, ${whiteSpace.length} white-space characters and ${lower.length} lowercase mappings.`);
