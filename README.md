# SOLIDWORKS API RAG and MCP Workspace

This workspace starts with a crawler for SOLIDWORKS 2025 API Web Help pages.

It now also contains a SolidWorks MCP server base from [just1step/solidworks-mcp](https://github.com/just1step/solidworks-mcp), with a local RAG-backed knowledge tool added on top.

## Crawl

```powershell
python scripts/crawl_solidworks_api.py --max-pages 100 --delay 0.5
```

Outputs are written to `data/solidworks_api_2025/`:

- `pages.jsonl`: one metadata record per crawled page, including URL, title, paths, and discovered links.
- `html/`: extracted `helpContentData.helpText` HTML for each page.
- `text/`: plain text extracted from the help HTML, suitable for later RAG chunking.
- `crawl_state.json`: summary for the latest run.

The crawler is scoped to `https://help.solidworks.com/2025/english/api/` and resumes by skipping URLs already present in `pages.jsonl`.

## Build RAG Index

The default RAG index is local and requires no API key:

```powershell
python scripts/build_rag_index.py --backend tfidf
```

This reads the current `pages.jsonl`, loads each page's text file, creates overlapping chunks, and writes an index to `data/solidworks_api_2025/rag_index/`.

You can rebuild while the crawler is still running. The builder ignores incomplete JSONL lines and uses only text files that already exist.

To use SentenceTransformers embeddings instead:

```powershell
python scripts/build_rag_index.py --backend sbert --sbert-model sentence-transformers/all-MiniLM-L6-v2
```

## Query

```powershell
python scripts/query_rag.py "IAssemblyDoc AddComponent4 insert part into assembly" --top-k 6
```

Print concatenated context for a downstream LLM or MCP tool:

```powershell
python scripts/query_rag.py "How do I open a SOLIDWORKS document with ISldWorks?" --context --top-k 8
```

## MCP Integration

The upstream MCP app lives in [vendor/solidworks-mcp](</C:/Users/uenx/Documents/New project 3/vendor/solidworks-mcp/README.md>).

Added integration:

- `SearchSolidWorksApiKnowledge`: an MCP tool exposed from `SolidWorksMcpApp` that calls the local Python RAG query script.
- It reads from `data/solidworks_api_2025/rag_index/`, so rebuild the index whenever the crawler has added enough new pages.
- `ListGlobalVariables`: inspect global variables defined in the active document.
- `GetSelectedDimensionInfo`: inspect the currently selected display dimension before binding it.
- `UpsertGlobalVariable`: create or update a SolidWorks global variable in the active document.
- `BindSelectedDimensionToGlobalVariable`: bind the currently selected SolidWorks display dimension to an existing global variable.
- `ListFeatureDimensions`: inspect bindable dimensions on a named feature.
- `UpsertGlobalVariableAndBindFeatureDimensionByDescription`: create/update a variable and bind the best-matching feature dimension by description, without manual dimension selection.
- `CaptureActiveAssemblyEntityAnnotationSet`: traverse the active assembly, highlight each component/feature/body target, export front/top/right PNGs, and write a stable `manifest.json`.
- `AnnotateAssemblyEntityCaptureSetWithQwen`: call an OpenAI-compatible Qwen vision endpoint to classify each captured target as directly related to overall X/Y/Z assembly size.
- `ImportAssemblyEntityDimensionAnnotations`: import externally generated target annotations into a normalized `dimension-annotations.json` index.
- `QueryAssemblyEntityDimensionAnnotations`: query the annotation index before changing overall assembly width/depth/height.
- `HighlightAssemblyEntityAnnotationTarget`: reselect a captured target in the active assembly by stable `targetId`.

## Assembly Entity Dimension Annotation Workflow

The entity annotation tools are designed for the workflow where the model first builds an evidence index, then later uses that index to plan size changes.

0. Export the complete feature tree for the active part or assembly:

```powershell
python .\scripts\capture_assembly_entity_annotations.py `
  --output-dir C:\temp\sw-work `
  --feature-tree-only `
  --overwrite-feature-tree
```

By default the runner writes the final files under `featru_tree_<active top document name>` next to the requested `--output-dir`; for example `C:\temp\featru_tree_FL9A项目号A401.001`. This directory contains `feature-tree.json`, `feature-tree-documents.jsonl`, and progress/recovery files when applicable. Pass `--no-use-active-document-output-dir` if you need to keep exactly the directory supplied in `--output-dir`.

For an active part, `feature-tree.json` exports that document's FeatureManager features. For an active assembly, it exports the recursive child part/subassembly document tree and address metadata, and it includes child part feature nodes by default. In schema v4, every node is traversed through a single `Children` array. Document children contain feature nodes (`EntityKind = "feature"`) and referenced component/subassembly document nodes (`EntityKind = "document"`). Reference features (`FeatureTypeName = "Reference"`) may contain the referenced part or subassembly document node in their own `Children`, which is how lower-level feature trees are reached without dumping every child assembly management folder. A part document normally has no child component documents, but its `Children` should contain its feature nodes when feature enumeration is enabled. Child assembly document features are skipped by default except for `Reference` features needed to continue recursion; pass `--include-component-features` when you want loaded child assembly feature nodes too. Imported neutral-format files such as STEP-derived parts are skipped by default while preserving their document/component address nodes. If a large imported part blocks export, use `--no-include-part-features` as a fallback, or let the script auto-skip stuck documents while preserving their document/address nodes. Feature subtrees and management folders such as mates, annotations, and component folders are recorded but not expanded by default; use `--expand-feature-subtrees` and `--expand-management-feature-subtrees` only when you need those raw SolidWorks subtrees too.

0a. Filter the feature tree into capture targets:

```powershell
python .\scripts\capture_assembly_entity_annotations.py `
  --output-dir C:\temp\featru_tree_FL9A项目号A401.001 `
  --filter-feature-tree
```

This reads `feature-tree.json` and writes `feature-tree-filtered-features.json` in the same directory. The current filter keeps only nodes where `EntityKind == "feature"`, `HasSubFeatures == true`, and `FeatureTypeName` is one of `Extrusion`, `WeldMemberFeat`, `ICE`, `SMBaseFlange`, or `EdgeFlange`. Each target keeps the fields needed to re-locate the feature later, including `SourceIndex`, `Name`, `FeatureTypeName`, `FeaturePath`, `GraphPath`, `DocumentPath`, `HierarchyPath`, and `ParentDocument.Path`.

0b. Capture normal highlighted three-view images for each filtered feature:

```powershell
python .\scripts\capture_assembly_entity_annotations.py `
  --output-dir C:\temp\featru_tree_FL9A项目号A401.001 `
  --capture-filtered-feature-three-views `
  --width 1280 `
  --height 720 `
  --batch-size 1 `
  --tool-time-budget 45 `
  --request-timeout 120
```

This reads `feature-tree-filtered-features.json` and writes images under `featru_tree_<active top document name>\three_views`. Each feature gets its own folder named from the feature name plus the feature `NodeId`, for example `凸台-拉伸1_ae_af61fff403c5137f`, so repeated feature names can be checked directly against the JSON node. Each folder currently contains `front.png`, `top.png`, and `right.png` with the target feature colored red. A resumable `three_views\three-view-manifest.json` records processed targets, images, selection status, `nextStartIndex`, and `stoppedReason`. Re-run with the returned `nextStartIndex`, or keep `--resume` enabled and repeat the same command until `stoppedReason` is `completed`.

Transparent context views are intentionally disabled for now because large assemblies can spend most of their time touching SolidWorks face-level appearance objects. The `--max-transparent-faces` parameter is still accepted for compatibility, but the current capture path exports only the normal highlighted three-view set.

0c. Ask a Qwen-compatible VLM whether each highlighted feature is structural and which dimension direction it mainly affects:

```powershell
Copy-Item .\config\qwen_vlm_config.example.json .\config\qwen_vlm_config.json
```

Edit `config\qwen_vlm_config.json` and fill `api_key`, or leave `api_key` as the placeholder and set the environment variable named by `api_key_env` such as `DASHSCOPE_API_KEY`. This local config file is ignored by git. If your key starts with `sk-ws-`, it is a Bailian workspace key; fill `workspace_id` or set `base_url` to `https://<WorkspaceId>.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`. The default model in the example config is `qwen3.7-plus`; if your workspace only has another vision model enabled, set `model` accordingly.

Preview the prompt and input paths without calling the VLM:

```powershell
python .\scripts\annotate_feature_structure_with_vlm.py `
  --output-dir C:\temp\featru_tree_FL9A椤圭洰鍙稟401.001 `
  --dry-run
```

Run the full annotation and merge the result into a copy of the feature tree:

```powershell
python .\scripts\annotate_feature_structure_with_vlm.py `
  --output-dir C:\temp\featru_tree_FL9A椤圭洰鍙稟401.001
```

The script reads `feature-tree-filtered-features.json` and `three_views\three-view-manifest.json`, sends the `front.png`, `top.png`, and `right.png` images for each target to the VLM, and writes `feature-structure-annotations.json`. It also writes `feature-tree-with-structure-annotations.json`, where matching feature nodes contain a `StructuralRoleAnnotation` object. The VLM output is normalized to stable JSON fields including `IsStructural`, `StructuralCategory`, `PrimaryDirection`, `AffectedDirections`, `DimensionChangeIntent`, `Evidence`, and `Confidence`.

Direction labels are intentionally view-relative and finite: `front.horizontal`, `front.vertical`, `top.horizontal`, `top.vertical`, `right.horizontal`, and `right.vertical`. The script also stores a coarse `global_axis_hint`: `X_width`, `Y_depth`, or `Z_height`. For example, a feature that drives overall height should usually be marked as `front.vertical` or `right.vertical` with `global_axis_hint = Z_height`, which lets later LLM/MCP workflows quickly find candidate features for requests such as "increase this part height by 100 mm".

0d. Search the generated structural feature annotations before CAD edits:

```text
SearchStructuralFeatureTargets(
  annotationPath="C:\\temp\\featru_tree_FL9A项目号A401.001\\feature-structure-annotations.json",
  direction="height",
  query="整体高度增加100mm",
  onlyStructural=true,
  maxResults=20
)
```

This MCP tool is intended for automatic LLM use when a user asks to change an overall structure dimension but does not name the exact feature. It reads `feature-structure-annotations.json`, first filters to `IsStructural == true` by default, then matches the requested direction against `PrimaryDirection.global_axis_hint` and `AffectedDirections[*].global_axis_hint`. The `direction` argument accepts `height`, `width`, `depth`, `x`, `y`, `z`, `X_width`, `Y_depth`, `Z_height`, exact view labels such as `front.vertical` or `top.horizontal`, `all`, or it can be omitted so the tool infers the direction from `query`.

The returned matches include `NodeId`, `SourceIndex`, `Name`, `FeatureTypeName`, `FeaturePath`, `GraphPath`, `DocumentPath`, `HierarchyPath`, `ThreeViewOutputDirectory`, `Images`, matched direction labels, matched `global_axis_hint` values, and the original VLM structural fields. For example, a height request should search for `Z_height` candidates, then later editing tools can use the returned feature address fields to inspect or modify the likely dimension-driving feature.

0e. Update a located SolidWorks dimension by name/token:

```text
SetDimensionValueByName(
  dimensionName="D7@边线-法兰1",
  valueExpression="100mm",
  rebuild=true
)
```

Use this after the model has identified the correct feature and dimension, for example by combining `SearchStructuralFeatureTargets` with `ListFeatureDimensions`. `dimensionName` can be a SolidWorks dimension token such as `D7@边线-法兰1`, a `FullName`, or a `DisplayDimensionSelectionName` returned by `ListFeatureDimensions`. `valueExpression` accepts `mm`, `cm`, or `m`; a unitless number is interpreted as meters. The result reports the previous and updated values in meters plus the matched owner feature when available.

1. Capture a manifest and front/top/right images for the active assembly:

```text
CaptureActiveAssemblyEntityAnnotationSet(
  outputDirectory="C:\\temp\\sw-entity-annotations",
  width=1280,
  height=720,
  includeComponents=true,
  includeFeatures=true,
  includeBodies=true,
  maxTargets=50,
  startIndex=0,
  skipExistingTargets=true,
  writeManifestAfterEachTarget=true,
  maxDurationSeconds=45,
  useCleanDisplayMode=false,
  capturePaddingFactor=1.35
)
```

This writes `manifest.json` and one `entities/<targetId>/front.png`, `top.png`, and `right.png` set per target. Each manifest target contains the owning component path, hierarchy path, document path, feature/body metadata, selection status, and stable `targetId`.

The capture skips FeatureManager management nodes such as `Favorites`, `Sensors`, `DocsFolder`, mate folders, reference folders, lights/cameras, and other non-geometric folders. Child part and subassembly contents are collected by recursively traversing assembly components instead of treating `DocsFolder` itself as a target. When a child feature or body cannot be directly selected in assembly context, the capture falls back to highlighting the owning component and records that as `selection.method = "owning-component"`.

For large assemblies, run capture in batches to avoid MCP request timeouts:

```text
CaptureActiveAssemblyEntityAnnotationSet(
  outputDirectory="C:\\temp\\sw-entity-annotations",
  width=800,
  height=600,
  includeComponents=true,
  includeFeatures=true,
  includeBodies=false,
  maxTargets=25,
  startIndex=0,
  skipExistingTargets=true,
  maxDurationSeconds=45,
  useCleanDisplayMode=false,
  capturePaddingFactor=1.35
)

CaptureActiveAssemblyEntityAnnotationSet(
  outputDirectory="C:\\temp\\sw-entity-annotations",
  width=800,
  height=600,
  includeComponents=true,
  includeFeatures=true,
  includeBodies=false,
  maxTargets=25,
  startIndex=25,
  skipExistingTargets=true,
  maxDurationSeconds=45,
  useCleanDisplayMode=false,
  capturePaddingFactor=1.35
)
```

`writeManifestAfterEachTarget=true` is the default, so completed targets are persisted after every capture. `maxDurationSeconds` is also enabled by default; it makes the tool return a partial manifest before common MCP client request timeouts cut the call off. The result includes `totalTargetCount`, `processedThisRun`, `skippedExistingCount`, `nextStartIndex`, and `stoppedReason`. Re-run with the same `outputDirectory`, `skipExistingTargets=true`, and `startIndex` set to the returned `nextStartIndex` until `stoppedReason` is `completed`.

By default, `useCleanDisplayMode=false` preserves the normal SolidWorks shaded display and selection highlight. The capture switches to each standard view, zooms to fit, zooms out with `capturePaddingFactor`, selects the target, refreshes highlighted items, and exports the PNG. The default `capturePaddingFactor=1.35` prioritizes fitting the whole model in view; if the model appears too small you can lower it, for example `1.2`. Set `useCleanDisplayMode=true` only when you explicitly want hidden-lines-removed images and do not need the normal shaded highlight appearance.

For very large assemblies, prefer the Python batch runner instead of asking an LLM client to hold one long MCP request open:

```powershell
python .\scripts\capture_assembly_entity_annotations.py `
  --output-dir C:\temp\sw-entity-annotations `
  --width 800 `
  --height 600 `
  --batch-size 5 `
  --tool-time-budget 20 `
  --request-timeout 90 `
  --padding 1.35 `
  --no-include-bodies
```

The runner starts `SolidWorksMcpApp.exe --proxy`, calls `CaptureActiveAssemblyEntityAnnotationSet` repeatedly, and resumes from `manifest.json` if a batch times out after writing partial progress. It automatically uses the latest `artifacts\solidworks-mcp*\SolidWorksMcpApp.exe`, or you can pass `--exe C:\path\to\SolidWorksMcpApp.exe`.

To verify the Python runner can talk to the local MCP hub before capturing, run:

```powershell
python .\scripts\capture_assembly_entity_annotations.py `
  --output-dir C:\temp\sw-entity-annotations `
  --probe-only
```

2. Annotate with Qwen vision from inside the MCP app:

```text
AnnotateAssemblyEntityCaptureSetWithQwen(
  manifestPath="C:\\temp\\sw-entity-annotations\\manifest.json",
  model="qwen3.6-flash",
  maxTargets=0
)
```

Set `DASHSCOPE_API_KEY` or `QWEN_API_KEY` in the environment before starting `SolidWorksMcpApp.exe`. Optional overrides are `SOLIDWORKS_ENTITY_ANNOTATION_QWEN_MODEL`, `QWEN_VISION_MODEL`, `DASHSCOPE_BASE_URL`, and `QWEN_BASE_URL`. The default base URL is DashScope OpenAI-compatible mode, and the default model is `qwen3.6-flash`.

3. Or import annotations produced by an external vision pipeline:

```json
[
  {
    "targetId": "ae_0123456789abcdef",
    "x": { "related": true, "description": "Controls the left/right outside envelope.", "identifiers": ["outer side face"] },
    "y": { "related": false },
    "z": { "related": true, "description": "Sets the top boundary.", "identifiers": ["top plate"] },
    "overallReason": "The target is on the assembly envelope.",
    "confidence": 0.82
  }
]
```

Call `ImportAssemblyEntityDimensionAnnotations(manifestPath, annotationJsonOrFilePath)` to normalize this into `dimension-annotations.json`.

4. Before changing an overall assembly dimension, query the index:

```text
QueryAssemblyEntityDimensionAnnotations(
  annotationPath="C:\\temp\\sw-entity-annotations\\dimension-annotations.json",
  axis="z",
  query="height",
  onlyRelated=true
)
```

5. Highlight a returned target before editing:

```text
HighlightAssemblyEntityAnnotationTarget(
  manifestOrAnnotationPath="C:\\temp\\sw-entity-annotations\\dimension-annotations.json",
  targetId="ae_0123456789abcdef"
)
```

The intended downstream edit flow is: query related targets for the requested X/Y/Z size change, inspect returned `componentPath`, `hierarchyPath`, `featureName`, and descriptions, then use the existing component-open and feature-dimension binding tools to adjust the confirmed controlling geometry.

Build prerequisites on the Windows machine where you publish the app:

- SolidWorks installed locally
- .NET 8 SDK available in `PATH`
- Python available in `PATH`

Optional environment variables when the published exe is not launched from this workspace:

- `SOLIDWORKS_MCP_WORKSPACE`: root folder containing `scripts/query_rag.py`
- `SOLIDWORKS_API_RAG_QUERY_SCRIPT`: explicit path to `query_rag.py`
- `SOLIDWORKS_API_RAG_INDEX_DIR`: explicit path to the built RAG index

Publish command:

```powershell
.\scripts\publish_solidworks_mcp.ps1
```

The app will still use the upstream tray hub + `--proxy` stdio client flow. The only added behavior is the local API knowledge-search tool.

After publishing, start the tray app:

```powershell
.\artifacts\solidworks-mcp\SolidWorksMcpApp.exe
```

The exported Claude Desktop and VS Code MCP configs now include the RAG environment variables automatically:

- `SOLIDWORKS_MCP_WORKSPACE`
- `SOLIDWORKS_API_RAG_QUERY_SCRIPT`
- `SOLIDWORKS_API_RAG_INDEX_DIR`

That allows `SearchSolidWorksApiKnowledge` to work even when the exe is launched from `artifacts\solidworks-mcp\`.

For `BindSelectedDimensionToGlobalVariable`, exactly one SolidWorks display dimension must already be selected.
