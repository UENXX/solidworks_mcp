using ModelContextProtocol.Server;
using SolidWorksBridge.SolidWorks;
using System.ComponentModel;
using System.Text.Json;

namespace SolidWorksMcpApp.Tools;

[McpServerToolType]
public class AssemblyTools(StaDispatcher sta, IAssemblyService assembly)
{
    [McpServerTool, Description("Traverse the active assembly's visible FeatureManager tree, including feature nodes inside child parts and nested subassemblies, without opening every child document. Use this when the user asks to inspect, browse, or understand features inside assembly child components.")]
    public async Task<string> TraverseAssemblyChildFeatureTrees()
    {
        var result = await sta.InvokeLoggedAsync(nameof(TraverseAssemblyChildFeatureTrees), null, assembly.TraverseAssemblyFeatureTrees);
        return JsonSerializer.Serialize(result);
    }

    [McpServerTool, Description("Search for a feature inside the active assembly and its child part/subassembly FeatureManager trees without opening child documents. This is the preferred first tool for requests like 'find a feature in an assembly child part' or 'locate the part that owns this feature'. Returns componentPath and hierarchyPath so the user can confirm the target before editing.")]
    public async Task<string> SearchAssemblyChildFeatures(
        [Description("Feature name, sketch name, component name, or FeatureManager tree text to search inside the active assembly's child feature trees.")] string query,
        [Description("When true, only exact text matches are returned.")] bool exactNameOnly = false,
        [Description("Maximum number of ranked matches to return. Defaults to 200 so large assemblies expose enough feature candidates in content.json.")] int maxResults = 200)
    {
        var payload = new { query, exactNameOnly, maxResults };
        var result = await sta.InvokeLoggedAsync(nameof(SearchAssemblyChildFeatures), payload, () => assembly.SearchAssemblyFeatureTrees(query, exactNameOnly, maxResults));
        return JsonSerializer.Serialize(result);
    }

    [McpServerTool, Description("Open one user-confirmed child part or subassembly source document for editing after SearchAssemblyChildFeatures identifies the owning component. Use only after the user confirms the returned componentPath/hierarchyPath is the intended target.")]
    public async Task<string> OpenAssemblyChildComponentForEditing(
        [Description("Exact confirmed component source file path returned by SearchAssemblyChildFeatures.")] string componentPath,
        [Description("Optional confirmed hierarchy path returned by SearchAssemblyChildFeatures to disambiguate reused parts.")] string? hierarchyPath = null)
    {
        var payload = new { componentPath, hierarchyPath };
        var result = await sta.InvokeLoggedAsync(nameof(OpenAssemblyChildComponentForEditing), payload, () => assembly.OpenComponentForEditing(componentPath, hierarchyPath));
        return JsonSerializer.Serialize(result);
    }

}
