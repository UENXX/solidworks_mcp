# SolidWorks MCP Feature Cache Architecture

## Overview

The **Feature Cache Manager** implements a localized, scoped context caching system that prevents the LLM from blindly crawling the entire assembly tree. Instead, it provides a "working directory" pattern similar to file system navigation (`cd` command), enabling efficient parameter discovery and modification.

## Architecture Components

### Phase 1: Data Structure (`FeatureCacheNode`)

Located in: [FeatureCacheManager.cs](../../bridge/SolidWorksBridge/SolidWorks/FeatureCacheManager.cs)

```csharp
public class FeatureCacheNode
{
    public string Name { get; set; }                           // Component name
    public string ComponentId { get; set; }                    // Internal ID
    public string PathOnDisk { get; set; }                     // File path
    public int DocumentType { get; set; }                      // 1=Part, 2=Assembly
    public Dictionary<string, double> Dimensions { get; set; } // D1@Sketch -> mm value
    public List<string> FeatureNames { get; set; }             // ["Boss-Extrude1", "Sketch1"]
    public List<string> ChildComponentNames { get; set; }      // ["Sub_A-1", "Part_B-1"]
    public Dictionary<string, string> FeatureTypes { get; set; } // Feature name -> type
    public DateTime CachedAt { get; set; }                      // Timestamp (5-min validity)
}
```

**Purpose:** Holds only essential metadata needed by the LLM—no deep recursion, no unnecessary data.

### Phase 2: State Manager (`FeatureCacheManager`)

Maintains a scoped context dictionary:

```csharp
private Dictionary<string, FeatureCacheNode> _scopedContextCache;
private string _activeContextKey = string.Empty;
```

**Key Methods:**

| Method | Purpose |
|--------|---------|
| `SetActiveContext(componentName)` | Navigate to a component (cache hit/miss on first access) |
| `GetActiveContextNode()` | Get current scope without changing it |
| `InvalidateActiveScope(invalidateParents)` | Clear cache after modifications |
| `GetStatistics()` | Monitor hit rate and scope count |

### Phase 3: MCP Tools (`ContextManagementTools`)

Located in: [ContextManagementTools.cs](../../app/SolidWorksMcpApp/Tools/ContextManagementTools.cs)

Exposes four tools to the LLM:

#### 1. `UpdateWorkingContext`
```
Input: componentName (e.g., "SubAssembly_A")
Output: WorkingContextSummary
  - Feature list
  - Dimension samples
  - Child components
  - Message: "You are in: SubAssembly_A. Found X features, Y dimensions..."
```

**Behavior:**
- If cache hit: returns instantly
- If cache miss: performs shallow crawl, populates node, returns summary

#### 2. `GetCurrentWorkingContext`
```
Input: (none)
Output: Current scope summary + cache statistics
```

**Use case:** LLM confirms its current position without navigation.

#### 3. `InvalidateWorkingContext`
```
Input: invalidateParents (bool)
Output: { invalidated: true, message: "..." }
```

**Use case:** After modifying a dimension, LLM calls this to ensure fresh data on next read.

#### 4. `GetCacheStatistics`
```
Output: { totalCachedScopes, cacheHits, cacheMisses, hitRate, activeContext }
```

**Use case:** Monitor cache effectiveness during development.

### Phase 4: Cache Invalidation Hooks

**Automatic Invalidation Triggered By:**

1. **`SetFeatureDimensionValue`** (FeatureDimensionTools)
   - Modifies dimension → calls `InvalidateActiveScope(invalidateParents=true)`
   - Clears both current scope and parent assemblies

2. **`EditAssemblyChildDimension`** (FeatureDimensionTools)
   - Modifies child component → calls `InvalidateScope(componentName)`
   - Also invalidates parents

3. **`UpsertGlobalVariable`** (EquationTools)
   - Creates/updates variable → calls `InvalidateActiveScope(invalidateParents=true)`

4. **`BindSelectedDimensionToGlobalVariable`** (EquationTools)
   - Binds dimension to variable → calls `InvalidateActiveScope(invalidateParents=true)`

**Invalidation Strategy:**
- **Direct modification:** Remove cache for current scope only
- **With parent cascade:** Also remove parent cache entries (mass properties may change)
- **Lazy re-fetch:** Next access rebuilds cache from fresh SolidWorks API data

## Workflow Example: LLM Changing a Sub-Assembly Parameter

### Scenario
*"I want to change the width of the flange in SubAssembly_A to 50mm"*

### Step-by-Step

**1. LLM discovers context:**
```
Tool: UpdateWorkingContext
Input: { componentName: "SubAssembly_A" }
```

Backend:
- Checks cache: `_scopedContextCache["SubAssembly_A"]` → MISS
- Performs **shallow crawl**:
  - Gets SubAssembly_A directly via `GetComponentByName()`
  - Iterates immediate features only (no recursion)
  - Extracts dimension names: `["D1@Sketch1", "D2@Sketch2"]`
  - Lists immediate child parts: `["Plate_A-1", "Connector_B-1"]`
- Populates node, stores in cache
- Returns summary

**2. LLM lists available features:**
```
Tool: ListFeatureDimensions
Input: { featureName: "Boss-Extrude1" }
Output: [
  { name: "Width", token: "D3@Sketch1", value: 25 },
  { name: "Height", token: "D4@Sketch1", value: 10 }
]
```

**3. LLM modifies the dimension:**
```
Tool: SetFeatureDimensionValue
Input: { 
  featureName: "Boss-Extrude1",
  dimensionToken: "D3@Sketch1",
  value: 50,
  unit: "mm"
}
```

Backend:
- Updates SolidWorks dimension
- Calls `doc.EditRebuild3()` → geometry updates
- **Immediately calls `InvalidateActiveScope(invalidateParents=true)`**
  - Removes `_scopedContextCache["SubAssembly_A"]`
  - Removes parent assembly cache entries
- Returns success

**4. LLM re-queries the same scope:**
```
Tool: UpdateWorkingContext
Input: { componentName: "SubAssembly_A" }
```

Backend:
- Checks cache: `_scopedContextCache["SubAssembly_A"]` → MISS
- Performs **fresh shallow crawl**
- Detects new dimension value: 50mm
- Returns updated summary

## Targeted Shallow Crawling Algorithm

When a cache miss occurs, the system executes a **shallow crawl** that:

1. **Locates target component directly** (not traversal):
   ```csharp
   Component2 swComp = (Component2)((AssemblyDoc)swActiveDoc)
       .GetComponentByName(componentName);
   ```

2. **Extracts immediate features only**:
   ```csharp
   Feature feature = swComp_ModelDoc.IFirstFeature();
   while (feature != null && featureIndex < 200) // Safety limit
   {
       // Extract name, type, dimensions
       // Do NOT recurse into child features
       feature = feature.IGetNextFeature();
   }
   ```

3. **Extracts direct child components** (one level only):
   ```csharp
   var children = parentComponent.GetChildren();
   foreach (Component2 child in children)
   {
       // Add to ChildComponentNames
       // Do NOT descend further
   }
   ```

4. **Caches result** with 5-minute TTL:
   ```csharp
   _scopedContextCache[componentName] = node;
   ```

**Key Benefit:** Avoids O(n) deep traversal; O(1) direct lookup + O(m) local iteration where m = features in scope.

## Cache Hit/Miss Ratio Monitoring

Use `GetCacheStatistics()` to monitor effectiveness:

```json
{
  "totalCachedScopes": 3,
  "cacheHits": 42,
  "cacheMisses": 5,
  "hitRate": 89.36,
  "activeContext": "SubAssembly_A",
  "lastInvalidation": "2026-06-11T12:34:56Z",
  "message": "Cache contains 3 scopes with 47 total accesses"
}
```

**Expected Ratios:**
- **First navigation:** 0% hit rate (all misses)
- **Stable workflow:** 75-90% hit rate (few modifications)
- **Heavy modification:** 50-70% hit rate (cache invalidated frequently)

## LLM System Prompt Integration

Add to the LLM's system instructions:

```
## Working with Component Scopes

Before modifying parameters deep inside an assembly, use the UpdateWorkingContext tool to "navigate" there, similar to `cd` in a file system:

1. Call UpdateWorkingContext(componentName) to enter a scope
2. Query features and dimensions within that scope
3. Make modifications
4. The cache is automatically invalidated, so the next read will be fresh

Example workflow:
- UpdateWorkingContext("SubAssembly_A")  # Enter the scope
- ListFeatureDimensions("Boss-Extrude1")  # See what's available
- SetFeatureDimensionValue(...)  # Modify
- UpdateWorkingContext("SubAssembly_A")  # Re-enter to see updated values

Do NOT attempt to modify features in child assemblies without first navigating to them.
```

## Design Rationale

### Why Not Just Cache Everything?
- **Scalability:** Deep assemblies with 100s of parts would cache explosively
- **Staleness:** After one modification, entire cache becomes invalid
- **Memory:** Unnecessary metadata consumes RAM

### Why Scoped Contexts?
- **LLM comfort:** "Working directory" is a familiar paradigm
- **Incremental validity:** Only active scope is invalidated on modification
- **Explicit navigation:** Prevents accidental parameter changes in unexpected scopes

### Why Shallow Crawling?
- **Efficiency:** Avoids O(n) full tree traversal on every cache miss
- **Freshness:** Quick local refresh mirrors file system `ls` behavior
- **Correctness:** Immediate feature list matches user's current SolidWorks view

## Testing & Validation

### Unit Tests
See [FeatureCacheManagerTests.cs](../../bridge/SolidWorksBridge.Tests/SolidWorks/FeatureCacheManagerTests.cs) (to be added).

### Integration Testing
```powershell
# Start SolidWorksMcpApp.exe
# Connect via VS Code or Claude Desktop
# In console or chat:
# 1. Call UpdateWorkingContext("Part1")
# 2. Check returned summary
# 3. Call SetFeatureDimensionValue(...)
# 4. Call GetCacheStatistics() -> hitRate should change
# 5. Call UpdateWorkingContext("Part1") again -> should see fresh cache miss
```

## Troubleshooting

### High Cache Miss Rate
- **Cause:** Too many dimension modifications
- **Solution:** This is normal; cache invalidation is working as intended

### Stale Data in GetCurrentWorkingContext
- **Cause:** Haven't navigated away and back after modification
- **Solution:** Call `InvalidateWorkingContext(invalidateParents=true)` explicitly

### "Component Not Found" Error
- **Cause:** Typo in component name
- **Solution:** Use `UpdateWorkingContext` with a different scope to list children

## Future Enhancements

1. **Hierarchical Cache Navigation:** `cd ..` to parent assembly
2. **Cache Warming:** Pre-populate cache on document open
3. **Persistent Cache:** Cache to disk between sessions
4. **Smart Invalidation:** Only invalidate affected sub-scopes (dependency graph)
5. **Performance Metrics:** Log cache effectiveness per session
