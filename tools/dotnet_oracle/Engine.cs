#if SONOFF2HUE
using System.Net;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Nodes;
using Sonoff2Hue.Ewelink;
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
}
#endif
