# Testing: Old Tools vs New Cache-Aware Tools

## Test Environment Setup

1. Start `SolidWorksMcpApp.exe` (Hub mode)
2. Connect via VS Code or Claude Desktop (Proxy mode)
3. Open a SolidWorks assembly with sub-assemblies (nested structure)

---

## Test Scenario 1: Old Tool (Direct Modification)

**Hypothesis:** Direct modification works but bypasses cache—next read is stale until explicit refresh.

### Steps

```python
# Step 1: Directly modify a feature WITHOUT UpdateWorkingContext
result = set_feature_dimension_value(
    featureName="Boss-Extrude1",
    dimensionToken="D1@Sketch1",
    value=50,
    unit="mm"
)
print("✓ Modified dimension to 50mm")

# Step 2: Try to read it back immediately via ListFeatureDimensions
dims = list_feature_dimensions("Boss-Extrude1")
# Expected: Might show OLD value (25mm) because cache invalidation only
# affects UpdateWorkingContext, not direct ListFeatureDimensions reads

# Step 3: Get cache statistics
stats = get_cache_statistics()
print(f"Cache hits: {stats['cacheHits']}, Misses: {stats['cacheMisses']}")
# Expected: Few or no hits (cache wasn't used for this workflow)
```

### Expected Behavior
- ✅ Dimension modifies successfully
- ⚠️ Cache statistics show minimal activity
- ⚠️ Stale reads possible if using old tool exclusively

---

## Test Scenario 2: New Tool (Cache-Aware Modification)

**Hypothesis:** Using UpdateWorkingContext + modification + re-navigation shows fresh data consistently.

### Steps

```python
# Step 1: Navigate to component (cache miss #1)
context1 = update_working_context("SubAssembly_A")
print(f"Found {len(context1['Features'])} features")
# Expected: Cache miss, features listed

# Step 2: Query dimension (cache hit)
dims = list_feature_dimensions("Boss-Extrude1")
width_before = [d for d in dims if "width" in d['name'].lower()][0]['value']
print(f"Width before: {width_before}mm")
# Expected: Cache hit (same scope)

# Step 3: Modify dimension (auto-invalidates cache)
result = set_feature_dimension_value(
    featureName="Boss-Extrude1",
    dimensionToken="D1@Sketch1",
    value=75,
    unit="mm"
)
print("✓ Modified dimension to 75mm, cache invalidated")

# Step 4: Re-navigate to same component (cache miss #2 - after invalidation)
context2 = update_working_context("SubAssembly_A")
print(f"Found {len(context2['Features'])} features (fresh crawl)")

# Step 5: Query dimension again (should show 75mm)
dims_fresh = list_feature_dimensions("Boss-Extrude1")
width_after = [d for d in dims_fresh if "width" in d['name'].lower()][0]['value']
print(f"Width after: {width_after}mm")
# Expected: 75mm (fresh data)

# Step 6: Check cache statistics
stats = get_cache_statistics()
print(f"Hit rate: {stats['hitRate']}%, Total accesses: {stats['cacheHits'] + stats['cacheMisses']}")
# Expected: 50% hit rate (2 hits from steps 1-2, 2 misses from steps 3-4)
```

### Expected Behavior
- ✅ First navigation: cache miss
- ✅ Second query: cache hit
- ✅ After modification: cache invalidated
- ✅ Re-navigation: fresh crawl, updated values visible
- ✅ Hit rate should be 40-50% in this scenario

---

## Test Scenario 3: Comparing Response Times

**Hypothesis:** Old tool is slightly faster (no cache overhead), new tool shows benefit on repeated accesses.

### Setup
Open assembly with 100+ features in sub-assembly.

### Old Tool Timing

```python
import time

# Warm up
list_feature_dimensions("Feature1")

# Benchmark: Direct reads (no cache)
times_old = []
for i in range(10):
    start = time.time()
    dims = list_feature_dimensions(f"Feature{i%5}")
    times_old.append(time.time() - start)

print(f"Old tool avg time: {sum(times_old)/len(times_old)*1000:.2f}ms")
# Expected: ~50-150ms per call (no cache)
```

### New Tool Timing

```python
import time

# Navigate once
update_working_context("SubAssembly_A")

# Benchmark: Repeated reads (with cache)
times_new = []
for i in range(10):
    start = time.time()
    dims = list_feature_dimensions(f"Feature{i%5}")
    times_new.append(time.time() - start)

print(f"New tool avg time: {sum(times_new)/len(times_new)*1000:.2f}ms")
# Expected: ~5-20ms per call (cache hits)
```

**Expected:** New tool 5-10x faster on repeated accesses in same scope.

---

## Test Scenario 4: Mixed Usage (Real-World)

**Hypothesis:** LLM naturally mixes both patterns; old tools still work, benefit from auto-invalidation.

### Steps

```python
# User: "I want to modify flanges in SubAssembly_A and SubAssembly_B"

# Approach 1: Old tool (direct, no context)
set_feature_dimension_value("Flange", "D1@Sketch1", 40, "mm")  # SubAssembly_A

# Switch to different assembly
set_feature_dimension_value("Flange", "D2@Sketch1", 45, "mm")  # SubAssembly_B

# Stats after mixing
stats = get_cache_statistics()
print(f"Total scopes cached: {stats['totalCachedScopes']}")
# Expected: 0-2 scopes (old tool didn't populate cache much)

# Now use new tool for final check
context_a = update_working_context("SubAssembly_A")
# Expected: Fresh crawl detects 40mm from earlier old tool modification
```

### Expected Behavior
- ✅ Old tool modifications work
- ✅ Cache invalidation triggered automatically
- ✅ New tool picks up old tool changes seamlessly
- ⚠️ Cache hit rate is low (mixed usage)

---

## Test Scenario 5: Parent Invalidation

**Hypothesis:** Modifying child invalidates parent cache entries (mass/bbox changed).

### Steps

```python
# Navigate to parent assembly
context_root = update_working_context("Assembly.sldasm")
print(f"Root scope cached")
# Cache stats: 1 scope

# Navigate to child
context_child = update_working_context("SubAssembly_A")
print(f"Child scope cached")
# Cache stats: 2 scopes

# Modify child dimension (with invalidateParents=True)
set_feature_dimension_value("Boss", "D1@Sketch1", 100, "mm")
print(f"Modified child, parent cache invalidated")
# Cache stats: 1 scope (child removed, parent also removed)

# Try to access parent
context_root_fresh = update_working_context("Assembly.sldasm")
print(f"Parent re-crawled (fresh)")
# Cache stats: cache miss (parent was invalidated)
```

### Expected Behavior
- ✅ Child modification removes both child and parent cache
- ✅ Next parent access triggers fresh crawl
- ✅ Mass properties reflect updated child geometry

---

## Quick Checklist

| Scenario | Old Tool | New Tool | Mixed | Notes |
|----------|----------|----------|-------|-------|
| **Modification works** | ✅ | ✅ | ✅ | Both function correctly |
| **Cache invalidation** | Auto | Auto | Auto | Hooked into both |
| **Fresh reads** | ⚠️ Stale | ✅ Fresh | Mixed | Depends on navigation |
| **Hit rate** | Low | High | Medium | Expected behavior |
| **Performance** | Baseline | 5-10x faster | Varies | On repeated access |
| **LLM guidance needed** | No | Yes | Yes | System prompt helps |

---

## Logging to Monitor

Check `logs/` directory for detailed traces:

```
AARNAV_YYYYMMDD_HHMMSS.txt
```

Search for:
- `UpdateWorkingContext` — Cache navigation calls
- `SetFeatureDimensionValue` — Modification calls
- `InvalidateActiveScope` — Cache invalidation events
- `PerformShallowCrawl` — Fresh crawl operations

Look for patterns:
```
[INFO] UpdateWorkingContext: "SubAssembly_A" → MISS, performing shallow crawl
[INFO] SetFeatureDimensionValue: Modified D1@Sketch1 to 50mm
[INFO] InvalidateActiveScope: Cleared "SubAssembly_A", invalidated parents
[INFO] UpdateWorkingContext: "SubAssembly_A" → MISS, performing shallow crawl (fresh)
```

---

## Success Criteria

✅ **All tests pass when:**
- Old tool modifications are immediately visible in new tool
- Cache hit rate >70% in new tool workflow
- No cache pollution or memory leaks
- Mixed usage doesn't cause conflicts
- Parent invalidation works correctly
