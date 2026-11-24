#if SONOFF2HUE
using System.Net;
using System.Reflection;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;
using Sonoff2Hue.Bridge;
using Sonoff2Hue.Configuration;
using Sonoff2Hue.Ewelink;
using Sonoff2Hue.Hue;
using Sonoff2Hue.Mdns;

/// <summary>Evaluates engine_cases.py's cases with the sonoff2hue app's own engine code.</summary>
internal static class Engine
{
    private const string Device = "1000abcdef";

    public static void Run(string casesPath, string outputPath, string appVersion)
    {
        var cases = JsonNode.Parse(File.ReadAllText(casesPath))!.AsObject();
        var output = new JsonObject
        {
            ["generator"] = "tools/dotnet_oracle/generate.sh",
            ["runtime"] = Environment.Version.ToString(),
            ["app"] = appVersion,
            ["packets"] = Map(cases["packets"]!, Packet),
            ["readParams"] = Map(cases["readParams"]!, ReadParams),
            ["detector"] = Map(cases["detector"]!, Detector),
            ["summarize"] = Map(cases["summarize"]!, Summarize),
            ["deepEquals"] = Map(cases["deepEquals"]!, DeepEquals),
            ["bridgeAddress"] = Map(cases["bridgeAddress"]!, BridgeAddress),
            ["pairingResponse"] = Map(cases["pairingResponse"]!, PairingResponse),
            ["hueClient"] = Map(cases["hueClient"]!, HueClientCase),
            ["fingerprint"] = Map(cases["fingerprint"]!, Fingerprint),
            ["statusLine"] = Map(cases["statusLine"]!, StatusLine),
            ["encodings"] = Map(cases["encodings"]!, EncodingCase),
            ["ipAddress"] = Map(cases["ipAddress"]!, IpAddressCase),
            ["targets"] = Map(cases["targets"]!, TargetsCase),
            ["toggle"] = Map(cases["toggle"]!, ToggleCase),
            ["compareIgnoreCase"] = Map(cases["compareIgnoreCase"]!, CompareIgnoreCase),
            ["switchValidation"] = Map(cases["switchValidation"]!, Switches.Validation),
            ["switchWrites"] = Map(cases["switchWrites"]!, Switches.Writes),
            ["reasonPhrases"] = ReasonPhrases(),
        };
        var options = new JsonSerializerOptions { WriteIndented = true, Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping };
        File.WriteAllText(outputPath, output.ToJsonString(options) + "\n");
        Console.WriteLine($"Wrote {outputPath}.");
    }

    private static JsonArray Map(JsonNode list, Func<JsonObject, JsonObject> evaluate) =>
        new([.. list.AsArray().Select(item => (JsonNode)evaluate(item!.AsObject()))]);

    private static string Thrown(Exception ex) => $"{ex.GetType().Name}: {ex.Message}";

    private static string? Text(JsonObject json, string key) => json[key]?.GetValue<string>();

    private static JsonObject Packet(JsonObject input)
    {
        var result = new JsonObject { ["name"] = Text(input, "name"), ["sender"] = Text(input, "sender"), ["hex"] = Text(input, "hex") };
        DnsMessage message;
        try
        {
            message = DnsMessage.Parse(Convert.FromHexString(Text(input, "hex")!));
        }
        catch (Exception ex)
        {
            result["parseError"] = Thrown(ex);
            return result;
        }

        result["message"] = new JsonObject
        {
            ["id"] = message.Id,
            ["isResponse"] = message.IsResponse,
            ["questions"] = new JsonArray([.. message.Questions.Select(q => (JsonNode)new JsonArray(q.Name, (int)q.Type, q.UnicastResponse))]),
            ["answers"] = Records(message.Answers),
            ["authorities"] = Records(message.Authorities),
            ["additionals"] = Records(message.Additionals),
        };
        var packet = new MdnsPacket(message, new IPEndPoint(IPAddress.Parse(Text(input, "sender")!), 5353), DateTimeOffset.UnixEpoch);
        try
        {
            result["announcements"] = new JsonArray([.. EwelinkAnnouncement.FromPacket(packet).Select(a => (JsonNode)new JsonObject
            {
                ["deviceId"] = a.DeviceId,
                ["instanceName"] = a.InstanceName,
                ["seq"] = a.Seq,
                ["deviceType"] = a.DeviceType,
                ["encrypted"] = a.Encrypted,
                ["iv"] = a.Iv,
                ["data"] = a.Data,
                ["address"] = a.Address?.ToString(),
            })]);
        }
        catch (Exception ex)
        {
            result["announcementsError"] = Thrown(ex);
        }

        result["probes"] = new JsonArray([.. EwelinkAnnouncement.ProbingDeviceIds(message).Select(id => (JsonNode)id)]);
        return result;
    }

    private static JsonArray Records(IReadOnlyList<DnsResourceRecord> records) =>
        new([.. records.Select(r => (JsonNode)new JsonObject
        {
            ["name"] = r.Name,
            ["type"] = (int)r.Type,
            ["cacheFlush"] = r.CacheFlush,
            ["ttl"] = r.Ttl,
            ["ptr"] = r.PtrDomain,
            ["address"] = r.Address?.ToString(),
            ["srvTarget"] = r.SrvTarget,
            ["srvPort"] = r.SrvPort,
            ["txt"] = r.Txt is null ? null : new JsonArray([.. r.Txt.Select(kv => (JsonNode)new JsonArray(kv.Key, kv.Value))]),
        })]);

    private static JsonObject ReadParams(JsonObject input)
    {
        var announcement = new EwelinkAnnouncement(
            Device, $"eWeLink_{Device}", 1, "plug", input["encrypted"]!.GetValue<bool>(), Text(input, "iv"), Text(input, "data")!, null,
            DateTimeOffset.UnixEpoch);
        var result = new JsonObject { ["name"] = Text(input, "name") };
        foreach (var key in new[] { "encrypted", "iv", "data", "key" })
        {
            result[key] = input[key]?.DeepClone();
        }

        try
        {
            var parameters = EwelinkCrypto.TryReadParams(announcement, Text(input, "key"), out var error);
            result["error"] = error;
            result["params"] = parameters is null ? null : Serialized(parameters);
        }
        catch (Exception ex)
        {
            result["throws"] = Thrown(ex);
        }

        return result;
    }

    private static JsonObject Serialized(JsonNode node)
    {
        try
        {
            return new JsonObject { ["json"] = node.ToJsonString() };
        }
        catch (Exception ex)
        {
            return new JsonObject { ["throws"] = Thrown(ex) };
        }
    }

    private static JsonObject Detector(JsonObject input)
    {
        var detector = new PressDetector();
        var results = new JsonArray();
        foreach (var step in input["steps"]!.AsArray())
        {
            var json = step!.AsObject();
            var parameters = Text(json, "params") is { } text ? (JsonObject)JsonNode.Parse(text)! : null;
            var announcement = new EwelinkAnnouncement(
                Device, $"eWeLink_{Device}", json["seq"]?.GetValue<long>(), "plug", true, Text(json, "iv"), Text(json, "data")!, null,
                DateTimeOffset.UnixEpoch);
            var result = new JsonObject();
            try
            {
                var switchEvent = detector.Process(announcement, parameters);
                result["kind"] = switchEvent.Kind.ToString();
                result["seq"] = switchEvent.Seq;
                result["detail"] = switchEvent.Detail;
            }
            catch (Exception ex)
            {
                result["throws"] = Thrown(ex);
            }

            result["isDetached"] = detector.IsDetached;
            result["hasBaseline"] = detector.HasBaseline;
            results.Add(result);
        }

        return new JsonObject { ["name"] = Text(input, "name"), ["steps"] = input["steps"]!.DeepClone(), ["results"] = results };
    }

    private static JsonObject Summarize(JsonObject input)
    {
        var result = new JsonObject { ["params"] = Text(input, "params") };
        try
        {
            result["summary"] = PressDetector.Summarize((JsonObject)JsonNode.Parse(Text(input, "params")!)!);
        }
        catch (Exception ex)
        {
            result["throws"] = Thrown(ex);
        }

        return result;
    }

    private static JsonObject DeepEquals(JsonObject input)
    {
        var result = new JsonObject { ["left"] = Text(input, "left"), ["right"] = Text(input, "right") };
        try
        {
            result["equal"] = JsonNode.DeepEquals(JsonNode.Parse(Text(input, "left")!), JsonNode.Parse(Text(input, "right")!));
        }
        catch (Exception ex)
        {
            result["throws"] = Thrown(ex);
        }

        return result;
    }

    private static JsonObject BridgeAddress(JsonObject input)
    {
        var address = Text(input, "address");
        var result = new JsonObject { ["address"] = address };
        try
        {
            if (HueBridgeAddress.TryCreateBaseUri(address, out var uri, out var error))
            {
                result["uri"] = uri.AbsoluteUri;
                result["host"] = uri.Host;
                result["port"] = uri.Port;
                try
                {
                    result["idnHost"] = uri.IdnHost;
                }
                catch (Exception ex)
                {
                    result["idnHostThrows"] = Thrown(ex);
                }
            }
            else
            {
                result["error"] = error;
            }
        }
        catch (Exception ex)
        {
            result["throws"] = Thrown(ex);
        }

        return result;
    }

    private static readonly MethodInfo ParsePairingResponse =
        typeof(HueBridgeSetup).GetMethod("ParsePairingResponse", BindingFlags.NonPublic | BindingFlags.Static)!;

    private static JsonObject PairingResponse(JsonObject input)
    {
        var status = input["status"]!.GetValue<int>();
        var body = Text(input, "body")!;
        var result = new JsonObject { ["status"] = status, ["body"] = body };
        try
        {
            var pairing = (PairingResult)ParsePairingResponse.Invoke(null, [(HttpStatusCode)status, body])!;
            result["result"] = new JsonObject
            {
                ["status"] = pairing.Status.ToString(),
                ["appKey"] = pairing.AppKey,
                ["error"] = pairing.Error,
            };
        }
        catch (TargetInvocationException ex) when (ex.InnerException is { } inner)
        {
            result["throws"] = Thrown(inner);
            if (inner is HueApiException hue)
            {
                result["statusCode"] = (int?)hue.StatusCode;
            }
        }

        return result;
    }

    private static JsonObject HueClientCase(JsonObject input)
    {
        var handler = new CannedHandler(input["answers"]!.AsArray());
        var result = new JsonObject
        {
            ["name"] = Text(input, "name"),
            ["operation"] = Text(input, "operation"),
            ["answers"] = input["answers"]!.DeepClone(),
            ["target"] = input["target"]?.DeepClone(),
        };
        using var client = new HueClient("192.168.1.20", "app-key", handler);
        try
        {
            result["result"] = Text(input, "operation") switch
            {
                "lights" => new JsonArray([.. client.GetLightsAsync(CancellationToken.None).GetAwaiter().GetResult().Select(
                    l => (JsonNode)new JsonArray(l.Id, l.Name, l.On))]),
                "groups" => new JsonArray([.. client.GetGroupsAsync(CancellationToken.None).GetAwaiter().GetResult().Select(
                    g => (JsonNode)new JsonArray(g.Id, g.Name, g.Kind, g.GroupedLightId))]),
                "lightStates" => States(client.GetLightStatesAsync(CancellationToken.None).GetAwaiter().GetResult()),
                "groupedLightStates" => States(client.GetGroupedLightStatesAsync(CancellationToken.None).GetAwaiter().GetResult()),
                "ping" => Done(client.PingAsync(CancellationToken.None)),
                "setOn" => Done(client.SetOnAsync(Target(input["target"]!.AsObject()), input["target"]!["on"]!.GetValue<bool>(), CancellationToken.None)),
                var other => throw new ArgumentException($"Unknown operation {other}"),
            };
        }
        catch (Exception ex)
        {
            result["throws"] = Thrown(ex);
            if (ex is HueApiException hue)
            {
                result["statusCode"] = (int?)hue.StatusCode;
                result["isTransient"] = hue.IsTransient;
                result["isAuthenticationError"] = hue.IsAuthenticationError;
            }
        }

        result["requests"] = handler.Requests;
        return result;
    }

    private static JsonNode? Done(Task task)
    {
        task.GetAwaiter().GetResult();
        return "done";
    }

    private static JsonArray States(IReadOnlyDictionary<string, bool> states) =>
        new([.. states.Select(s => (JsonNode)new JsonArray(s.Key, s.Value))]);

    private static HueTarget Target(JsonObject json) =>
        new(Enum.Parse<HueTargetKind>(Text(json, "kind")!), Text(json, "id")!, "name");

    /// <summary>Answers each request with the next canned answer, the last one repeated, and records the requests.</summary>
    private sealed class CannedHandler(JsonArray answers) : HttpMessageHandler
    {
        private int _next;

        public JsonArray Requests { get; } = [];

        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            Requests.Add(new JsonObject
            {
                ["method"] = request.Method.Method,
                ["uri"] = request.RequestUri!.AbsoluteUri,
                ["key"] = request.Headers.TryGetValues("hue-application-key", out var keys) ? string.Join(",", keys) : null,
                ["contentType"] = request.Content?.Headers.ContentType?.ToString(),
                ["body"] = request.Content is null ? null : await request.Content.ReadAsStringAsync(cancellationToken),
            });
            var answer = answers[Math.Min(_next++, answers.Count - 1)]!.AsObject();
            var response = new HttpResponseMessage((HttpStatusCode)answer["status"]!.GetValue<int>())
            {
                Content = new ByteArrayContent(Text(answer, "bodyBase64") is { } base64
                    ? Convert.FromBase64String(base64)
                    : Encoding.UTF8.GetBytes(Text(answer, "body") ?? string.Empty)),
            };
            if (Text(answer, "reason") is { } reason)
            {
                response.ReasonPhrase = reason;
            }

            if (Text(answer, "contentType") is { } contentType)
            {
                response.Content.Headers.TryAddWithoutValidation("Content-Type", contentType);
            }

            return response;
        }
    }

    private static JsonObject Fingerprint(JsonObject input)
    {
        var fingerprint = Text(input, "fingerprint")!;
        return new JsonObject { ["fingerprint"] = fingerprint, ["normalized"] = HueTls.Normalize(fingerprint) };
    }

    private static JsonObject StatusLine(JsonObject input)
    {
        var status = input["status"]!.GetValue<int>();
        var reason = Text(input, "reason");
        using var response = new HttpResponseMessage((HttpStatusCode)status);
        if (reason is not null)
        {
            response.ReasonPhrase = reason;
        }

        var result = new JsonObject { ["status"] = status, ["reason"] = reason, ["reasonPhrase"] = response.ReasonPhrase };
        try
        {
            response.EnsureSuccessStatusCode();
        }
        catch (HttpRequestException ex)
        {
            result["ensureSuccess"] = ex.Message;
        }

        return result;
    }

    private static JsonObject TargetsCase(JsonObject input)
    {
        var settings = new SwitchSettings
        {
            Name = Text(input, "name"),
            DeviceId = Text(input, "deviceId")!,
            Lights = [.. input["lights"]!.AsArray().Select(n => n!.GetValue<string>())],
            Groups = [.. input["groups"]!.AsArray().Select(n => n!.GetValue<string>())],
        };
        var lights = input["hueLights"]!.AsArray()
            .Select(n => new HueLight(Text(n!.AsObject(), "id")!, Text(n.AsObject(), "name")!, false))
            .ToList();
        var groups = input["hueGroups"]!.AsArray()
            .Select(n => new HueGroup(Text(n!.AsObject(), "id")!, Text(n.AsObject(), "name")!, Text(n.AsObject(), "kind")!, Text(n.AsObject(), "grouped")))
            .ToList();
        var result = (JsonObject)input.DeepClone();
        var errors = new List<string>();
        try
        {
            var targets = TargetResolver.Resolve(settings, lights, groups, errors);
            result["targets"] = new JsonArray([.. targets.Select(t => (JsonNode)new JsonArray(t.Kind.ToString(), t.Id, t.Name))]);
            result["errors"] = new JsonArray([.. errors.Select(e => (JsonNode)e)]);
        }
        catch (Exception ex)
        {
            result["throws"] = Thrown(ex);
        }

        return result;
    }

    private static JsonObject ToggleCase(JsonObject input)
    {
        var handler = new CannedHandler(input["answers"]!.AsArray());
        var logger = new RecordingLogger();
        var targets = input["targets"]!.AsArray()
            .Select(n => new HueTarget(Enum.Parse<HueTargetKind>(n![0]!.GetValue<string>()), n[1]?.GetValue<string>()!, n[2]?.GetValue<string>()!))
            .ToList();
        var result = (JsonObject)input.DeepClone();
        using var client = new HueClient("192.0.2.20", "app-key", handler);
        try
        {
            var toggled = new HueToggler(client, logger).ToggleAsync(targets, CancellationToken.None).GetAwaiter().GetResult();
            result["result"] = new JsonArray(toggled.TurnedOn, toggled.Changed, toggled.Unchanged, toggled.Failed);
        }
        catch (Exception ex)
        {
            result["throws"] = Thrown(ex);
        }

        result["requests"] = handler.Requests;
        result["log"] = logger.Lines;
        return result;
    }

    private static JsonObject CompareIgnoreCase(JsonObject input)
    {
        var result = (JsonObject)input.DeepClone();
        result["sign"] = Math.Sign(StringComparer.OrdinalIgnoreCase.Compare(Utf16(input["left"]), Utf16(input["right"])));
        return result;
    }

    private static string? Utf16(JsonNode? units) =>
        units is null ? null : new string([.. units.AsArray().Select(unit => (char)unit!.GetValue<int>())]);

    /// <summary>Records each line logged, as "&lt;level&gt;: &lt;message&gt;".</summary>
    private sealed class RecordingLogger : ILogger
    {
        public JsonArray Lines { get; } = [];

        public IDisposable? BeginScope<TState>(TState state) where TState : notnull => null;

        public bool IsEnabled(LogLevel logLevel) => true;

        public void Log<TState>(
            LogLevel logLevel, EventId eventId, TState state, Exception? exception, Func<TState, Exception?, string> formatter) =>
            Lines.Add((JsonNode)$"{logLevel}: {formatter(state, exception)}");
    }

    private static JsonObject IpAddressCase(JsonObject input)
    {
        var address = Text(input, "address")!;
        return new JsonObject { ["address"] = address, ["valid"] = IPAddress.TryParse(address, out _) };
    }

    private static JsonObject EncodingCase(JsonObject input)
    {
        var name = Text(input, "name")!;
        var result = new JsonObject { ["name"] = name };
        try
        {
            result["codePage"] = Encoding.GetEncoding(name).CodePage;
        }
        catch (Exception ex)
        {
            result["throws"] = Thrown(ex);
        }

        return result;
    }

    /// <summary>The reason phrase of a response whose status line has none, by status code.</summary>
    private static JsonObject ReasonPhrases()
    {
        var phrases = new JsonObject();
        for (var code = 100; code <= 599; code++)
        {
            using var response = new HttpResponseMessage((HttpStatusCode)code);
            if (response.ReasonPhrase is { } phrase)
            {
                phrases[code.ToString(System.Globalization.CultureInfo.InvariantCulture)] = phrase;
            }
        }

        return phrases;
    }
}
#endif
