// "text": writes tests/engine/fixtures/dotnet_text.json, the .NET string rules that engine/text.py reproduces, for
// every code point. "engine <cases> <output> <app version>": evaluates engine_cases.py's cases with the app's code
// (Engine.cs). Run from the repository root through generate.sh.
using System.Text.Json;

if (args is ["engine", var cases, var output, var appVersion])
{
#if SONOFF2HUE
    Engine.Run(cases, output, appVersion);
    return;
#else
    Console.Error.WriteLine("Build with -p:Sonoff2HueProject=<path to Sonoff2Hue.csproj> to evaluate the engine cases.");
    Environment.Exit(2);
#endif
}

if (args is not ["text"])
{
    Console.Error.WriteLine("usage: DotnetOracle text | engine <cases.json> <output.json> <app version>");
    Environment.Exit(2);
}

var whiteSpace = new List<int>();
var upper = new List<int[]>();
var lower = new List<int[]>();
for (var c = 0; c <= 0x10FFFF; c++)
{
    if (c is >= 0xD800 and <= 0xDFFF)
    {
        continue;
    }

    var s = char.ConvertFromUtf32(c);
    if (s.Length == 1 && char.IsWhiteSpace(s[0]))
    {
        whiteSpace.Add(c);
    }

    if (s.ToUpperInvariant() is var u && u != s)
    {
        upper.Add([c, char.ConvertToUtf32(u, 0)]);
    }

    if (s.ToLowerInvariant() is var l && l != s)
    {
        lower.Add([c, char.ConvertToUtf32(l, 0)]);
    }
}

var path = Path.Combine("tests", "engine", "fixtures", "dotnet_text.json");
File.WriteAllText(path, JsonSerializer.Serialize(new
{
    generator = "tools/dotnet_oracle",
    runtime = Environment.Version.ToString(),
    whiteSpace,
    upper,
    lower,
}));
Console.WriteLine($"Wrote {path}: .NET {Environment.Version}, {whiteSpace.Count} white-space characters, {upper.Count} uppercase and {lower.Count} lowercase mappings.");
