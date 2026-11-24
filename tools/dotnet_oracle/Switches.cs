#if SONOFF2HUE
using System.Reflection;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Http.HttpResults;
using Microsoft.Extensions.Logging.Abstractions;
using Sonoff2Hue.Configuration;

/// <summary>
/// Evaluates switch_cases.py's cases with the app's switch API (Web/Management/ConfigEndpoints.cs). Its types are
/// internal, so they are reached through reflection, as Engine.cs reaches ParsePairingResponse.
/// </summary>
internal static class Switches
{
    private const string Management = "Sonoff2Hue.Web.Management.";

    private static readonly Assembly App = typeof(AppConfig).Assembly;
    private static readonly Type InputType = App.GetType(Management + "SwitchInput", throwOnError: true)!;
    private static readonly Type StoreType = App.GetType(Management + "ConfigStore", throwOnError: true)!;
    private static readonly Type FactoryType = App.GetType(Management + "HueClientFactory", throwOnError: true)!;
    private static readonly Type EndpointsType = App.GetType(Management + "ConfigEndpoints", throwOnError: true)!;
    private static readonly MethodInfo ValidateMethod =
        App.GetType(Management + "SwitchValidation", throwOnError: true)!.GetMethod("Validate", BindingFlags.Public | BindingFlags.Static)!;
    private static readonly MethodInfo CreateMethod = EndpointsType.GetMethod("CreateAsync", BindingFlags.NonPublic | BindingFlags.Static)!;
    private static readonly MethodInfo UpdateMethod = EndpointsType.GetMethod("UpdateAsync", BindingFlags.NonPublic | BindingFlags.Static)!;

    // The app's message about a malformed device id gives an example id, which could be one of the user's devices. The
    // fixture is public, so the example becomes the made-up id that the port's message names.
    private static readonly Regex ExampleId = new(@"e\.g\. [0-9a-z]+\.$");

    /// <summary>SwitchValidation.Validate: the errors by field, in order, and the switch it would save.</summary>
    public static JsonObject Validation(JsonObject input)
    {
        object?[] args = [Input(input), null];
        var errors = (Dictionary<string, string[]>)ValidateMethod.Invoke(null, args)!;
        return new JsonObject
        {
            ["input"] = input.DeepClone(),
            ["errors"] = Errors(errors),
            ["settings"] = Settings((SwitchSettings)args[1]!),
        };
    }

    /// <summary>
    /// The endpoints that add (POST /api/switches) and change (PUT /api/switches/{deviceId}) a switch, one step after
    /// the other on a config file of their own, with the switches in the file after each step.
    /// </summary>
    public static JsonObject Writes(JsonObject scenario)
    {
        var folder = Directory.CreateTempSubdirectory("switch-writes-");
        try
        {
            var path = Path.Combine(folder.FullName, AppConfig.FileName);
            var hue = scenario["hue"]!.AsObject();
            new AppConfig
            {
                Hue = new HueSettings { BridgeIp = Text(hue, "bridgeIp"), AppKey = Text(hue, "appKey") },
                Switches = [.. scenario["switches"]!.AsArray().Select(item => Switch(item!.AsObject()))],
            }.Save(path);

            var logger = typeof(NullLogger<>).MakeGenericType(StoreType).GetField("Instance")!.GetValue(null);
            using var store = (IDisposable)Activator.CreateInstance(StoreType, path, logger, false)!;
            var factory = Activator.CreateInstance(FactoryType)!;
            var result = new JsonObject
            {
                ["name"] = Text(scenario, "name"),
                ["hue"] = hue.DeepClone(),
                ["switches"] = Stored(path),
            };
            var steps = new JsonArray();
            foreach (var item in scenario["steps"]!.AsArray())
            {
                var step = item!.AsObject();
                var input = Input(step["input"]!.AsObject());
                var http = new DefaultHttpContext();
                var task = Text(step, "op") == "create"
                    ? CreateMethod.Invoke(null, [input, http, store, factory, CancellationToken.None])
                    : UpdateMethod.Invoke(null, [Text(step, "deviceId"), input, http, store, factory, CancellationToken.None]);
                var answer = ((Task<IResult>)task!).GetAwaiter().GetResult();
                var shown = step.DeepClone().AsObject();
                shown["result"] = Result(answer);
                shown["switches"] = Stored(path);
                steps.Add(shown);
            }

            result["steps"] = steps;
            return result;
        }
        finally
        {
            folder.Delete(recursive: true);
        }
    }

    private static string? Text(JsonObject json, string key) => json[key]?.GetValue<string>();

    private static List<string>? List(JsonNode? node) =>
        node is JsonArray array ? [.. array.Select(item => item?.GetValue<string>()!)] : null;

    private static object Input(JsonObject json) =>
        Activator.CreateInstance(InputType, Text(json, "name"), Text(json, "deviceId"), Text(json, "deviceKey"), List(json["lights"]), List(json["groups"]))!;

    private static SwitchSettings Switch(JsonObject json) => new()
    {
        Name = Text(json, "name"),
        DeviceId = Text(json, "deviceId")!,
        DeviceKey = Text(json, "deviceKey"),
        Lights = List(json["lights"]) ?? [],
        Groups = List(json["groups"]) ?? [],
    };

    private static JsonObject Settings(SwitchSettings settings) => new()
    {
        ["name"] = settings.Name,
        ["deviceId"] = settings.DeviceId,
        ["deviceKey"] = settings.DeviceKey,
        ["lights"] = new JsonArray([.. settings.Lights.Select(light => (JsonNode?)light)]),
        ["groups"] = new JsonArray([.. settings.Groups.Select(group => (JsonNode?)group)]),
    };

    private static JsonArray Stored(string path) => new([.. AppConfig.Load(path).Switches.Select(s => (JsonNode)Settings(s))]);

    private static string Public(string message) => ExampleId.Replace(message, "e.g. 1000abcdef.");

    private static JsonObject Errors(IEnumerable<KeyValuePair<string, string[]>> errors) =>
        new(errors.Select(error => KeyValuePair.Create(error.Key, (JsonNode?)new JsonArray([.. error.Value.Select(m => (JsonNode?)Public(m))]))));

    private static JsonObject Result(IResult result)
    {
        var shown = new JsonObject { ["status"] = (result as IStatusCodeHttpResult)?.StatusCode };
        switch (result)
        {
            case ValidationProblem validation:
                shown["errors"] = Errors(validation.ProblemDetails.Errors);
                break;
            case ProblemHttpResult problem:
                shown["detail"] = problem.ProblemDetails.Detail;
                break;
            case IValueHttpResult { Value: { } value }:
                var warnings = (IEnumerable<string>)value.GetType().GetProperty("Warnings")!.GetValue(value)!;
                shown["warnings"] = new JsonArray([.. warnings.Select(warning => (JsonNode?)warning)]);
                break;
            default:
                throw new InvalidOperationException($"Unexpected result {result.GetType()}");
        }

        return shown;
    }
}
#endif
