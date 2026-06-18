# Feature Cache System - Quick Reference Guide

### For LLM Developers

### The Three Rules of Scoped Editing

1.  **Always `UpdateWorkingContext` First**: Before editing a sub-component, **always** call `UpdateWorkingContext(componentName)`. This is your `cd` command. Do not use `open_...` for simple dimension changes.
2.  **Trust the Cache Invalidation**: After you modify a dimension or feature, the cache is automatically cleared. The next time you read data (e.g., with `GetCurrentWorkingContext`), it will be fresh.
3.  **Verify in Context**: After a change, call `GetCurrentWorkingContext()` to see the updated dimension values. This is faster and safer than re-opening files.

---

## Tool Reference

### UpdateWorkingContext
**Like `cd` in a file system**

```python
# Navigate to a sub-assembly
result = update_working_context("SubAssembly_A")
# Returns: feature names, dimensions, child components

# Navigate to root
result = update_working_context("Assembly.sldasm")
```

### GetCurrentWorkingContext
**Like `pwd` in a file system**

```python
# Check where you are without moving
current = get_current_working_context()
# Returns: current scope summary + cache stats
```

### InvalidateWorkingContext
**Force cache refresh**

```python
# After complex changes, force fresh cache read
invalidate_working_context(invalidateParents=True)

# Next call to GetCurrentWorkingContext will crawl fresh data
```

### GetCacheStatistics
**Monitor performance**

```python
stats = get_cache_statistics()
# {
#   "totalCachedScopes": 3,
#   "cacheHits": 42,
#   "cacheMisses": 5,
#   "hitRate": 89.36,
#   "activeContext": "SubAssembly_A"
# }
```

---

## Workflow Templates

### Template 1: Modify One Feature in a Sub-Assembly

```python
# Step 1: Navigate
context = update_working_context("SubAssembly_A")
print(f"Features available: {context['Features']}")

# Step 2: Get dimension token
dims = list_feature_dimensions("Boss-Extrude1")
width_dim = [d for d in dims if "width" in d['name'].lower()][0]

# Step 3: Modify
result = set_feature_dimension_value(
    featureName="Boss-Extrude1",
    dimensionToken=width_dim['dimensionToken'],
    value=50,
    unit="mm"
)

# Done! Cache auto-invalidated. New values will be fresh next time.
```

### Template 2: Parameterize Multiple Features

```python
# Step 1: Create global variable
upsert_global_variable("WIDTH", "50mm")

# Step 2: Bind features to it
upsert_global_variable_and_bind_feature_dimension_by_description(
    featureName="Boss-Extrude1",
    variableName="WIDTH",
    expression="WIDTH",
    dimensionDescription="width"
)

# Step 3: Enter child sub-assembly and bind there too
context = update_working_context("SubAssembly_A")
upsert_global_variable_and_bind_feature_dimension_by_description(
    featureName="Pad1",
    variableName="WIDTH",
    expression="WIDTH",
    dimensionDescription="width"
)

# Now change WIDTH once, affects all linked features
upsert_global_variable("WIDTH", "75mm")
```

### Template 3: Inspect Hierarchy

```python
# Start at root
root = update_working_context("Assembly.sldasm")
print(f"Root has {len(root['ChildComponents'])} sub-parts")

# Descend into each
for child in root['ChildComponents']:
    child_context = update_working_context(child)
    print(f"  {child}: {child_context['FeatureCount']} features")
```

### Template 4: Diagnose Cache Health

```python
before_stat = get_cache_statistics()

# Do some work...
set_feature_dimension_value(...)
update_working_context("SubAssembly_B")
set_feature_dimension_value(...)

after_stats = get_cache_statistics()

print(f"Cache hits: {after_stats['cacheHits']} vs {before_stats['cacheHits']}")
print(f"Hit rate: {after_stats['hitRate']}%")
```

---

## Common Mistakes & Fixes

| Mistake | Fix |
|---------|-----|
| "Dimension token not found" | Use `list_feature_dimensions()` to find exact token |
| Stale data in response | Call `update_working_context(componentName)` again |
| "Component not found" error | Typo in name; use `update_working_context(parent)` to list children |
| Cache hit rate is 0% | Normal after modifications; invalidation is working |
| Changes don't appear | Modification succeeded; next read will be fresh |

---

## Performance Tips

1. **Reuse contexts:** Stay in same scope for multiple edits (cache hits)
2. **Batch modifications:** Do all changes to one feature, then move on
3. **Navigate once per scope:** Don't `cd` in and out repeatedly
4. **Monitor with `GetCacheStatistics()`:** Aim for 70%+ hit rate in stable workflows

---

## Under the Hood (Optional Reading)

### How Cache Invalidation Works

```
LLM: SetFeatureDimensionValue(...)
  ↓
Bridge: Update dimension in SolidWorks
  ↓
Bridge: Call EditRebuild3()
  ↓
Bridge: Call InvalidateActiveScope(invalidateParents=True)
  ↓
Cache: Remove current scope
  ↓
Cache: Remove parent scopes (mass/bbox may have changed)
  ↓
Return success
  ↓
LLM: Next read will trigger fresh crawl
```

### Why Shallow Crawling is Fast

Instead of:
```
Traverse entire tree
├── Assembly
│   ├── SubAssembly_A
│   │   ├── SubAssembly_A1
│   │   │   └── Part_X (recursion continues...)
│   ├── SubAssembly_B
│   ...
```

We do:
```
GetComponentByName("SubAssembly_A")  # Direct lookup
├── Features: [Boss1, Sketch1, ...]  # Iterate 1 level
├── Children: [Sub_A1, Part_X]       # List direct children only
└── Done!                             # No recursion
```

Time: **O(1) lookup + O(m) iteration** instead of **O(n) traversal**

---

## Integration with LLM Prompts

Add this to your system prompt:

```
You have access to a "working directory" system for SolidWorks:

- UpdateWorkingContext(componentName): Navigate into a scope
- GetCurrentWorkingContext(): See current scope without moving
- InvalidateWorkingContext(): Force refresh after changes
- GetCacheStatistics(): Monitor cache performance

Always call UpdateWorkingContext before modifying parameters in a sub-assembly.
Cache is automatically invalidated after each modification, so next reads will be fresh.

Example: "I want to change the flange width in SubAssembly_A to 50mm"
→ UpdateWorkingContext("SubAssembly_A")
→ list_feature_dimensions("Flange")
→ set_feature_dimension_value(...)
→ Done! Next read will see 50mm.
```
