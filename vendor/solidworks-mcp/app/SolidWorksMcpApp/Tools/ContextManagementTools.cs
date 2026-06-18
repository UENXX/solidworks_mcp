using ModelContextProtocol.Server;
using SolidWorksBridge.SolidWorks;
using System.ComponentModel;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace SolidWorksMcpApp.Tools;

/// <summary>
/// Result returned when setting working context.
/// </summary>
public record WorkingContextSummary(
    string ComponentName,
    string PathOnDisk,
    string DocumentType,
    int FeatureCount,
    int DimensionCount,
    List<string> Features,
    Dictionary<string, double> RecentDimensions,
    int ChildComponentCount,
    List<string> ChildComponents,
    string Message);

/// <summary>
/// MCP tools for managing scoped context and feature caching.
/// Implements "working directory" pattern for assembly/component navigation.
/// </summary>
[McpServerToolType]
public class ContextManagementTools(StaDispatcher sta, IFeatureCacheManager cacheManager, ISwConnectionManager connectionManager)
{
    [McpServerTool, Description("Sets the active working context to a component/assembly. This is the PREFERRED way to work on sub-components. It is fast and avoids opening new windows. Use this before modifying dimensions.")]
    public async Task<string> UpdateWorkingContext(
        [Description("The component name or assembly path to navigate to (e.g., 'SubAssembly_A', 'Boss-Extrude1', or a file path)")] string componentName)
    {
        if (string.IsNullOrWhiteSpace(componentName))
            throw new ArgumentException("Component name must not be empty.", nameof(componentName));

        var result = await sta.InvokeLoggedAsync(
            nameof(UpdateWorkingContext),
            new { componentName },
            () =>
            {
                var cacheNode = cacheManager.SetActiveContext(componentName, connectionManager);
                return BuildContextSummary(cacheNode);
            });

        return JsonSerializer.Serialize(result);
    }

    [McpServerTool, Description("Get the current working context and its metadata without changing it. Returns features, dimensions, and available child scopes.")]
    public async Task<string> GetCurrentWorkingContext()
    {
        var result = await sta.InvokeLoggedAsync(
            nameof(GetCurrentWorkingContext),
            null,
            () =>
            {
                var cacheNode = cacheManager.GetActiveContextNode();
                var contextKey = cacheManager.GetActiveContextKey();
                return new
                {
                    activeContext = contextKey ?? "(root)",
                    summary = BuildContextSummary(cacheNode),
                    cacheStats = GetCacheStats()
                };
            });

        return JsonSerializer.Serialize(result);
    }

    [McpServerTool, Description("Forcefully invalidates the cache for the current working context. This is a recovery tool for when the cache might be out of sync. Normal modification tools invalidate the cache automatically.")]
    public async Task<string> InvalidateWorkingContext(
        [Description("If true, also invalidate parent assembly caches. Use when a child modification affects parent bounding box or mass.")] bool invalidateParents = false)
    {
        var result = await sta.InvokeLoggedAsync(
            nameof(InvalidateWorkingContext),
            new { invalidateParents },
            () =>
            {
                cacheManager.InvalidateActiveScope(invalidateParents);
                return new
                {
                    invalidated = true,
                    activeContext = cacheManager.GetActiveContextKey() ?? "(root)",
                    message = invalidateParents
                        ? "Cache cleared for active scope and parent scopes"
                        : "Cache cleared for active scope only"
                };
            });

        return JsonSerializer.Serialize(result);
    }

    [McpServerTool, Description("Clear all cached context data across all scopes. Use sparingly; causes full re-crawl on next access.")]
    public async Task<string> ClearAllContextCache()
    {
        var result = await sta.InvokeLoggedAsync(
            nameof(ClearAllContextCache),
            null,
            () =>
            {
                cacheManager.ClearAllCache();
                return new { cleared = true, message = "All cache cleared" };
            });

        return JsonSerializer.Serialize(result);
    }

    [McpServerTool, Description("Get diagnostic statistics about the current cache state: hit rate, scope count, and last invalidation time.")]
    public async Task<string> GetCacheStatistics()
    {
        var result = await sta.InvokeLoggedAsync(
            nameof(GetCacheStatistics),
            null,
            () => GetCacheStats());

        return JsonSerializer.Serialize(result);
    }

    /// <summary>Build a human-readable summary of a cache node for the LLM.</summary>
    private WorkingContextSummary BuildContextSummary(FeatureCacheNode node)
    {
        string docType = node.DocumentType switch
        {
            1 => "Part",
            2 => "Assembly",
            3 => "Drawing",
            _ => "Unknown"
        };

        // Limit dimensions shown to top 10 most recent
        var recentDimensions = node.Dimensions
            .OrderByDescending(kv => kv.Key)
            .Take(10)
            .ToDictionary(kv => kv.Key, kv => kv.Value);

        return new WorkingContextSummary(
            ComponentName: node.Name,
            PathOnDisk: node.PathOnDisk,
            DocumentType: docType,
            FeatureCount: node.FeatureNames.Count,
            DimensionCount: node.Dimensions.Count,
            Features: node.FeatureNames.Take(20).ToList(), // Show first 20
            RecentDimensions: recentDimensions,
            ChildComponentCount: node.ChildComponentNames.Count,
            ChildComponents: node.ChildComponentNames,
            Message: $"You are in: {node.Name}. Found {node.FeatureNames.Count} features, " +
                     $"{node.Dimensions.Count} dimensions, {node.ChildComponentNames.Count} child components."
        );
    }

    /// <summary>Get cache statistics object.</summary>
    private object GetCacheStats()
    {
        var stats = cacheManager.GetStatistics();
        return new
        {
            totalCachedScopes = stats.TotalCachedScopes,
            activeContextKey = stats.ActiveContextKey ?? "(root)",
            cacheHits = stats.CacheHits,
            cacheMisses = stats.CacheMisses,
            hitRate = stats.CacheHits + stats.CacheMisses > 0
                ? Math.Round((double)stats.CacheHits / (stats.CacheHits + stats.CacheMisses) * 100, 2)
                : 0.0,
            lastInvalidation = stats.LastInvalidationTime?.ToString("O") ?? "never",
            message = $"Cache contains {stats.TotalCachedScopes} scopes with {stats.CacheHits + stats.CacheMisses} total accesses"
        };
    }
}
