#!/usr/bin/env python3
"""
Annotate filtered SolidWorks feature three-view captures with a Qwen-compatible VLM.

The script is offline with respect to SolidWorks: it reads the exported
feature tree, filtered feature targets, and three-view PNG manifest, calls an
OpenAI-compatible vision endpoint, writes a flat annotation index, and merges
the result back into a copy of feature-tree.json.
"""

from __future__ import annotations

import argparse
import base64
import copy
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "solidworks-mcp.feature-structure-vlm.v1"
DEFAULT_CONFIG_PATH = Path("config/qwen_vlm_config.json")
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen3.7-plus"
DEFAULT_REGION = "cn-beijing"
VIEW_ORDER = ("front", "top", "right")
ALLOWED_DIRECTION_LABELS = {
    "front.horizontal": ("front", "horizontal", "X_width"),
    "front.vertical": ("front", "vertical", "Z_height"),
    "top.horizontal": ("top", "horizontal", "X_width"),
    "top.vertical": ("top", "vertical", "Y_depth"),
    "right.horizontal": ("right", "horizontal", "Y_depth"),
    "right.vertical": ("right", "vertical", "Z_height"),
}

SYSTEM_PROMPT = """You are a strict mechanical CAD structure inspector.

You receive three SolidWorks screenshots of the same model: front, top, and right.
The target feature is colored red. Judge only the red highlighted feature, not the
entire model.

Your task:
1. Decide whether the red feature is a structural feature.
2. Decide which view-relative dimension direction(s) it mainly affects.
3. Return one JSON object only. Do not return markdown, comments, or extra text.

Definitions:
- A structural feature is a feature that materially contributes to the main frame,
  load path, support, mounting base, enclosing plate/flange, rail, beam, bracket,
  spacer, or an outside envelope/spacing dimension of the overall assembly.
- A non-structural feature is local detail that does not materially control the
  overall size or support structure, such as a small hole/cut/chamfer/fillet,
  local cosmetic boss, minor clearance detail, fastener-only feature, or a detail
  hidden inside a larger part.
- If the feature is hard to see or the evidence is weak, use lower confidence and
  prefer "uncertain" or "non_structural" rather than guessing.

Allowed direction labels:
- front.horizontal: left/right width in the front view, usually global X_width.
- front.vertical: height in the front view, usually global Z_height.
- top.horizontal: left/right width in the top view, usually global X_width.
- top.vertical: front/back depth in the top view, usually global Y_depth.
- right.horizontal: front/back depth in the right view, usually global Y_depth.
- right.vertical: height in the right view, usually global Z_height.

Return exactly this JSON shape:
{
  "is_structural": true,
  "structural_category": "frame_member|base_plate|side_plate|bracket|rail|support|panel_or_flange|dimension_control_feature|non_structural|uncertain",
  "primary_direction": {
    "label": "front.vertical|front.horizontal|top.vertical|top.horizontal|right.vertical|right.horizontal|unknown",
    "view": "front|top|right|unknown",
    "axis": "horizontal|vertical|unknown",
    "global_axis_hint": "X_width|Y_depth|Z_height|unknown",
    "influence": "sets_overall_extent|sets_spacing|supports_load_path|defines_clearance|local_only|unknown",
    "confidence": 0.0
  },
  "affected_directions": [
    {
      "label": "front.vertical",
      "view": "front",
      "axis": "vertical",
      "global_axis_hint": "Z_height",
      "influence": "sets_overall_extent",
      "confidence": 0.0
    }
  ],
  "dimension_change_intent": {
    "can_drive_overall_size_change": true,
    "recommended_edit_axis": "height|width|depth|unknown",
    "edit_relevance": "direct|indirect|weak|none"
  },
  "evidence": {
    "red_feature_location": "short visual description",
    "reason": "short reason for the structural and direction decision",
    "uncertainty": "short note, empty string if none"
  },
  "confidence": 0.0
}
"""


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def json_get(obj: dict[str, Any], key: str, default: Any = None) -> Any:
    if key in obj:
        return obj[key]

    lowered = key.lower()
    for candidate, value in obj.items():
        if candidate.lower() == lowered:
            return value
    return default


def json_bool(obj: dict[str, Any], key: str, default: bool = False) -> bool:
    value = json_get(obj, key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def clamp_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(0.0, min(1.0, parsed))


def resolve_endpoint(base_url: str) -> str:
    trimmed = base_url.strip().rstrip("/")
    if trimmed.lower().endswith("/chat/completions"):
        return trimmed
    return f"{trimmed}/chat/completions"


def is_placeholder(value: Any) -> bool:
    text = str(value or "").strip()
    return (
        not text
        or text.startswith("YOUR_")
        or "{WorkspaceId}" in text
        or "<WorkspaceId>" in text
    )


def resolve_base_url(config: dict[str, Any], explicit_base_url: str | None = None) -> str:
    if explicit_base_url and explicit_base_url.strip():
        return explicit_base_url.strip()

    workspace_id = (
        str(config.get("workspace_id") or "").strip()
        or os.environ.get("DASHSCOPE_WORKSPACE_ID", "").strip()
        or os.environ.get("BAILIAN_WORKSPACE_ID", "").strip()
    )
    if not is_placeholder(workspace_id):
        region = str(config.get("region") or DEFAULT_REGION).strip() or DEFAULT_REGION
        return f"https://{workspace_id}.{region}.maas.aliyuncs.com/compatible-mode/v1"

    configured_base_url = str(config.get("base_url") or "").strip()
    if not is_placeholder(configured_base_url):
        return configured_base_url

    return DEFAULT_BASE_URL


def is_global_dashscope_endpoint(endpoint: str) -> bool:
    normalized = endpoint.lower()
    return "dashscope.aliyuncs.com" in normalized and ".maas.aliyuncs.com" not in normalized


def validate_endpoint_for_api_key(api_key: str, endpoint: str) -> None:
    if api_key.startswith("sk-ws-") and is_global_dashscope_endpoint(endpoint):
        raise RuntimeError(
            "The configured API key looks like a Bailian workspace key (prefix sk-ws-), "
            "but the endpoint is the global DashScope endpoint. Fill workspace_id in "
            "config/qwen_vlm_config.json, or set base_url to "
            "https://<WorkspaceId>.cn-beijing.maas.aliyuncs.com/compatible-mode/v1. "
            "You can also set DASHSCOPE_WORKSPACE_ID or BAILIAN_WORKSPACE_ID."
        )


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"VLM config was not found: {path}. Copy config/qwen_vlm_config.example.json "
            "to config/qwen_vlm_config.json and fill api_key."
        )

    data = load_json(path)
    if not isinstance(data, dict):
        raise RuntimeError(f"VLM config must be a JSON object: {path}")
    return data


def resolve_api_key(config: dict[str, Any]) -> str:
    configured = str(config.get("api_key") or "").strip()
    if configured and not configured.startswith("YOUR_"):
        return configured

    env_name = str(config.get("api_key_env") or "").strip()
    if env_name:
        env_value = os.environ.get(env_name)
        if env_value:
            return env_value.strip()

    for fallback in ("DASHSCOPE_API_KEY", "QWEN_API_KEY"):
        env_value = os.environ.get(fallback)
        if env_value:
            return env_value.strip()

    raise RuntimeError(
        "No Qwen API key configured. Fill api_key in the config file, set api_key_env, "
        "or set DASHSCOPE_API_KEY/QWEN_API_KEY."
    )


def image_to_data_url(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Image was not found: {path}")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def compact_target_metadata(target: dict[str, Any], manifest_target: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "SourceIndex",
        "NodeId",
        "Name",
        "FeatureTypeName",
        "FeaturePath",
        "GraphPath",
        "DocumentTitle",
        "DocumentPath",
        "ComponentName",
        "ComponentPath",
        "HierarchyPath",
    ]
    metadata: dict[str, Any] = {}
    for key in keys:
        value = json_get(target, key)
        if value in (None, ""):
            value = json_get(manifest_target, key)
        if value not in (None, ""):
            metadata[key] = value

    selection = json_get(manifest_target, "Selection")
    if isinstance(selection, dict):
        metadata["Selection"] = {
            "Selected": json_bool(selection, "Selected", False),
            "Method": json_get(selection, "Method"),
            "Message": json_get(selection, "Message"),
        }
    return metadata


def build_user_prompt(target: dict[str, Any], manifest_target: dict[str, Any]) -> str:
    metadata = compact_target_metadata(target, manifest_target)
    return (
        "Inspect the red highlighted SolidWorks feature in the attached front, top, "
        "and right screenshots.\n\n"
        "Use the target metadata only as identity context; make the structural and "
        "direction decision from the images.\n\n"
        "Target metadata:\n"
        f"{json.dumps(metadata, ensure_ascii=False, indent=2)}\n\n"
        "Important decision rules:\n"
        "- Mark is_structural=true only when the highlighted red geometry is part of "
        "the main support/envelope/dimension-controlling structure.\n"
        "- Mark is_structural=false when it is only a local manufacturing/detail "
        "feature, even if it is visible.\n"
        "- If it affects height, prefer front.vertical or right.vertical.\n"
        "- If it affects left/right width, prefer front.horizontal or top.horizontal.\n"
        "- If it affects front/back depth, prefer top.vertical or right.horizontal.\n"
        "- affected_directions may contain multiple labels, but primary_direction "
        "must contain the single strongest one.\n\n"
        "Return only the JSON object specified by the system instructions."
    )


def image_paths_for_manifest_target(manifest_target: dict[str, Any]) -> dict[str, Path]:
    images = json_get(manifest_target, "Images", [])
    if not isinstance(images, list):
        raise RuntimeError("Manifest target Images must be a list.")

    by_view: dict[str, Path] = {}
    for image in images:
        if not isinstance(image, dict):
            continue
        view = str(json_get(image, "ViewName", "") or "").strip().lower()
        output_path = json_get(image, "OutputPath")
        if view and output_path:
            by_view[view] = Path(str(output_path))

    missing = [view for view in VIEW_ORDER if view not in by_view]
    if missing:
        raise RuntimeError(
            f"Manifest target SourceIndex={json_get(manifest_target, 'SourceIndex')} "
            f"is missing view image(s): {', '.join(missing)}"
        )
    return by_view


def build_messages(target: dict[str, Any], manifest_target: dict[str, Any]) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [
        {"type": "text", "text": build_user_prompt(target, manifest_target)}
    ]
    image_paths = image_paths_for_manifest_target(manifest_target)
    for view in VIEW_ORDER:
        content.append({"type": "text", "text": f"{view} view image:"})
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": image_to_data_url(image_paths[view])},
            }
        )

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]


def call_openai_compatible_chat(
    endpoint: str,
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    config: dict[str, Any],
) -> str:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": float(config.get("temperature", 0)),
        "max_tokens": int(config.get("max_tokens", 1200)),
    }
    if bool(config.get("response_format_json", True)):
        payload["response_format"] = {"type": "json_object"}

    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    timeout = float(config.get("timeout_seconds", 120))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_json = json.loads(response.read().decode("utf-8-sig"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(format_vlm_http_error(exc.code, body, endpoint, model)) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"VLM request failed: {exc}") from exc

    choices = response_json.get("choices") or []
    if not choices:
        raise RuntimeError(f"VLM response did not contain choices: {response_json}")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not isinstance(content, str):
        raise RuntimeError(f"VLM response message content was not text: {response_json}")
    return content


def format_vlm_http_error(status_code: int, body: str, endpoint: str, model: str) -> str:
    guidance = ""
    try:
        parsed = json.loads(body)
        error = parsed.get("error") if isinstance(parsed, dict) else None
        code = str((error or {}).get("code") or "").lower() if isinstance(error, dict) else ""
        message = str((error or {}).get("message") or "") if isinstance(error, dict) else ""
    except json.JSONDecodeError:
        code = ""
        message = ""

    if status_code == 403 and ("access_denied" in code or "access denied" in message.lower()):
        guidance = (
            "\nLikely causes for Bailian/DashScope access_denied:\n"
            "- The endpoint does not match the API key's workspace/region. Workspace keys usually need "
            "https://<WorkspaceId>.cn-beijing.maas.aliyuncs.com/compatible-mode/v1.\n"
            "- The workspace has not been granted access to the requested model.\n"
            "- The account has not enabled Bailian, has arrears, or free quota has been exhausted.\n"
            "- The model name is unavailable for this workspace. Try model=qwen3.7-plus first."
        )

    return (
        f"VLM HTTP {status_code} while calling model '{model}' at '{endpoint}': {body}"
        f"{guidance}"
    )


def parse_json_object_from_text(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`").strip()
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()

    decoder = json.JSONDecoder()
    for index, char in enumerate(stripped):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise RuntimeError(f"Could not parse a JSON object from VLM response: {text[:500]}")


def normalize_direction(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        value = {}

    label = str(value.get("label") or "unknown").strip()
    if label not in ALLOWED_DIRECTION_LABELS:
        view = str(value.get("view") or "unknown").strip()
        axis = str(value.get("axis") or "unknown").strip()
        candidate = f"{view}.{axis}"
        label = candidate if candidate in ALLOWED_DIRECTION_LABELS else "unknown"

    if label in ALLOWED_DIRECTION_LABELS:
        view, axis, global_axis = ALLOWED_DIRECTION_LABELS[label]
    else:
        view = "unknown"
        axis = "unknown"
        global_axis = "unknown"

    influence = str(value.get("influence") or "unknown").strip()
    if influence not in {
        "sets_overall_extent",
        "sets_spacing",
        "supports_load_path",
        "defines_clearance",
        "local_only",
        "unknown",
    }:
        influence = "unknown"

    return {
        "label": label,
        "view": view,
        "axis": axis,
        "global_axis_hint": global_axis,
        "influence": influence,
        "confidence": clamp_float(value.get("confidence"), 0.0),
    }


def normalize_vlm_result(raw: dict[str, Any]) -> dict[str, Any]:
    is_structural = bool(raw.get("is_structural", False))
    category = str(raw.get("structural_category") or "").strip() or (
        "uncertain" if is_structural else "non_structural"
    )
    allowed_categories = {
        "frame_member",
        "base_plate",
        "side_plate",
        "bracket",
        "rail",
        "support",
        "panel_or_flange",
        "dimension_control_feature",
        "non_structural",
        "uncertain",
    }
    if category not in allowed_categories:
        category = "uncertain" if is_structural else "non_structural"

    affected_raw = raw.get("affected_directions")
    if not isinstance(affected_raw, list):
        affected_raw = []
    affected = [normalize_direction(item) for item in affected_raw]
    affected = [item for item in affected if item["label"] != "unknown"]

    primary = normalize_direction(raw.get("primary_direction"))
    if primary["label"] == "unknown" and affected:
        primary = affected[0]

    dimension_change_intent = raw.get("dimension_change_intent")
    if not isinstance(dimension_change_intent, dict):
        dimension_change_intent = {}
    recommended_axis = str(dimension_change_intent.get("recommended_edit_axis") or "unknown").strip()
    if recommended_axis not in {"height", "width", "depth", "unknown"}:
        recommended_axis = "unknown"
    edit_relevance = str(dimension_change_intent.get("edit_relevance") or "none").strip()
    if edit_relevance not in {"direct", "indirect", "weak", "none"}:
        edit_relevance = "none"

    evidence = raw.get("evidence")
    if not isinstance(evidence, dict):
        evidence = {}

    return {
        "is_structural": is_structural,
        "structural_category": category,
        "primary_direction": primary,
        "affected_directions": affected,
        "dimension_change_intent": {
            "can_drive_overall_size_change": bool(
                dimension_change_intent.get("can_drive_overall_size_change", False)
            ),
            "recommended_edit_axis": recommended_axis,
            "edit_relevance": edit_relevance,
        },
        "evidence": {
            "red_feature_location": str(evidence.get("red_feature_location") or ""),
            "reason": str(evidence.get("reason") or ""),
            "uncertainty": str(evidence.get("uncertainty") or ""),
        },
        "confidence": clamp_float(raw.get("confidence"), 0.0),
    }


def source_index(value: dict[str, Any]) -> int:
    raw = json_get(value, "SourceIndex", -1)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return -1


def entry_key(entry: dict[str, Any]) -> str:
    node_id = json_get(entry, "NodeId")
    if node_id:
        return f"node:{node_id}"
    return f"source:{source_index(entry)}"


def feature_fallback_key(node: dict[str, Any]) -> str:
    parts = [
        str(json_get(node, "DocumentPath", "") or "").lower(),
        str(json_get(node, "HierarchyPath", "") or "").lower(),
        str(json_get(node, "FeaturePath", "") or "").lower(),
        str(json_get(node, "Name", "") or "").lower(),
    ]
    return "|".join(parts)


def make_annotation_entry(
    target: dict[str, Any],
    manifest_target: dict[str, Any],
    vlm_result: dict[str, Any] | None,
    model: str,
    endpoint: str,
    status: str,
    error: str | None = None,
) -> dict[str, Any]:
    normalized = normalize_vlm_result(vlm_result or {})
    image_paths = image_paths_for_manifest_target(manifest_target)
    return {
        "SchemaVersion": SCHEMA_VERSION,
        "AnnotatedUtc": utc_now(),
        "AnnotationStatus": status,
        "Error": error,
        "Model": model,
        "Endpoint": endpoint,
        "SourceIndex": source_index(target),
        "NodeId": json_get(target, "NodeId"),
        "Name": json_get(target, "Name"),
        "FeatureTypeName": json_get(target, "FeatureTypeName"),
        "FeaturePath": json_get(target, "FeaturePath"),
        "GraphPath": json_get(target, "GraphPath"),
        "DocumentTitle": json_get(target, "DocumentTitle"),
        "DocumentPath": json_get(target, "DocumentPath"),
        "HierarchyPath": json_get(target, "HierarchyPath"),
        "ThreeViewOutputDirectory": json_get(manifest_target, "OutputDirectory"),
        "Images": {view: str(image_paths[view]) for view in VIEW_ORDER},
        "IsStructural": normalized["is_structural"],
        "StructuralCategory": normalized["structural_category"],
        "PrimaryDirection": normalized["primary_direction"],
        "AffectedDirections": normalized["affected_directions"],
        "DimensionChangeIntent": normalized["dimension_change_intent"],
        "Evidence": normalized["evidence"],
        "Confidence": normalized["confidence"],
        "VlmResult": normalized,
    }


def build_annotation_set(
    entries: list[dict[str, Any]],
    args: argparse.Namespace,
    model: str,
    endpoint: str,
) -> dict[str, Any]:
    completed = [entry for entry in entries if entry.get("AnnotationStatus") == "completed"]
    failed = [entry for entry in entries if entry.get("AnnotationStatus") != "completed"]
    return {
        "SchemaVersion": SCHEMA_VERSION,
        "UpdatedUtc": utc_now(),
        "Model": model,
        "Endpoint": endpoint,
        "FeatureTreePath": str(args.feature_tree_path.resolve()),
        "FilteredFeatureTreePath": str(args.filtered_feature_tree_path.resolve()),
        "ThreeViewManifestPath": str(args.three_view_manifest_path.resolve()),
        "AnnotationPath": str(args.annotation_output.resolve()),
        "AnnotatedFeatureTreePath": str(args.annotated_feature_tree_output.resolve()),
        "TargetCount": len(entries),
        "CompletedCount": len(completed),
        "FailedCount": len(failed),
        "Entries": sorted(entries, key=source_index),
    }


def annotation_for_feature_tree(entry: dict[str, Any], annotation_path: Path) -> dict[str, Any]:
    return {
        "SchemaVersion": SCHEMA_VERSION,
        "AnnotatedUtc": entry.get("AnnotatedUtc"),
        "AnnotationPath": str(annotation_path.resolve()),
        "AnnotationStatus": entry.get("AnnotationStatus"),
        "SourceIndex": entry.get("SourceIndex"),
        "Model": entry.get("Model"),
        "IsStructural": entry.get("IsStructural"),
        "StructuralCategory": entry.get("StructuralCategory"),
        "PrimaryDirection": entry.get("PrimaryDirection"),
        "AffectedDirections": entry.get("AffectedDirections"),
        "DimensionChangeIntent": entry.get("DimensionChangeIntent"),
        "Evidence": entry.get("Evidence"),
        "Confidence": entry.get("Confidence"),
        "ThreeViewOutputDirectory": entry.get("ThreeViewOutputDirectory"),
        "VlmResult": entry.get("VlmResult"),
    }


def merge_annotations_into_feature_tree(
    feature_tree: dict[str, Any],
    entries: list[dict[str, Any]],
    annotation_path: Path,
) -> tuple[dict[str, Any], int]:
    merged = copy.deepcopy(feature_tree)
    by_node = {
        str(entry.get("NodeId")): entry
        for entry in entries
        if entry.get("NodeId")
    }
    by_fallback = {
        feature_fallback_key(entry): entry
        for entry in entries
        if feature_fallback_key(entry).strip("|")
    }

    merged_count = 0

    def visit(node: Any) -> None:
        nonlocal merged_count
        if not isinstance(node, dict):
            return

        if str(json_get(node, "EntityKind", "") or "").lower() == "feature":
            entry = None
            node_id = json_get(node, "NodeId")
            if node_id:
                entry = by_node.get(str(node_id))
            if entry is None:
                entry = by_fallback.get(feature_fallback_key(node))
            if entry is not None:
                node["StructuralRoleAnnotation"] = annotation_for_feature_tree(entry, annotation_path)
                merged_count += 1

        children = json_get(node, "Children", [])
        if isinstance(children, list):
            for child in children:
                visit(child)

    root = json_get(merged, "RootDocument")
    visit(root if isinstance(root, dict) else merged)
    merged["StructureAnnotationSchemaVersion"] = SCHEMA_VERSION
    merged["StructureAnnotationPath"] = str(annotation_path.resolve())
    merged["StructureAnnotationMergedUtc"] = utc_now()
    merged["StructureAnnotationMergedFeatureCount"] = merged_count
    return merged, merged_count


def load_existing_entries(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = load_json(path)
    entries = json_get(data, "Entries", [])
    if not isinstance(entries, list):
        raise RuntimeError(f"Annotation file Entries must be a list: {path}")
    return [entry for entry in entries if isinstance(entry, dict)]


def select_targets(
    filtered_targets: list[dict[str, Any]],
    manifest_targets: dict[int, dict[str, Any]],
    existing_entries: dict[str, dict[str, Any]],
    args: argparse.Namespace,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    selected: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for target in filtered_targets:
        index = source_index(target)
        if index < args.start_index:
            continue
        manifest_target = manifest_targets.get(index)
        if manifest_target is None:
            print(f"Skipping SourceIndex={index}: no three-view manifest target.", file=sys.stderr)
            continue
        key = entry_key(target)
        existing = existing_entries.get(key)
        if existing and existing.get("AnnotationStatus") == "completed" and not args.overwrite:
            continue
        if existing and existing.get("AnnotationStatus") != "completed" and not args.retry_failed and not args.overwrite:
            continue
        selected.append((target, manifest_target))
        if args.max_targets > 0 and len(selected) >= args.max_targets:
            break
    return selected


def run(args: argparse.Namespace) -> int:
    args.output_dir = args.output_dir.resolve()
    args.feature_tree_path = (args.feature_tree_path or args.output_dir / "feature-tree.json").resolve()
    args.filtered_feature_tree_path = (
        args.filtered_feature_tree_path or args.output_dir / "feature-tree-filtered-features.json"
    ).resolve()
    args.three_view_manifest_path = (
        args.three_view_manifest_path or args.output_dir / "three_views" / "three-view-manifest.json"
    ).resolve()
    args.annotation_output = (
        args.annotation_output or args.output_dir / "feature-structure-annotations.json"
    ).resolve()
    args.annotated_feature_tree_output = (
        args.annotated_feature_tree_output
        or args.output_dir / "feature-tree-with-structure-annotations.json"
    ).resolve()

    feature_tree = load_json(args.feature_tree_path)
    filtered = load_json(args.filtered_feature_tree_path)
    manifest = load_json(args.three_view_manifest_path)

    filtered_targets = json_get(filtered, "Targets", [])
    manifest_targets_list = json_get(manifest, "Targets", [])
    if not isinstance(filtered_targets, list):
        raise RuntimeError("Filtered feature tree Targets must be a list.")
    if not isinstance(manifest_targets_list, list):
        raise RuntimeError("Three-view manifest Targets must be a list.")

    manifest_targets = {
        source_index(target): target
        for target in manifest_targets_list
        if isinstance(target, dict) and source_index(target) >= 0
    }

    config: dict[str, Any] = {}
    if not args.merge_only and not args.dry_run:
        config = load_config(args.config.resolve())
    elif args.config.exists():
        config = load_config(args.config.resolve())
    model = str(args.model or config.get("model") or DEFAULT_MODEL).strip()
    base_url = resolve_base_url(config, args.base_url)
    endpoint = resolve_endpoint(base_url)

    existing_entries = [] if args.overwrite else load_existing_entries(args.annotation_output)
    entries_by_key = {entry_key(entry): entry for entry in existing_entries}
    selected = select_targets(filtered_targets, manifest_targets, entries_by_key, args)

    if args.dry_run:
        print(f"Feature tree: {args.feature_tree_path}")
        print(f"VLM model: {model}")
        print(f"VLM endpoint: {endpoint}")
        print(f"Filtered targets: {len(filtered_targets)}")
        print(f"Manifest targets: {len(manifest_targets)}")
        print(f"Existing annotations: {len(existing_entries)}")
        print(f"Pending selected targets: {len(selected)}")
        if selected:
            target, manifest_target = selected[0]
            print("\n--- System prompt ---")
            print(SYSTEM_PROMPT)
            print("\n--- User prompt for first selected target ---")
            print(build_user_prompt(target, manifest_target))
            print("\n--- Image paths ---")
            for view, path in image_paths_for_manifest_target(manifest_target).items():
                print(f"{view}: {path}")
        return 0

    if args.merge_only:
        annotation_set = build_annotation_set(existing_entries, args, model, endpoint)
        merged_tree, merged_count = merge_annotations_into_feature_tree(
            feature_tree,
            annotation_set["Entries"],
            args.annotation_output,
        )
        write_json(args.annotated_feature_tree_output, merged_tree)
        print(f"Merged {merged_count} annotations into {args.annotated_feature_tree_output}")
        return 0

    api_key = resolve_api_key(config)
    validate_endpoint_for_api_key(api_key, endpoint)
    print(f"Using VLM model: {model}")
    print(f"Using endpoint: {endpoint}")
    print(f"Targets selected for annotation: {len(selected)}")

    for ordinal, (target, manifest_target) in enumerate(selected, start=1):
        index = source_index(target)
        name = json_get(target, "Name", "<unnamed>")
        print(f"[{ordinal}/{len(selected)}] Annotating SourceIndex={index}: {name}")
        try:
            text = call_openai_compatible_chat(
                endpoint,
                api_key,
                model,
                build_messages(target, manifest_target),
                config,
            )
            raw_result = parse_json_object_from_text(text)
            entry = make_annotation_entry(
                target,
                manifest_target,
                raw_result,
                model,
                endpoint,
                status="completed",
            )
        except Exception as exc:
            if not args.keep_failed:
                raise
            entry = make_annotation_entry(
                target,
                manifest_target,
                None,
                model,
                endpoint,
                status="failed",
                error=str(exc),
            )
            print(f"  failed: {exc}", file=sys.stderr)

        entries_by_key[entry_key(entry)] = entry
        annotation_set = build_annotation_set(
            list(entries_by_key.values()),
            args,
            model,
            endpoint,
        )
        write_json(args.annotation_output, annotation_set)

    annotation_set = build_annotation_set(list(entries_by_key.values()), args, model, endpoint)
    write_json(args.annotation_output, annotation_set)
    merged_tree, merged_count = merge_annotations_into_feature_tree(
        feature_tree,
        annotation_set["Entries"],
        args.annotation_output,
    )
    write_json(args.annotated_feature_tree_output, merged_tree)

    print(f"Wrote annotations: {args.annotation_output}")
    print(f"Merged {merged_count} annotations into: {args.annotated_feature_tree_output}")
    print(
        f"completed={annotation_set['CompletedCount']} failed={annotation_set['FailedCount']} "
        f"total={annotation_set['TargetCount']}"
    )
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Use a Qwen-compatible VLM to classify highlighted feature three-view captures as structural/dimension-driving features."
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--feature-tree-path", type=Path)
    parser.add_argument("--filtered-feature-tree-path", type=Path)
    parser.add_argument("--three-view-manifest-path", type=Path)
    parser.add_argument("--annotation-output", type=Path)
    parser.add_argument("--annotated-feature-tree-output", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--max-targets", type=int, default=0, help="0 means all pending targets.")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument(
        "--keep-failed",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Write failed annotation entries and continue instead of stopping at the first failed VLM call.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and print the first prompt without calling the VLM.",
    )
    parser.add_argument(
        "--merge-only",
        action="store_true",
        help="Do not call the VLM; merge existing feature-structure-annotations.json into the feature tree.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(run(parse_args(sys.argv[1:])))
