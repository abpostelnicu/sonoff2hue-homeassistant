# dotnet_oracle

Generates test fixtures from .NET and from the sonoff2hue .NET app itself, so the tests can check that the Python port
behaves exactly as the app does, including where Python's own functions differ from .NET's. CI doesn't need .NET: the
fixtures are committed, and `tests/engine` compares the port against them.

- `tests/engine/fixtures/dotnet_text.json`: `char.IsWhiteSpace`, `ToUpperInvariant` and `ToLowerInvariant` for every
  code point, with invariant globalization as the app runs (`Program.cs`, "text").
- `tests/engine/fixtures/dotnet_engine.json`: edge cases that `engine_cases.py` builds (DNS packets, eWeLink
  announcements and payloads, press-detection sequences, summaries, JSON comparisons, target resolution, toggles with
  their requests and log lines, `StringComparer.OrdinalIgnoreCase` comparisons), evaluated by the app's own classes
  (`Engine.cs`). `tests/engine/test_dotnet_parity.py` replays them through the port, and lists the few known
  differences with their reasons.

Regenerate both after a change to the app's engine (the parity rule in `.github/copilot-instructions.md`), with the
.NET 10 SDK and a checkout of the private repository `abpostelnicu/sonoff2hue`. Use a checkout of the commit to compare
against, for example a detached worktree of `main`:

```sh
tools/dotnet_oracle/generate.sh <checkout of abpostelnicu/sonoff2hue>
```

The fixture records the app's `git describe`. The script builds the app from that checkout, so its `bin/` and `obj/`
folders change there.
