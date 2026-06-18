using System.Collections.Concurrent;
using System.Runtime.InteropServices;
using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swconst;

namespace SolidWorksBridge.SolidWorks;

/// <summary>
/// Lightweight node representing cached metadata for a component or assembly scope.
/// Holds only essential information the LLM needs to make decisions without deep recursion.
/// </summary>
public class FeatureCacheNode
{
    /// <summary>Unique name of the component (e.g., "SubAssembly_A", "Boss-Extrude1").</summary>
    public string Name { get; set; } = string.Empty;

    /// <summary>Internal SolidWorks component identifier or path.</summary>
    public string ComponentId { get; set; } = string.Empty;

    /// <summary>Full file path to the component on disk.</summary>
    public string PathOnDisk { get; set; } = string.Empty;

    /// <summary>Document type: 1=Part, 2=Assembly, 3=Drawing.</summary>
    public int DocumentType { get; set; }

    /// <summary>Cached dimensions: Name -> Value (in millimeters).</summary>
    public Dictionary<string, double> Dimensions { get; set; } = new();

    /// <summary>Cached feature names at this scope level.</summary>
    public List<string> FeatureNames { get; set; } = new();

    /// <summary>Direct child component names (next level only, not recursive).</summary>
    public List<string> ChildComponentNames { get; set; } = new();

    /// <summary>Feature type mapping: FeatureName -> FeatureType (e.g., "Boss-Extrude", "Sketch").</summary>
    public Dictionary<string, string> FeatureTypes { get; set; } = new();

    /// <summary>Timestamp when this cache entry was last populated.</summary>
    public DateTime CachedAt { get; set; }

    /// <summary>Whether this cache entry is still valid.</summary>
    public bool IsValid => DateTime.UtcNow - CachedAt < TimeSpan.FromMinutes(5);
}

/// <summary>
/// Manages scoped context caching for assembly/component navigation.
/// Implements localized "working directory" concept similar to file system `cd` command.
/// Handles cache invalidation when modifications are made.
/// </summary>
public interface IFeatureCacheManager
{
    /// <summary>Set the active working context to a specific component or assembly.</summary>
    FeatureCacheNode SetActiveContext(string componentName, ISwConnectionManager connectionManager);

    /// <summary>Get the currently active context node, fetching from cache or building on demand.</summary>
    FeatureCacheNode GetActiveContextNode();

    /// <summary>Get the active context key (component name/path).</summary>
    string GetActiveContextKey();

    /// <summary>Invalidate cache for the active scope and optionally parent scopes.</summary>
    void InvalidateActiveScope(bool invalidateParents = false);

    /// <summary>Invalidate cache for a specific component scope.</summary>
    void InvalidateScope(string componentKey);

    /// <summary>Clear all cached data.</summary>
    void ClearAllCache();

    /// <summary>Get cache statistics for debugging.</summary>
    CacheStatistics GetStatistics();
}

/// <summary>Cache statistics for monitoring and diagnostics.</summary>
public record CacheStatistics(
    int TotalCachedScopes,
    int ActiveScopeDepth,
    DateTime? LastInvalidationTime,
    string? ActiveContextKey,
    int CacheHits,
    int CacheMisses);

/// <summary>Implementation of feature cache management with scoped context tracking.</summary>
public class FeatureCacheManager : IFeatureCacheManager
{
    private readonly ConcurrentDictionary<string, FeatureCacheNode> _scopedContextCache;
    private string _activeContextKey = string.Empty;
    private DateTime? _lastInvalidationTime;
    private int _cacheHits;
    private int _cacheMisses;

    public FeatureCacheManager()
    {
        _scopedContextCache = new ConcurrentDictionary<string, FeatureCacheNode>(StringComparer.OrdinalIgnoreCase);
    }

    /// <summary>
    /// Set the active working context to a specific component.
    /// If cache miss occurs, performs shallow crawl to populate metadata.
    /// </summary>
    public FeatureCacheNode SetActiveContext(string componentName, ISwConnectionManager connectionManager)
    {
        if (string.IsNullOrWhiteSpace(componentName))
            throw new ArgumentException("Component name must not be empty.", nameof(componentName));

        connectionManager.EnsureConnected();

        _activeContextKey = componentName;

        // Try cache hit first
        if (_scopedContextCache.TryGetValue(componentName, out var cached) && cached.IsValid)
        {
            _cacheHits++;
            return cached;
        }

        // Cache miss - build on demand via shallow crawl
        _cacheMisses++;
        var node = PerformShallowCrawl(componentName, connectionManager);
        _scopedContextCache[componentName] = node;
        return node;
    }

    /// <summary>Get the currently active context node, returning cached or empty if not set.</summary>
    public FeatureCacheNode GetActiveContextNode()
    {
        if (string.IsNullOrEmpty(_activeContextKey))
            return new FeatureCacheNode { Name = "(root)", CachedAt = DateTime.UtcNow };

        if (_scopedContextCache.TryGetValue(_activeContextKey, out var node))
            return node;

        return new FeatureCacheNode { Name = _activeContextKey, CachedAt = DateTime.UtcNow };
    }

    /// <summary>Get the currently active context key.</summary>
    public string GetActiveContextKey() => _activeContextKey;

    /// <summary>
    /// Invalidate cache for the active scope after a modification.
    /// Optionally cascade up to parent assemblies.
    /// </summary>
    public void InvalidateActiveScope(bool invalidateParents = false)
    {
        if (!string.IsNullOrEmpty(_activeContextKey))
        {
            InvalidateScope(_activeContextKey);
        }

        if (invalidateParents)
        {
            // Invalidate all cached entries to ensure parent bounding boxes update
            ClearAllCache();
        }

        _lastInvalidationTime = DateTime.UtcNow;
    }

    /// <summary>Invalidate cache for a specific component scope.</summary>
    public void InvalidateScope(string componentKey)
    {
        _scopedContextCache.TryRemove(componentKey, out _);
    }

    /// <summary>Clear all cached data.</summary>
    public void ClearAllCache()
    {
        _scopedContextCache.Clear();
    }

    /// <summary>Get cache statistics for monitoring and diagnostics.</summary>
    public CacheStatistics GetStatistics()
    {
        return new CacheStatistics(
            TotalCachedScopes: _scopedContextCache.Count,
            ActiveScopeDepth: string.IsNullOrEmpty(_activeContextKey) ? 0 : 1,
            LastInvalidationTime: _lastInvalidationTime,
            ActiveContextKey: _activeContextKey,
            CacheHits: _cacheHits,
            CacheMisses: _cacheMisses);
    }

    /// <summary>
    /// Perform targeted shallow crawl of a specific component.
    /// Extracts only immediate features and direct child components (no deep recursion).
    /// </summary>
    private FeatureCacheNode PerformShallowCrawl(string componentName, ISwConnectionManager connectionManager)
    {
        var node = new FeatureCacheNode
        {
            Name = componentName,
            ComponentId = componentName,
            CachedAt = DateTime.UtcNow
        };

        try
        {
            // Get active document and locate the target component
            var swApp = connectionManager.SwApp;
            if (swApp == null)
            {
                node.PathOnDisk = "(SolidWorks not connected)";
                return node;
            }

            var activeDoc = swApp.IActiveDoc2;
            if (activeDoc == null)
            {
                node.PathOnDisk = "(no active document)";
                return node;
            }

            // Cast to ModelDoc2 for methods that expect the class type
            var modelDoc = (ModelDoc2)activeDoc;

            // Use dynamic to resolve the COM GetType() method, not the C# Object.GetType()
            node.DocumentType = ((dynamic)activeDoc).GetType();
            node.PathOnDisk = modelDoc.GetPathName() ?? "(unsaved)";

            // If this is an assembly, retrieve component directly
            if (activeDoc is AssemblyDoc assemblyDoc)
            {
                Component2? targetComponent = null;

                // Try direct component lookup by name
                try
                {
                    targetComponent = (Component2?)assemblyDoc.GetComponentByName(componentName);
                }
                catch
                {
                    // Fallback: componentName might be a part of hierarchy path
                }

                if (targetComponent != null)
                {
                    ExtractComponentMetadata(targetComponent, node);
                    ExtractImmediateFeaturesFromComponent(targetComponent, node);
                    ExtractImmediateChildComponents(targetComponent, node);
                }
                else
                {
                    // If component not found as child, treat active doc as the scope
                    ExtractDocumentRootMetadata(modelDoc, node);
                    ExtractImmediateFeaturesFromDocument(modelDoc, node);
                }
            }
            else
            {
                // For part documents, extract features from the part itself
                ExtractDocumentRootMetadata(modelDoc, node);
                ExtractImmediateFeaturesFromDocument(modelDoc, node);
            }
        }
        catch (COMException ex)
        {
            node.PathOnDisk = $"(Error: {ex.Message})";
        }
        catch (Exception ex)
        {
            node.PathOnDisk = $"(Unexpected error: {ex.Message})";
        }

        return node;
    }

    /// <summary>Extract metadata from a component object.</summary>
    private void ExtractComponentMetadata(Component2 component, FeatureCacheNode node)
    {
        try
        {
            node.Name = component.Name2 ?? node.Name;
            node.ComponentId = component.GetID().ToString();
            node.PathOnDisk = component.GetPathName() ?? "(unsaved)";

            // Get component's document if possible
            var componentDoc = component.GetModelDoc2();
            if (componentDoc != null)
            {
                // Use dynamic to resolve the COM GetType() method, not the C# Object.GetType()
                node.DocumentType = ((dynamic)componentDoc).GetType();
            }
        }
        catch
        {
            // Silently continue if metadata extraction fails
        }
    }

    /// <summary>Extract metadata from a document at root scope.</summary>
    private void ExtractDocumentRootMetadata(ModelDoc2 document, FeatureCacheNode node)
    {
        try
        {
            node.PathOnDisk = document.GetPathName() ?? "(unsaved)";
            // Use dynamic to resolve the COM GetType() method, not the C# Object.GetType()
            node.DocumentType = ((dynamic)document).GetType();
        }
        catch
        {
            // Silently continue
        }
    }

    /// <summary>Extract immediate features (direct children) from a Component2 scope.</summary>
    private void ExtractImmediateFeaturesFromComponent(Component2 component, FeatureCacheNode node)
    {
        try
        {
            // Get the component's direct features via its owning document
            var componentDoc = component.GetModelDoc2();
            if (componentDoc != null)
            {
                ExtractImmediateFeaturesFromDocument((ModelDoc2)componentDoc, node);
            }
        }
        catch
        {
            // Silently continue if feature extraction fails
        }
    }

    /// <summary>Extract immediate features from a document.</summary>
    private void ExtractImmediateFeaturesFromDocument(ModelDoc2 document, FeatureCacheNode node)
    {
        try
        {
            var feature = document.FirstFeature() as Feature;
            int featureIndex = 0;

            while (feature != null && featureIndex < 200)
            {
                try
                {
                    string? featureName = GetFeatureName(feature);
                    if (!string.IsNullOrEmpty(featureName))
                    {
                        node.FeatureNames.Add(featureName);
                        string? typeName = feature.GetTypeName2();
                        node.FeatureTypes[featureName] = typeName ?? "Unknown"; // Using GetTypeName2 is generally more reliable.
                    }

                    feature = feature.GetNextFeature() as Feature;
                    featureIndex++;
                }
                catch
                {
                    feature = feature?.GetNextFeature() as Feature;
                }
            }
        }
        catch
        {
            // Silently continue if feature iteration fails
        }
    }

    /// <summary>
    /// Safely get the feature name for selection using the out-parameter overload.
    /// </summary>
    private static string? GetFeatureName(Feature feature)
    {
        try
        {
            string selectionType;
            return feature.GetNameForSelection(out selectionType);
        }
        catch
        {
            return null;
        }
    }

    /// <summary>Extract immediate child component names (direct children only, not recursive).</summary>
    private void ExtractImmediateChildComponents(Component2 parentComponent, FeatureCacheNode node)
    {
        try
        {   // This pattern is more robust for handling the object array returned by the COM API.
            var children = parentComponent.GetChildren() as object[] ?? Array.Empty<object>();
            foreach (var child in children.OfType<IComponent2>())
            {
                try
                {
                    string childName = child.Name2 ?? string.Empty;
                    if (!string.IsNullOrEmpty(childName) && !node.ChildComponentNames.Contains(childName))
                    {
                        node.ChildComponentNames.Add(childName);
                    }
                }
                catch
                {
                    // Silently skip problematic children
                }
            }
        }
        catch
        {
            // Silently continue if child extraction fails
        }
    }
}

/// <summary>Null implementation of cache manager for testing or when caching is disabled.</summary>
public class NullFeatureCacheManager : IFeatureCacheManager
{
    public FeatureCacheNode SetActiveContext(string componentName, ISwConnectionManager connectionManager)
    {
        return new FeatureCacheNode { Name = componentName, CachedAt = DateTime.UtcNow };
    }

    public FeatureCacheNode GetActiveContextNode()
    {
        return new FeatureCacheNode { Name = "(no context)", CachedAt = DateTime.UtcNow };
    }

    public string GetActiveContextKey() => string.Empty;

    public void InvalidateActiveScope(bool invalidateParents = false) { }

    public void InvalidateScope(string componentKey) { }

    public void ClearAllCache() { }

    public CacheStatistics GetStatistics()
    {
        return new CacheStatistics(0, 0, null, null, 0, 0);
    }
}