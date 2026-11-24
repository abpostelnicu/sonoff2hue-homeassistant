# js_oracle

Generates a test fixture from the .NET app's own web UI, so the tests can check that the Python port of its switch
editor behaves exactly as the browser did. CI needs neither Node nor .NET: the fixture is committed, and
`tests/engine/test_picker.py` and `test_jstext.py` compare the port against it.

- `tests/engine/fixtures/js_picker.json`: JavaScript's `trim()` and `toLowerCase()` for every code point and for words
  where the final sigma applies, and the cases that `picker_cases.py` builds for `targets.js` (`splitKind`,
  `resolveLight`, `resolveGroup`, `encodeLight`, `encodeGroup`, `createSelection`, `unresolvedEntries`,
  `selectionToConfig` and `splitList`) and for `format.js` (`sameId` and `switchLabel`), evaluated by the real modules
  (`oracle.mjs`). Resources are passed as objects, and results name them by their index in the list they came from, so
  the tests check identity as JavaScript's `indexOf` does. `tests/engine/test_switch_form.py` replays the `format.js`
  cases.

Regenerate it after a change to the app's web UI (the parity rule in `.github/copilot-instructions.md`), with Node 20
or later and a checkout of the private repository `abpostelnicu/sonoff2hue`. Use a checkout of the commit to compare
against, for example a detached worktree of `main`:

```sh
tools/js_oracle/generate.sh <checkout of abpostelnicu/sonoff2hue>
```

The fixture records the app's `git describe` and Node's version. The script only reads the checkout. The port uses
Python's `str.lower()` for `toLowerCase()`, which agrees with it only for the same Unicode version: Node 20.19 and
Python 3.14 both have Unicode 16.0, and `test_jstext.py` fails if the fixture's version isn't Python's.
