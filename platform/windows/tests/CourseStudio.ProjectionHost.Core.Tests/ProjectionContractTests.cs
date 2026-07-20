using System.Text.Json;
using CourseStudio.ProjectionHost.Core;

namespace CourseStudio.ProjectionHost.Core.Tests;

[TestClass]
public sealed class ProjectionContractTests
{
    private const string Fixture = """
        {
          "schemaVersion": 1,
          "commandId": "11111111-1111-4111-8111-111111111111",
          "command": "detect_displays",
          "sessionId": null,
          "expectedGeneration": 0,
          "payload": {}
        }
        """;

    [TestMethod]
    public void DetectDisplayFixtureIsStrictAndRoundTrips()
    {
        ProjectionCommand command = ProjectionJson.DeserializeCommand(Fixture);

        Assert.AreEqual(ProjectionCommandName.DetectDisplays, command.Command);
        string serialized = ProjectionJson.Serialize(command);
        Assert.IsTrue(JsonElement.DeepEquals(
            JsonDocument.Parse(Fixture).RootElement,
            JsonDocument.Parse(serialized).RootElement));

        string[] unsafeFields =
        [
            "sourcePath",
            "url",
            "token",
            "hwnd",
            "executablePath",
            "courseBody",
        ];
        foreach (string unsafeField in unsafeFields)
        {
            string unsafeJson = Fixture.Replace(
                "\"payload\": {}",
                $"\"payload\": {{}}, \"{unsafeField}\": \"unsafe\"",
                StringComparison.Ordinal);
            Assert.ThrowsExactly<JsonException>(
                () => ProjectionJson.DeserializeCommand(unsafeJson),
                $"Expected {unsafeField} to be rejected.");
        }
    }
}
