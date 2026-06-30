using ModelContextProtocol.Server;
using SolidWorksBridge.SolidWorks;
using System.ComponentModel;
using System.Text.Json;

namespace SolidWorksMcpApp.Tools;

[McpServerToolType]
public class AssemblyEntityAnnotationTools(StaDispatcher sta, IAssemblyEntityAnnotationService annotations)
{
    [McpServerTool, Description("Export the full FeatureManager feature tree for the active SolidWorks part or assembly. For assemblies, recursively includes loaded child parts and child subassemblies, preserving component hierarchy paths and document paths. Writes feature-tree.json without screenshots or filtering.")]
    public async Task<string> ExportActiveDocumentFeatureTree(
        [Description("Output directory for feature-tree.json.")] string outputDirectory,
        [Description("When true, rebuilds feature-tree.json even if it already exists.")] bool overwrite = false,
        [Description("When true, expands management FeatureManager folders such as component folders, mates, annotations, and reference folders. Defaults to false because large assemblies can be extremely slow; child part/subassembly trees are still exported through component documents.")] bool expandManagementFeatureSubTrees = false,
        [Description("When true, expands feature subtrees recursively. Defaults to false; HasSubFeatures is still recorded and is enough for first-pass filtering.")] bool expandFeatureSubTrees = false,
        [Description("When true, enumerates features inside loaded child assembly documents too. Defaults to false; child part features are controlled by includePartFeatures.")] bool includeComponentFeatures = false,
        [Description("When true, enumerates features inside loaded child part documents. Defaults to true so recursive part feature trees are included.")] bool includePartFeatures = true,
        [Description("When true, exports only the currently active document's own FeatureManager features and does not traverse child components or subassemblies. Use this for timing/debugging active-document feature enumeration.")] bool activeDocumentOnly = false,
        [Description("Optional full document paths whose feature nodes should be skipped while still exporting their component/document address nodes. Use this for imported parts that block feature enumeration.")] string[]? skipFeatureDocumentPaths = null,
        [Description("When true, keeps appending to feature-tree-documents.jsonl even when overwrite is true. Used by retrying clients so partial document journals survive timeouts.")] bool appendDocumentJournal = false,
        [Description("When true, skips feature enumeration for imported neutral-format documents such as Open CASCADE STEP translator files while preserving their document/component nodes. Defaults to true for large assemblies.")] bool skipImportedFeatureDocuments = true,
        [Description("When true, exports feature nodes for every repeated instance of the same source document. Defaults to false so repeated parts/subassemblies keep their address nodes but only the first instance exports features.")] bool exportDuplicateSourceDocumentFeatures = false)
    {
        var result = await sta.InvokeLoggedAsync(
            nameof(ExportActiveDocumentFeatureTree),
            new { outputDirectory, overwrite, expandManagementFeatureSubTrees, expandFeatureSubTrees, includeComponentFeatures, includePartFeatures, activeDocumentOnly, skipFeatureDocumentPaths, appendDocumentJournal, skipImportedFeatureDocuments, exportDuplicateSourceDocumentFeatures },
            () => annotations.ExportActiveDocumentFeatureTree(outputDirectory, overwrite, expandManagementFeatureSubTrees, expandFeatureSubTrees, includeComponentFeatures, includePartFeatures, activeDocumentOnly, skipFeatureDocumentPaths, appendDocumentJournal, skipImportedFeatureDocuments, exportDuplicateSourceDocumentFeatures));
        return JsonSerializer.Serialize(new SolidWorksFeatureTreeExportSummary
        {
            SchemaVersion = result.SchemaVersion,
            CreatedUtc = result.CreatedUtc,
            OutputDirectory = result.OutputDirectory,
            FeatureTreePath = result.FeatureTreePath,
            DocumentJournalPath = result.DocumentJournalPath,
            ActiveDocumentTitle = result.ActiveDocumentTitle,
            ActiveDocumentPath = result.ActiveDocumentPath,
            ActiveDocumentType = result.ActiveDocumentType,
            ActiveDocumentTypeName = result.ActiveDocumentTypeName,
            DocumentCount = result.DocumentCount,
            FeatureCount = result.FeatureCount,
        });
    }

    [McpServerTool, Description("Build a reusable candidate target index for the active assembly without exporting screenshots. Traverses the active assembly plus loaded child components once and writes target-index.json so later capture calls can resolve targets by sourceIndex or targetId without re-enumerating every feature.")]
    public async Task<string> BuildActiveAssemblyEntityAnnotationTargetIndex(
        [Description("Output directory for target-index.json.")] string outputDirectory,
        [Description("Include component instance targets.")] bool includeComponents = true,
        [Description("Include active assembly and loaded child feature targets.")] bool includeFeatures = true,
        [Description("Include solid body targets when the body list is available from component documents.")] bool includeBodies = true,
        [Description("When true, only keeps targets that have a valid FeatureTypeName and filters ProfileFeature/OneBend. Defaults to true for feature annotation datasets.")] bool requireFeatureTypeName = true,
        [Description("When true, rebuilds target-index.json even if it already exists.")] bool overwrite = false)
    {
        var result = await sta.InvokeLoggedAsync(
            nameof(BuildActiveAssemblyEntityAnnotationTargetIndex),
            new { outputDirectory, includeComponents, includeFeatures, includeBodies, requireFeatureTypeName, overwrite },
            () => annotations.BuildActiveAssemblyEntityAnnotationTargetIndex(
                outputDirectory,
                includeComponents,
                includeFeatures,
                includeBodies,
                requireFeatureTypeName,
                overwrite));
        return JsonSerializer.Serialize(result);
    }

    [McpServerTool, Description("Capture one or more assembly entity annotation targets from an existing target-index.json. Prefer sourceIndex or targetId for single-target capture; this avoids rebuilding the full candidate target list in every batch.")]
    public async Task<string> CaptureActiveAssemblyEntityAnnotationTargetsFromIndex(
        [Description("Output directory containing target-index.json and receiving manifest.json plus per-entity front/top/right PNG files.")] string outputDirectory,
        [Description("Image width in pixels.")] int width = 1280,
        [Description("Image height in pixels.")] int height = 720,
        [Description("Zero-based target offset in target-index.json. Ignored when targetId or sourceIndex is supplied.")] int startIndex = 0,
        [Description("Maximum targets to capture from the index. Use 1 for the most stable long-running capture.")] int maxTargets = 1,
        [Description("Optional stable targetId from target-index.json. When supplied, captures that single target.")] string? targetId = null,
        [Description("Optional zero-based SourceIndex from target-index.json. When supplied, captures that single source target.")] int? sourceIndex = null,
        [Description("When true, reads an existing manifest.json and skips targets already captured in it.")] bool skipExistingTargets = true,
        [Description("When true, updates manifest.json after every target so timed-out requests still preserve progress.")] bool writeManifestAfterEachTarget = true,
        [Description("Maximum wall-clock seconds this tool should spend capturing before returning a partial manifest normally. 0 disables the internal budget, but the MCP client may still enforce its own request timeout.")] int maxDurationSeconds = 45,
        [Description("When true, switches capture views to a clean hidden-lines-removed display and disables perspective/RealView where possible. Defaults to false so SolidWorks shading and target color override remain visible.")] bool useCleanDisplayMode = false,
        [Description("Extra zoom-out padding applied before selecting the target and exporting PNG. Defaults to 1.35 to prioritize fitting the whole model in view.")] double capturePaddingFactor = 1.35)
    {
        var result = await sta.InvokeLoggedAsync(
            nameof(CaptureActiveAssemblyEntityAnnotationTargetsFromIndex),
            new { outputDirectory, width, height, startIndex, maxTargets, targetId, sourceIndex, skipExistingTargets, writeManifestAfterEachTarget, maxDurationSeconds, useCleanDisplayMode, capturePaddingFactor },
            () => annotations.CaptureActiveAssemblyEntityAnnotationTargetsFromIndex(
                outputDirectory,
                width,
                height,
                startIndex,
                maxTargets,
                targetId,
                sourceIndex,
                skipExistingTargets,
                writeManifestAfterEachTarget,
                maxDurationSeconds,
                useCleanDisplayMode,
                capturePaddingFactor));
        return JsonSerializer.Serialize(result);
    }

    [McpServerTool, Description("Read feature-tree-filtered-features.json, resolve each filtered feature in the active SolidWorks part/assembly, color it red, export front/top/right PNGs, then export another front/top/right set with non-target model faces made transparent. Images are written under three_views/<feature-name>.")]
    public async Task<string> CaptureFilteredFeatureTreeThreeViews(
        [Description("Path to feature-tree-filtered-features.json produced by the Python filter step.")] string filteredFeatureTreePath,
        [Description("Image width in pixels.")] int width = 1280,
        [Description("Image height in pixels.")] int height = 720,
        [Description("Zero-based SourceIndex from the filtered feature tree target list.")] int startIndex = 0,
        [Description("Maximum filtered feature targets to capture. Use 1 for stable batching.")] int maxTargets = 1,
        [Description("When true, reads three_views/three-view-manifest.json and skips targets already captured in it.")] bool skipExistingTargets = true,
        [Description("When true, updates three-view-manifest.json after every target so timed-out requests preserve progress.")] bool writeManifestAfterEachTarget = true,
        [Description("Maximum wall-clock seconds this tool should spend before returning a partial manifest normally. 0 disables the internal budget, but the MCP client may still enforce its own timeout.")] int maxDurationSeconds = 45,
        [Description("Extra zoom-out padding applied before exporting each PNG. Defaults to 1.35 to prioritize fitting the whole model in view.")] double capturePaddingFactor = 1.35,
        [Description("When true, ignores an existing three-view-manifest.json and overwrites output images as targets are processed.")] bool overwrite = false,
        [Description("Maximum non-target faces to make transparent for the transparent-context views. Defaults to 1000 to avoid excessive COM work on huge assemblies. Use 0 to skip context transparency and only switch to a clean edge display for the second view set.")] int maxTransparentFaces = 1000)
    {
        var result = await sta.InvokeLoggedAsync(
            nameof(CaptureFilteredFeatureTreeThreeViews),
            new { filteredFeatureTreePath, width, height, startIndex, maxTargets, skipExistingTargets, writeManifestAfterEachTarget, maxDurationSeconds, capturePaddingFactor, overwrite, maxTransparentFaces },
            () => annotations.CaptureFilteredFeatureTreeThreeViews(
                filteredFeatureTreePath,
                width,
                height,
                startIndex,
                maxTargets,
                skipExistingTargets,
                writeManifestAfterEachTarget,
                maxDurationSeconds,
                capturePaddingFactor,
                overwrite,
                maxTransparentFaces));
        return JsonSerializer.Serialize(result);
    }

    [McpServerTool, Description("Capture an entity-level annotation dataset for the active assembly. Traverses active assembly features plus child component instances, child feature trees, and solid bodies when available; highlights each target; exports front/top/right PNG views; and writes a manifest.json with stable target IDs and ownership paths.")]
    public async Task<string> CaptureActiveAssemblyEntityAnnotationSet(
        [Description("Output directory for manifest.json and per-entity front/top/right PNG files.")] string outputDirectory,
        [Description("Image width in pixels.")] int width = 1280,
        [Description("Image height in pixels.")] int height = 720,
        [Description("Include component instance targets.")] bool includeComponents = true,
        [Description("Include active assembly and loaded child feature targets.")] bool includeFeatures = true,
        [Description("Include solid body targets when the body list is available from component documents.")] bool includeBodies = true,
        [Description("When true, only keeps targets that have a valid FeatureTypeName and filters ProfileFeature/OneBend. Defaults to true for feature annotation datasets.")] bool requireFeatureTypeName = true,
        [Description("Maximum targets to capture. 0 means no limit; use a small number for a dry run or batch.")] int maxTargets = 0,
        [Description("Zero-based target offset before maxTargets is applied. Use this to batch large assemblies: 0, 50, 100, ...")] int startIndex = 0,
        [Description("When true, reads an existing manifest.json and skips targets already captured in it.")] bool skipExistingTargets = true,
        [Description("When true, updates manifest.json after every target so timed-out requests still preserve progress.")] bool writeManifestAfterEachTarget = true,
        [Description("Maximum wall-clock seconds this tool should spend capturing before returning a partial manifest normally. 0 disables the internal budget, but the MCP client may still enforce its own request timeout.")] int maxDurationSeconds = 45,
        [Description("When true, switches capture views to a clean hidden-lines-removed display and disables perspective/RealView where possible. Defaults to false so SolidWorks shading and selection highlight remain visible.")] bool useCleanDisplayMode = false,
        [Description("Extra zoom-out padding applied before selecting the target and exporting PNG. Defaults to 1.35 to prioritize fitting the whole model in view.")] double capturePaddingFactor = 1.35)
    {
        var result = await sta.InvokeLoggedAsync(
            nameof(CaptureActiveAssemblyEntityAnnotationSet),
            new { outputDirectory, width, height, includeComponents, includeFeatures, includeBodies, requireFeatureTypeName, maxTargets, startIndex, skipExistingTargets, writeManifestAfterEachTarget, maxDurationSeconds, useCleanDisplayMode, capturePaddingFactor },
            () => annotations.CaptureActiveAssemblyEntityAnnotationSet(
                outputDirectory,
                width,
                height,
                includeComponents,
                includeFeatures,
                includeBodies,
                requireFeatureTypeName,
                maxTargets,
                startIndex,
                skipExistingTargets,
                writeManifestAfterEachTarget,
                maxDurationSeconds,
                useCleanDisplayMode,
                capturePaddingFactor));
        return JsonSerializer.Serialize(result);
    }

    [McpServerTool, Description("Call an OpenAI-compatible Qwen vision endpoint to classify each captured assembly entity target as directly related to overall X/Y/Z assembly dimensions. Requires DASHSCOPE_API_KEY or QWEN_API_KEY in the SolidWorksMcpApp environment. Writes dimension-annotations.json next to the manifest by default.")]
    public async Task<string> AnnotateAssemblyEntityCaptureSetWithQwen(
        [Description("Path to the manifest.json produced by CaptureActiveAssemblyEntityAnnotationSet.")] string manifestPath,
        [Description("Optional output JSON path. Defaults to dimension-annotations.json next to the manifest.")] string? outputPath = null,
        [Description("Qwen vision model name. Defaults to SOLIDWORKS_ENTITY_ANNOTATION_QWEN_MODEL, QWEN_VISION_MODEL, or qwen3.6-flash.")] string? model = null,
        [Description("OpenAI-compatible API base URL or /chat/completions endpoint. Defaults to DASHSCOPE_BASE_URL, QWEN_BASE_URL, or DashScope compatible mode.")] string? baseUrl = null,
        [Description("Maximum targets to annotate in this call. 0 means all pending targets.")] int maxTargets = 0,
        [Description("When true, discards previous annotations at outputPath and recomputes requested targets.")] bool overwrite = false,
        [Description("HTTP timeout per request in seconds.")] int timeoutSeconds = 90)
    {
        var result = await annotations.AnnotateCaptureSetWithQwenAsync(
            manifestPath,
            outputPath,
            model,
            baseUrl,
            maxTargets,
            overwrite,
            timeoutSeconds);
        return JsonSerializer.Serialize(result);
    }

    [McpServerTool, Description("Import external vision-model dimension annotations for a captured assembly entity manifest. Use this when Qwen is called outside the MCP process. Accepts either a JSON string or a file path containing an array/object of annotations and writes the normalized dimension-annotations.json index.")]
    public Task<string> ImportAssemblyEntityDimensionAnnotations(
        [Description("Path to the manifest.json produced by CaptureActiveAssemblyEntityAnnotationSet.")] string manifestPath,
        [Description("Annotation JSON string or path to a JSON file. Each entry should include targetId plus x/y/z related flags, descriptions, and identifiers.")] string annotationJsonOrFilePath,
        [Description("Optional output JSON path. Defaults to dimension-annotations.json next to the manifest.")] string? outputPath = null)
    {
        var result = annotations.ImportAssemblyDimensionAnnotations(manifestPath, annotationJsonOrFilePath, outputPath);
        return Task.FromResult(JsonSerializer.Serialize(result));
    }

    [McpServerTool, Description("Query an assembly entity dimension annotation index to find targets related to an overall X, Y, or Z size change. Use this before editing an assembly based on a user request like 'increase width', 'change height', or 'adjust overall length'.")]
    public Task<string> QueryAssemblyEntityDimensionAnnotations(
        [Description("Path to dimension-annotations.json produced by Qwen annotation or import.")] string annotationPath,
        [Description("Axis to query: x, y, z, all, or omitted.")] string? axis = null,
        [Description("Optional natural language or identifier query such as width, height, bracket, outer face, or component name.")] string? query = null,
        [Description("When true, only returns targets marked related on the requested axis.")] bool onlyRelated = true,
        [Description("Maximum number of matches to return.")] int maxResults = 50)
    {
        var result = annotations.QueryAssemblyDimensionAnnotations(annotationPath, axis, query, onlyRelated, maxResults);
        return Task.FromResult(JsonSerializer.Serialize(result));
    }

    [McpServerTool, Description("Query manifest.json for vision-added StructualInfo/StructuralInfo marks. Use this before CAD edits when the user asks to change the whole assembly/part height or length but does not name the exact component or feature.")]
    public Task<string> QueryAssemblyStructuralComponentTargets(
        [Description("Path to manifest.json containing target StructualInfo objects. Defaults to C:\\temp\\sw-entity-annotations-full\\manifest.json.")] string manifestPath = @"C:\temp\sw-entity-annotations-full\manifest.json",
        [Description("Structural type to find: height or length. If omitted, the tool tries to infer it from query.")] string? type = null,
        [Description("Optional user request or search text, such as '整体高度增高100mm' or 'increase overall length'.")] string? query = null,
        [Description("Maximum number of structural target matches to return.")] int maxResults = 20)
    {
        var result = annotations.QueryAssemblyStructuralComponentTargets(manifestPath, type, query, maxResults);
        return Task.FromResult(JsonSerializer.Serialize(result));
    }

    [McpServerTool, Description("Search feature-structure-annotations.json produced by the VLM feature-structure workflow for structural feature targets that drive an overall height, width, depth, X, Y, Z, or front/top/right view direction. Use this before SolidWorks CAD edits when the user asks to increase/decrease overall height, width, depth, length, footprint, or a view-relative dimension but does not name the exact feature.")]
    public Task<string> SearchStructuralFeatureTargets(
        [Description("Path to feature-structure-annotations.json produced by scripts/annotate_feature_structure_with_vlm.py.")] string annotationPath,
        [Description("Requested affected direction. Accepts height/width/depth, x/y/z, X_width/Y_depth/Z_height, front.vertical, front.horizontal, top.vertical, top.horizontal, right.vertical, right.horizontal, all, or omitted. If omitted, the tool tries to infer direction from query.")] string? direction = null,
        [Description("Optional user request or search text, such as '整体高度增加100mm', 'increase height', 'make the frame wider', a feature name, document name, or NodeId.")] string? query = null,
        [Description("When true, first filters to annotations where IsStructural is true. Keep true for dimension-driving structural searches.")] bool onlyStructural = true,
        [Description("Maximum number of structural feature matches to return.")] int maxResults = 20)
    {
        var result = annotations.SearchStructuralFeatureTargets(annotationPath, direction, query, onlyStructural, maxResults);
        return Task.FromResult(JsonSerializer.Serialize(result));
    }

    [McpServerTool, Description("Highlight a previously captured assembly entity target by targetId using either the capture manifest or dimension annotation file. Use this after querying annotations to locate the entity in the active SolidWorks assembly.")]
    public async Task<string> HighlightAssemblyEntityAnnotationTarget(
        [Description("Path to manifest.json or dimension-annotations.json.")] string manifestOrAnnotationPath,
        [Description("Stable targetId returned by capture/query tools.")] string targetId)
    {
        var result = await sta.InvokeLoggedAsync(
            nameof(HighlightAssemblyEntityAnnotationTarget),
            new { manifestOrAnnotationPath, targetId },
            () => annotations.HighlightAssemblyEntityAnnotationTarget(manifestOrAnnotationPath, targetId));
        return JsonSerializer.Serialize(result);
    }
}
