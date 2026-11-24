#if SONOFF2HUE
using System.Diagnostics;
using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using Sonoff2Hue.Configuration;
using YamlDotNet.Core;
using YamlDotNet.Core.Events;

/// <summary>
/// Evaluates the "yaml" cases: YamlDotNet's parsing events for each text, and what AppConfig.Parse makes of it. A
/// document that is a single plain scalar makes AppConfig.Parse call itself with the scalar's value until the stack
/// overflows, which ends the process. So the cases run in a child process that writes one line per case, and the case
/// that ends it is recorded as a crash before another child goes on with the next one.
/// </summary>
internal static class Yaml
{
    private const int MaxListing = 2048;

    public static JsonArray Run(string casesPath, int count)
    {
        var results = new JsonArray();
        while (results.Count < count)
        {
            var start = new ProcessStartInfo(Environment.ProcessPath!) { RedirectStandardOutput = true, RedirectStandardError = true };
            if (Path.GetFileNameWithoutExtension(Environment.ProcessPath!) == "dotnet")
            {
                start.ArgumentList.Add(typeof(Yaml).Assembly.Location);
            }

            foreach (var argument in new[] { "yaml-child", casesPath, results.Count.ToString(CultureInfo.InvariantCulture) })
            {
                start.ArgumentList.Add(argument);
            }

            using var child = Process.Start(start)!;
            var stderr = child.StandardError.ReadToEndAsync();
            while (child.StandardOutput.ReadLine() is { } line)
            {
                results.Add(JsonNode.Parse(line));
            }

            child.WaitForExit();
            if (results.Count < count)
            {
                var crashed = Parse(Cases(casesPath)[results.Count]!.AsObject(), out _);
                crashed["crash"] = stderr.Result.Split('\n')[0].Trim();
                results.Add(crashed);
            }
        }

        return results;
    }

    /// <summary>Writes the result of each case from <paramref name="first"/> on as a line of JSON.</summary>
    public static void Child(string casesPath, int first)
    {
        foreach (var input in Cases(casesPath).Skip(first))
        {
            Console.WriteLine(Evaluate(input!.AsObject()).ToJsonString());
            Console.Out.Flush();
        }
    }

    /// <summary>
    /// A "wellFormedUri" case: whether Uri.IsWellFormedUriString accepts the text, as YamlDotNet's TagName asks of every
    /// tag that doesn't start with '!', or what it throws.
    /// </summary>
    public static JsonObject WellFormedUri(JsonObject input)
    {
        var result = (JsonObject)input.DeepClone();
        var text = new string([.. input["units"]!.AsArray().Select(unit => (char)unit!.GetValue<int>())]);
        try
        {
            result["wellFormed"] = Uri.IsWellFormedUriString(text, UriKind.RelativeOrAbsolute);
        }
        catch (Exception ex)
        {
            result["error"] = $"{ex.GetType().Name}: {ex.Message}";
        }

        return result;
    }

    private static JsonArray Cases(string casesPath) => JsonNode.Parse(File.ReadAllText(casesPath))!["yaml"]!.AsArray();

    private static JsonObject Evaluate(JsonObject input)
    {
        var result = Parse(input, out var text);
        try
        {
            result["config"] = Config(AppConfig.Parse(text));
        }
        catch (Exception ex)
        {
            result["error"] = Escape($"{ex.GetType().Name}: {ex.Message}");
        }

        return result;
    }

    /// <summary>The case with YamlDotNet's parsing events, which never crash.</summary>
    private static JsonObject Parse(JsonObject input, out string text)
    {
        text = input["units"] is JsonArray units
            ? new string([.. units.Select(unit => (char)unit!.GetValue<int>())])
            : input["text"]!.GetValue<string>();
        var result = (JsonObject)input.DeepClone();
        var events = new List<string>();
        try
        {
            var parser = new Parser(new StringReader(text));
            while (parser.MoveNext())
            {
                events.Add(Describe(parser.Current!));
            }
        }
        catch (Exception ex)
        {
            result["parseError"] = Thrown(ex);
        }

        // A long listing is kept as its SHA-256, which the test computes from its own listing.
        var listing = string.Join("\n", events);
        if (listing.Length > MaxListing)
        {
            result["eventCount"] = events.Count;
            result["eventsSha256"] = Convert.ToHexStringLower(SHA256.HashData(Encoding.ASCII.GetBytes(listing)));
        }
        else
        {
            result["events"] = listing;
        }

        return result;
    }

    private static JsonObject Thrown(Exception ex)
    {
        var json = new JsonObject { ["type"] = ex.GetType().Name, ["message"] = Escape(ex.Message) };
        if (ex is YamlException yaml)
        {
            json["start"] = Mark(yaml.Start);
            json["end"] = Mark(yaml.End);
        }

        return json;
    }

    private static string Mark(YamlDotNet.Core.Mark mark) => $"{mark.Line}:{mark.Column}:{mark.Index}";

    /// <summary>An event on one line: its type, marks, properties and value, in printable ASCII.</summary>
    private static string Describe(ParsingEvent e)
    {
        var line = new StringBuilder($"{e.GetType().Name} {Mark(e.Start)}-{Mark(e.End)}");
        if (e is NodeEvent node)
        {
            if (!node.Anchor.IsEmpty)
            {
                line.Append($" &{Escape(node.Anchor.Value)}");
            }

            if (!node.Tag.IsEmpty)
            {
                line.Append($" !{Escape(node.Tag.Value)}");
            }
        }

        switch (e)
        {
            case Scalar scalar:
                line.Append($" {scalar.Style}");
                line.Append(scalar.IsPlainImplicit ? " plain" : string.Empty);
                line.Append(scalar.IsQuotedImplicit ? " quoted" : string.Empty);
                line.Append(scalar.IsKey ? " key" : string.Empty);
                line.Append($" \"{Escape(scalar.Value)}\"");
                break;
            case SequenceStart sequence:
                line.Append($" {sequence.Style}{(sequence.IsImplicit ? " implicit" : string.Empty)}");
                break;
            case MappingStart mapping:
                line.Append($" {mapping.Style}{(mapping.IsImplicit ? " implicit" : string.Empty)}");
                break;
            case AnchorAlias alias:
                line.Append($" *{Escape(alias.Value.Value)}");
                break;
            case DocumentStart document:
                line.Append(document.IsImplicit ? " implicit" : string.Empty);
                if (document.Version is { } version)
                {
                    line.Append($" %YAML {version.Version.Major}.{version.Version.Minor}");
                }

                foreach (var tag in document.Tags ?? [])
                {
                    line.Append($" %TAG {Escape(tag.Handle)} {Escape(tag.Prefix)}");
                }

                break;
            case DocumentEnd document:
                line.Append(document.IsImplicit ? " implicit" : string.Empty);
                break;
        }

        return line.ToString();
    }

    /// <summary>Printable ASCII, with every other UTF-16 code unit, a quote and a backslash as \uXXXX.</summary>
    private static string Escape(string value)
    {
        var escaped = new StringBuilder(value.Length);
        foreach (var c in value)
        {
            if (c is < ' ' or > '~' or '"' or '\\')
            {
                escaped.Append($"\\u{(int)c:x4}");
            }
            else
            {
                escaped.Append(c);
            }
        }

        return escaped.ToString();
    }

    private static JsonNode? EscapedOrNull(string? value) => value is null ? null : Escape(value);

    private static JsonObject Config(AppConfig config) => new()
    {
        ["hue"] = new JsonObject
        {
            ["bridgeIp"] = EscapedOrNull(config.Hue.BridgeIp),
            ["bridgeId"] = EscapedOrNull(config.Hue.BridgeId),
            ["appKey"] = EscapedOrNull(config.Hue.AppKey),
            ["certificateSha256"] = EscapedOrNull(config.Hue.CertificateSha256),
        },
        ["switches"] = new JsonArray([.. config.Switches.Select(s => (JsonNode)new JsonObject
        {
            ["name"] = EscapedOrNull(s.Name),
            ["deviceId"] = EscapedOrNull(s.DeviceId),
            ["deviceKey"] = EscapedOrNull(s.DeviceKey),
            ["lights"] = new JsonArray([.. s.Lights.Select(EscapedOrNull)]),
            ["groups"] = new JsonArray([.. s.Groups.Select(EscapedOrNull)]),
        })]),
    };
}
#endif
