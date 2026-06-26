using ModelContextProtocol.Server;
using System.ComponentModel;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;

namespace SolidWorksMcpApp.Tools;

[McpServerToolType]
public class PythonCacheTools
{
    private static readonly HttpClient s_httpClient = new();
    private readonly string _pythonServerBaseUrl;

    public PythonCacheTools()
    {
        // The Python cache server should be running locally.
        // This could be made configurable via environment variables.
        _pythonServerBaseUrl = "http://localhost:8001";
    }

    [McpServerTool, Description("Query a SolidWorks file through the Python caching layer. Performs a shallow scan if not in cache.")]
    public async Task<string> QueryCadFile(
        [Description("The full path to the SolidWorks file to query.")] string filePath)
    {
        return await PostRequestAsync("/query_cad_file", new { file_path = filePath });
    }

    [McpServerTool, Description("Assign a semantic tag (e.g., 'bracket', 'motor') to a specific CAD file path via the Python caching layer.")]
    public async Task<string> TagCadComponent(
        [Description("The full path to the SolidWorks file to tag.")] string filePath,
        [Description("The semantic tag to assign.")] string tag)
    {
        return await PostRequestAsync("/tag_cad_component", new { file_path = filePath, tag });
    }

    [McpServerTool, Description("Find a specific component in the cache based on its semantic tag via the Python caching layer.")]
    public async Task<string> SearchCadByTag(
        [Description("The semantic tag to search for.")] string tag)
    {
        return await PostRequestAsync("/search_cad_by_tag", new { tag });
    }

    private async Task<string> PostRequestAsync(string endpoint, object payload)
    {
        var url = _pythonServerBaseUrl + endpoint;
        try
        {
            var jsonPayload = JsonSerializer.Serialize(payload);
            var content = new StringContent(jsonPayload, Encoding.UTF8, "application/json");
            var response = await s_httpClient.PostAsync(url, content);

            response.EnsureSuccessStatusCode();
            return await response.Content.ReadAsStringAsync();
        }
        catch (HttpRequestException e)
        {
            return $"Error: Could not connect to the Python cache server at {url}. Is it running? Details: {e.Message}";
        }
    }
}