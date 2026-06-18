#!/usr/bin/env python3
"""
Quick test runner for old vs new SolidWorks MCP cache tools.

Run this in VS Code console or Claude Desktop to automatically test scenarios.
"""

import json
import time

# Test Scenario 1: Old Tool (Direct)
def test_scenario_1_old_tool():
    print("\n" + "="*60)
    print("TEST SCENARIO 1: Old Tool (Direct Modification)")
    print("="*60)
    
    print("\n[1] Directly modify feature WITHOUT UpdateWorkingContext")
    result = set_feature_dimension_value(
        featureName="Boss-Extrude1",
        dimensionToken="D1@Sketch1",
        value=50,
        unit="mm"
    )
    print(f"✓ Modified: {json.loads(result)}")
    
    print("\n[2] List dimensions immediately (may be stale)")
    dims = list_feature_dimensions("Boss-Extrude1")
    print(f"✓ Dimensions: {json.loads(dims)[:2]}")  # Show first 2
    
    print("\n[3] Check cache statistics (expect low activity)")
    stats = get_cache_statistics()
    stats_obj = json.loads(stats)
    print(f"✓ Cache hits: {stats_obj['cacheHits']}, Misses: {stats_obj['cacheMisses']}, Hit rate: {stats_obj['hitRate']}%")
    
    return stats_obj


# Test Scenario 2: New Tool (Cache-Aware)
def test_scenario_2_new_tool():
    print("\n" + "="*60)
    print("TEST SCENARIO 2: New Tool (UpdateWorkingContext)")
    print("="*60)
    
    print("\n[1] Navigate to component (cache miss)")
    context1 = update_working_context("SubAssembly_A")
    context1_obj = json.loads(context1)
    print(f"✓ Entered: {context1_obj['ComponentName']}")
    print(f"  - Features: {len(context1_obj['Features'])}")
    print(f"  - Dimensions: {len(context1_obj['RecentDimensions'])}")
    print(f"  - Children: {len(context1_obj['ChildComponents'])}")
    
    print("\n[2] Query dimension (cache hit)")
    dims = list_feature_dimensions("Boss-Extrude1")
    dims_obj = json.loads(dims)
    width_before = dims_obj[0]['Value'] if dims_obj else None
    print(f"✓ Width before: {width_before}mm")
    
    print("\n[3] Modify dimension (auto-invalidates cache)")
    result = set_feature_dimension_value(
        featureName="Boss-Extrude1",
        dimensionToken="D1@Sketch1",
        value=75,
        unit="mm"
    )
    print(f"✓ Modified to 75mm, cache auto-invalidated")
    
    print("\n[4] Re-navigate (cache miss after invalidation)")
    context2 = update_working_context("SubAssembly_A")
    context2_obj = json.loads(context2)
    print(f"✓ Re-entered: {context2_obj['ComponentName']} (fresh crawl)")
    
    print("\n[5] Query dimension again (should show fresh value)")
    dims_fresh = list_feature_dimensions("Boss-Extrude1")
    dims_fresh_obj = json.loads(dims_fresh)
    width_after = dims_fresh_obj[0]['Value'] if dims_fresh_obj else None
    print(f"✓ Width after: {width_after}mm")
    
    print("\n[6] Check cache statistics")
    stats = get_cache_statistics()
    stats_obj = json.loads(stats)
    print(f"✓ Cache hits: {stats_obj['cacheHits']}, Misses: {stats_obj['cacheMisses']}, Hit rate: {stats_obj['hitRate']}%")
    
    return stats_obj


# Test Scenario 3: Mixed Usage
def test_scenario_3_mixed_usage():
    print("\n" + "="*60)
    print("TEST SCENARIO 3: Mixed Usage (Old + New)")
    print("="*60)
    
    print("\n[1] Use old tool (direct modification)")
    set_feature_dimension_value(
        featureName="Flange",
        dimensionToken="D2@Sketch1",
        value=40,
        unit="mm"
    )
    print(f"✓ Old tool modified Flange to 40mm")
    
    print("\n[2] Use new tool to verify")
    context = update_working_context("SubAssembly_A")
    context_obj = json.loads(context)
    print(f"✓ New tool navigated to {context_obj['ComponentName']}")
    
    print("\n[3] Query updated value")
    dims = list_feature_dimensions("Flange")
    dims_obj = json.loads(dims)
    updated_value = dims_obj[0]['Value'] if dims_obj else None
    print(f"✓ Old tool modification visible in new tool: {updated_value}mm")
    
    print("\n[4] Check cache stats")
    stats = get_cache_statistics()
    stats_obj = json.loads(stats)
    print(f"✓ Total cached scopes: {stats_obj['totalCachedScopes']}")
    
    return stats_obj


# Test Scenario 4: Parent Invalidation
def test_scenario_4_parent_invalidation():
    print("\n" + "="*60)
    print("TEST SCENARIO 4: Parent Invalidation")
    print("="*60)
    
    print("\n[1] Navigate to parent")
    context_root = update_working_context("Assembly.sldasm")
    stats1 = json.loads(get_cache_statistics())
    print(f"✓ Parent scope cached. Total scopes: {stats1['totalCachedScopes']}")
    
    print("\n[2] Navigate to child")
    context_child = update_working_context("SubAssembly_A")
    stats2 = json.loads(get_cache_statistics())
    print(f"✓ Child scope cached. Total scopes: {stats2['totalCachedScopes']}")
    
    print("\n[3] Modify child (with parent invalidation)")
    set_feature_dimension_value(
        featureName="Boss",
        dimensionToken="D1@Sketch1",
        value=100,
        unit="mm"
    )
    stats3 = json.loads(get_cache_statistics())
    print(f"✓ Modified child, parent invalidated. Total scopes: {stats3['totalCachedScopes']}")
    
    print("\n[4] Re-navigate parent (triggers fresh crawl)")
    context_root_fresh = update_working_context("Assembly.sldasm")
    stats4 = json.loads(get_cache_statistics())
    print(f"✓ Parent re-crawled (fresh). Total scopes: {stats4['totalCachedScopes']}")
    
    return stats4


# Test Scenario 5: Performance Comparison
def test_scenario_5_performance():
    print("\n" + "="*60)
    print("TEST SCENARIO 5: Performance Comparison")
    print("="*60)
    
    print("\n[1] Warm up cache")
    update_working_context("SubAssembly_A")
    
    print("\n[2] Repeated reads (measuring response time)")
    times = []
    for i in range(5):
        start = time.time()
        list_feature_dimensions("Boss-Extrude1")
        elapsed = (time.time() - start) * 1000
        times.append(elapsed)
    
    avg_time = sum(times) / len(times)
    print(f"✓ Average response time: {avg_time:.2f}ms (expected: 5-20ms with cache)")
    
    print("\n[3] Clear cache and repeat (no cache)")
    clear_all_context_cache()
    times_no_cache = []
    for i in range(3):
        start = time.time()
        list_feature_dimensions("Boss-Extrude1")
        elapsed = (time.time() - start) * 1000
        times_no_cache.append(elapsed)
    
    avg_no_cache = sum(times_no_cache) / len(times_no_cache)
    print(f"✓ Average response time (no cache): {avg_no_cache:.2f}ms (expected: 50-150ms)")
    
    speedup = avg_no_cache / avg_time if avg_time > 0 else 0
    print(f"\n✓ Cache speedup: {speedup:.1f}x faster")
    
    return {"with_cache": avg_time, "without_cache": avg_no_cache, "speedup": speedup}


# Main test runner
def run_all_tests():
    print("\n" + "#"*60)
    print("# SolidWorks MCP Cache System - Comprehensive Test Suite")
    print("#"*60)
    
    results = {}
    
    try:
        results["scenario_1"] = test_scenario_1_old_tool()
        print("\n✅ SCENARIO 1 PASSED")
    except Exception as e:
        print(f"\n❌ SCENARIO 1 FAILED: {e}")
    
    try:
        results["scenario_2"] = test_scenario_2_new_tool()
        print("\n✅ SCENARIO 2 PASSED")
    except Exception as e:
        print(f"\n❌ SCENARIO 2 FAILED: {e}")
    
    try:
        results["scenario_3"] = test_scenario_3_mixed_usage()
        print("\n✅ SCENARIO 3 PASSED")
    except Exception as e:
        print(f"\n❌ SCENARIO 3 FAILED: {e}")
    
    try:
        results["scenario_4"] = test_scenario_4_parent_invalidation()
        print("\n✅ SCENARIO 4 PASSED")
    except Exception as e:
        print(f"\n❌ SCENARIO 4 FAILED: {e}")
    
    try:
        results["scenario_5"] = test_scenario_5_performance()
        print("\n✅ SCENARIO 5 PASSED")
    except Exception as e:
        print(f"\n❌ SCENARIO 5 FAILED: {e}")
    
    print("\n" + "#"*60)
    print("# Test Summary")
    print("#"*60)
    print(f"\nResults: {json.dumps(results, indent=2, default=str)}")
    
    return results


# Run tests
if __name__ == "__main__":
    run_all_tests()
