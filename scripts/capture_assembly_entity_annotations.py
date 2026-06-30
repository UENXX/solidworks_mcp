#!/usr/bin/env python3
"""
Run SolidWorks assembly entity annotation capture outside an LLM/MCP client.

The script controls SolidWorksMcpApp through its stdio MCP proxy, first builds
a reusable target-index.json, then captures targets from that index in short
resumable batches. It continues from manifest.json if a call times out after
partial progress.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_INDEX_TOOL = "BuildActiveAssemblyEntityAnnotationTargetIndex"
INDEX_CAPTURE_TOOL = "CaptureActiveAssemblyEntityAnnotationTargetsFromIndex"
LEGACY_CAPTURE_TOOL = "CaptureActiveAssemblyEntityAnnotationSet"
FEATURE_TREE_TOOL = "ExportActiveDocumentFeatureTree"
FEATURE_THREE_VIEWS_TOOL = "CaptureFilteredFeatureTreeThreeViews"
FILTERED_FEATURE_TREE_TYPE_NAMES = {
    "EdgeFlange",
    "Extrusion",
    "ICE",
    "SMBaseFlange",
    "WeldMemberFeat",
}


class McpClient:
    def __init__(self, exe_path: Path, client_name: str, request_timeout: float, framing: str) -> None:
        self.exe_path = exe_path
        self.client_name = client_name
        self.request_timeout = request_timeout
        self.framing = framing
        self._next_id = 1
        self._proc: subprocess.Popen[bytes] | None = None
        self._messages: queue.Queue[dict[str, Any] | BaseException] = queue.Queue()
        self._reader_thread: threading.Thread | None = None

    def __enter__(self) -> "McpClient":
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def start(self) -> None:
        if self._proc is not None:
            return

        self._proc = subprocess.Popen(
            [
                str(self.exe_path),
                "--proxy",
                "--client",
                self.client_name,
            ],
            cwd=str(self.exe_path.parent),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._reader_thread = threading.Thread(target=self._reader_loop, name="mcp-reader", daemon=True)
        self._reader_thread.start()
        self.initialize()

    def close(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return

        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass

        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def initialize(self) -> None:
        response = self.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": self.client_name, "version": "1.0.0"},
            },
        )
        if "result" not in response:
            raise RuntimeError(f"MCP initialize failed: {response}")
        self.notify("notifications/initialized", {})

    def call_tool_text(self, tool_name: str, arguments: dict[str, Any]) -> str:
        response = self.request(
            "tools/call",
            {
                "name": to_mcp_tool_name(tool_name),
                "arguments": arguments,
            },
        )
        if "error" in response:
            raise RuntimeError(json.dumps(response["error"], ensure_ascii=False))

        result = response.get("result") or {}
        if result.get("isError"):
            raise RuntimeError(extract_tool_text(result) or json.dumps(result, ensure_ascii=False))

        return extract_tool_text(result)

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        text = self.call_tool_text(tool_name, arguments)
        if not text:
            return {}

        try:
            return loads_json(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Tool returned non-JSON text: {text}") from exc

    def list_tools(self) -> list[dict[str, Any]]:
        response = self.request("tools/list", {})
        if "error" in response:
            raise RuntimeError(json.dumps(response["error"], ensure_ascii=False))
        return list((response.get("result") or {}).get("tools") or [])

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        message_id = self._next_id
        self._next_id += 1
        self._send({"jsonrpc": "2.0", "id": message_id, "method": method, "params": params})

        deadline = time.monotonic() + self.request_timeout
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError(f"MCP request timed out after {self.request_timeout:.0f}s: {method}")

            message = self._read_message(deadline)
            if message.get("id") == message_id:
                return message

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _send(self, message: dict[str, Any]) -> None:
        proc = self._require_proc()
        if proc.stdin is None:
            raise RuntimeError("MCP proxy stdin is closed.")

        raw_json = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if self.framing == "content-length":
            payload = f"Content-Length: {len(raw_json)}\r\n\r\n".encode("ascii") + raw_json
        else:
            payload = raw_json + b"\n"
        proc.stdin.write(payload)
        proc.stdin.flush()

    def _read_message(self, deadline: float) -> dict[str, Any]:
        timeout = max(0.01, deadline - time.monotonic())
        try:
            message = self._messages.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError("Timed out while reading MCP response.") from exc

        if isinstance(message, BaseException):
            raise message
        return message

    def _reader_loop(self) -> None:
        try:
            proc = self._require_proc()
            stdout = proc.stdout
            if stdout is None:
                raise RuntimeError("MCP proxy stdout is closed.")

            while True:
                line = stdout.readline()
                if not line:
                    raise RuntimeError(read_process_failure(proc))

                if line.lower().startswith(b"content-length:"):
                    self._messages.put(read_content_length_message(stdout, line))
                    continue

                if not line.strip():
                    continue

                self._messages.put(loads_json(line))
        except BaseException as exc:
            self._messages.put(exc)

    def _require_proc(self) -> subprocess.Popen[bytes]:
        if self._proc is None:
            raise RuntimeError("MCP proxy is not running.")
        if self._proc.poll() is not None:
            raise RuntimeError(read_process_failure(self._proc))
        return self._proc


def extract_tool_text(result: dict[str, Any]) -> str:
    blocks = result.get("content") or []
    texts = [
        block.get("text", "")
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "text" and block.get("text")
    ]
    return "\n".join(texts)


def read_content_length_message(stdout: Any, first_header_line: bytes) -> dict[str, Any]:
    headers: dict[str, str] = {}
    current = first_header_line
    while True:
        if current in (b"\r\n", b"\n", b""):
            break
        key, _, value = current.decode("ascii", errors="replace").partition(":")
        headers[key.strip().lower()] = value.strip()
        current = stdout.readline()

    length_text = headers.get("content-length")
    if not length_text:
        raise RuntimeError(f"MCP response did not include Content-Length: {headers}")

    length = int(length_text)
    body = stdout.read(length)
    if len(body) != length:
        raise RuntimeError("MCP stream ended before a full Content-Length body was read.")
    return loads_json(body)


def loads_json(value: str | bytes) -> Any:
    if isinstance(value, bytes):
        return json.loads(value.decode("utf-8-sig"))
    return json.loads(value.lstrip("\ufeff"))


def read_process_failure(proc: subprocess.Popen[bytes]) -> str:
    stderr = b""
    try:
        if proc.stderr:
            stderr = proc.stderr.read() or b""
    except Exception:
        pass
    detail = stderr.decode("utf-8", errors="replace").strip()
    code = proc.poll()
    return f"MCP proxy exited or closed the stream. exit_code={code}, stderr={detail}"


def to_mcp_tool_name(name: str) -> str:
    if "_" in name:
        return name.lower()

    result: list[str] = []
    for index, current in enumerate(name):
        if current.isupper():
            has_previous = index > 0
            previous = name[index - 1] if has_previous else ""
            next_char = name[index + 1] if index + 1 < len(name) else ""
            if has_previous and (previous.islower() or previous.isdigit() or next_char.islower()):
                result.append("_")
            result.append(current.lower())
        else:
            result.append(current)
    return "".join(result)


def resolve_exe_path(configured: str | None) -> Path:
    if configured:
        path = Path(configured).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"SolidWorksMcpApp.exe not found: {path}")
        return path

    env_path = os.environ.get("SOLIDWORKS_MCP_APP_EXE")
    if env_path:
        return resolve_exe_path(env_path)

    artifact_root = REPO_ROOT / "artifacts"
    candidates = list(artifact_root.glob("solidworks-mcp*/SolidWorksMcpApp.exe"))
    candidates.extend(
        [
            REPO_ROOT
            / "vendor"
            / "solidworks-mcp"
            / "app"
            / "SolidWorksMcpApp"
            / "bin"
            / "Release"
            / "net8.0-windows"
            / "win-x64"
            / "SolidWorksMcpApp.exe",
            REPO_ROOT
            / "vendor"
            / "solidworks-mcp"
            / "app"
            / "SolidWorksMcpApp"
            / "bin"
            / "Debug"
            / "net8.0-windows"
            / "win-x64"
            / "SolidWorksMcpApp.exe",
        ]
    )
    existing = [path for path in candidates if path.exists()]
    if not existing:
        raise FileNotFoundError(
            "Could not locate SolidWorksMcpApp.exe. Pass --exe or set SOLIDWORKS_MCP_APP_EXE."
        )
    return max(existing, key=lambda path: path.stat().st_mtime).resolve()


def load_manifest(output_directory: Path) -> dict[str, Any] | None:
    path = output_directory / "manifest.json"
    if not path.exists():
        return None
    return loads_json(path.read_text(encoding="utf-8-sig"))


def load_target_index(output_directory: Path) -> dict[str, Any] | None:
    path = output_directory / "target-index.json"
    if not path.exists():
        return None
    return loads_json(path.read_text(encoding="utf-8-sig"))


def count_existing_targets(output_directory: Path) -> int:
    manifest = load_manifest(output_directory)
    if not manifest:
        return 0
    targets = json_get(manifest, "targets", [])
    return len(targets)


def safe_path_segment(value: str, fallback: str = "untitled") -> str:
    stripped = value.strip()
    if not stripped:
        stripped = fallback
    result = "".join(char if char.isalnum() or char in ("-", "_", ".") else "_" for char in stripped)
    result = result.strip("._ ")
    return result or fallback


def active_document_stem_from_feature_tree_result(result: dict[str, Any]) -> str:
    active_path = str(json_get(result, "activeDocumentPath", "") or "").strip()
    if active_path:
        return Path(active_path).stem
    active_title = str(json_get(result, "activeDocumentTitle", "") or "").strip()
    if active_title:
        return Path(active_title).stem
    return "active_document"


def default_feature_tree_output_directory(base_output_dir: Path, active_stem: str) -> Path:
    desired_name = f"featru_tree_{safe_path_segment(active_stem)}"
    if base_output_dir.name == desired_name:
        return base_output_dir.resolve()
    parent = base_output_dir.parent if base_output_dir.name else base_output_dir
    return (parent / desired_name).resolve()


def json_get(data: dict[str, Any], key: str, default: Any = None) -> Any:
    if key in data:
        return data[key]
    pascal_key = key[:1].upper() + key[1:]
    if pascal_key in data:
        return data[pascal_key]
    return default


def json_int(data: dict[str, Any], key: str, default: int = 0) -> int:
    value = json_get(data, key, default)
    return default if value is None else int(value)


def json_bool(data: dict[str, Any], key: str, default: bool = False) -> bool:
    value = json_get(data, key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("1", "true", "yes", "y"):
            return True
        if normalized in ("0", "false", "no", "n", ""):
            return False
    if value is None:
        return default
    return bool(value)


def feature_tree_node_kind(node: dict[str, Any]) -> str:
    kind = str(json_get(node, "entityKind", "") or "").lower()
    if kind:
        return kind
    if json_get(node, "featurePath") is not None or json_get(node, "nodeId") is not None:
        return "feature"
    return "document"


def feature_tree_children(node: dict[str, Any]) -> list[dict[str, Any]]:
    children = json_get(node, "children", [])
    if not isinstance(children, list):
        return []
    return [child for child in children if isinstance(child, dict)]


def feature_tree_features(node: dict[str, Any]) -> list[dict[str, Any]]:
    features = json_get(node, "features", [])
    if not isinstance(features, list):
        return []
    return [feature for feature in features if isinstance(feature, dict)]


def feature_tree_direct_feature_children(node: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        child
        for child in feature_tree_children(node)
        if feature_tree_node_kind(child) == "feature"
    ]


def count_feature_nodes(nodes: list[dict[str, Any]]) -> int:
    total = 0
    for node in nodes:
        kind = feature_tree_node_kind(node)
        if kind == "feature":
            total += 1
        for child in feature_tree_children(node):
            total += count_feature_nodes([child])
    return total


def iter_feature_tree_documents(root: dict[str, Any]) -> Any:
    stack = [root]
    while stack:
        node = stack.pop()
        if feature_tree_node_kind(node) == "document":
            yield node
        stack.extend(reversed(feature_tree_children(node)))


def count_mixed_tree_features(root: dict[str, Any]) -> int:
    return count_feature_nodes(feature_tree_children(root))


def print_feature_tree_file_summary(output_directory: Path, filename: str = "feature-tree.json") -> None:
    path = output_directory / filename
    if not path.exists():
        return

    data = loads_json(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        return
    root = json_get(data, "rootDocument")
    if not isinstance(root, dict):
        return

    documents = list(iter_feature_tree_documents(root))
    docs_with_features = [doc for doc in documents if feature_tree_direct_feature_children(doc)]
    part_docs = [
        doc
        for doc in documents
        if str(json_get(doc, "typeName", "") or "").lower() == "part"
    ]
    part_docs_with_features = [
        doc for doc in part_docs if feature_tree_direct_feature_children(doc)
    ]
    skipped_docs = [doc for doc in documents if bool(json_get(doc, "featuresSkipped", False))]
    feature_count = count_mixed_tree_features(root)
    root_children = feature_tree_children(root)
    print(
        "Feature tree file summary: "
        f"schema={json_get(data, 'schemaVersion', '<unknown>')}, "
        f"documents={len(documents)}, "
        f"features={feature_count}, "
        f"docsWithFeatures={len(docs_with_features)}, "
        f"partDocsWithFeatures={len(part_docs_with_features)}/{len(part_docs)}, "
        f"skippedDocs={len(skipped_docs)}, "
        f"rootChildren={len(root_children)}"
    )


def resolve_feature_tree_input_path(output_directory: Path, configured: str | Path | None) -> Path:
    if not configured:
        return (output_directory / "feature-tree.json").resolve()

    path = Path(configured).expanduser()
    if path.is_absolute():
        return path.resolve()

    output_relative = output_directory / path
    if output_relative.exists() or not path.exists():
        return output_relative.resolve()
    return path.resolve()


def resolve_feature_tree_output_path(output_directory: Path, configured: str | Path) -> Path:
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = output_directory / path
    return path.resolve()


def feature_tree_document_context(document: dict[str, Any] | None) -> dict[str, Any]:
    if not document:
        return {}

    path = json_get(document, "path")
    return {"Path": path} if path is not None else {}


def feature_tree_value_with_document_fallback(
    feature: dict[str, Any],
    feature_key: str,
    document: dict[str, Any] | None,
    document_key: str,
) -> Any:
    value = json_get(feature, feature_key)
    if value not in (None, ""):
        return value
    if not document:
        return value
    return json_get(document, document_key)


def is_filtered_feature_tree_target(node: dict[str, Any]) -> bool:
    entity_kind = str(json_get(node, "entityKind", "") or "").lower()
    feature_type_name = str(json_get(node, "featureTypeName", "") or "")
    return (
        entity_kind == "feature"
        and json_bool(node, "hasSubFeatures", False)
        and feature_type_name in FILTERED_FEATURE_TREE_TYPE_NAMES
    )


def build_filtered_feature_tree_target(
    feature: dict[str, Any],
    parent_document: dict[str, Any] | None,
    source_index: int,
) -> dict[str, Any]:
    document_context = feature_tree_document_context(parent_document)
    return {
        "SourceIndex": source_index,
        "EntityKind": "feature",
        "NodeId": json_get(feature, "nodeId"),
        "Name": json_get(feature, "name"),
        "FeatureTypeName": json_get(feature, "featureTypeName"),
        "FeaturePath": json_get(feature, "featurePath"),
        "GraphPath": json_get(feature, "graphPath"),
        "HasSubFeatures": json_bool(feature, "hasSubFeatures", False),
        "SubFeaturesOmitted": json_get(feature, "subFeaturesOmitted"),
        "SubFeaturesOmittedReason": json_get(feature, "subFeaturesOmittedReason"),
        "Depth": json_get(feature, "depth"),
        "SiblingIndex": json_get(feature, "siblingIndex"),
        "DocumentId": feature_tree_value_with_document_fallback(
            feature, "documentId", parent_document, "documentId"
        ),
        "DocumentTitle": feature_tree_value_with_document_fallback(
            feature, "documentTitle", parent_document, "title"
        ),
        "DocumentPath": feature_tree_value_with_document_fallback(
            feature, "documentPath", parent_document, "path"
        ),
        "ComponentName": feature_tree_value_with_document_fallback(
            feature, "componentName", parent_document, "componentName"
        ),
        "ComponentRawName": feature_tree_value_with_document_fallback(
            feature, "componentRawName", parent_document, "componentRawName"
        ),
        "ComponentPath": feature_tree_value_with_document_fallback(
            feature, "componentPath", parent_document, "componentPath"
        ),
        "HierarchyPath": feature_tree_value_with_document_fallback(
            feature, "hierarchyPath", parent_document, "hierarchyPath"
        ),
        "ComponentDepth": feature_tree_value_with_document_fallback(
            feature, "componentDepth", parent_document, "componentDepth"
        ),
        "ParentDocument": document_context,
    }


def collect_filtered_feature_tree_targets(root: dict[str, Any]) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    root_document = root if feature_tree_node_kind(root) == "document" else None
    stack: list[tuple[dict[str, Any], dict[str, Any] | None]] = [(root, root_document)]

    while stack:
        node, parent_document = stack.pop()
        kind = feature_tree_node_kind(node)
        current_document = node if kind == "document" else parent_document
        if is_filtered_feature_tree_target(node):
            targets.append(
                build_filtered_feature_tree_target(
                    node,
                    current_document,
                    len(targets),
                )
            )
        for child in reversed(feature_tree_children(node)):
            stack.append((child, current_document))

    return targets


def filter_feature_tree_targets(feature_tree_path: Path, output_path: Path) -> dict[str, Any]:
    if not feature_tree_path.exists():
        raise FileNotFoundError(f"Feature tree JSON not found: {feature_tree_path}")

    data = loads_json(feature_tree_path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise RuntimeError(f"Feature tree JSON root must be an object: {feature_tree_path}")

    root = json_get(data, "rootDocument")
    if not isinstance(root, dict):
        raise RuntimeError(f"Feature tree JSON does not contain RootDocument: {feature_tree_path}")

    documents = list(iter_feature_tree_documents(root))
    targets = collect_filtered_feature_tree_targets(root)
    result = {
        "SchemaVersion": "solidworks-mcp.feature-tree-filter.v1",
        "CreatedUtc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "SourceFeatureTreePath": str(feature_tree_path.resolve()),
        "OutputPath": str(output_path.resolve()),
        "SourceSchemaVersion": json_get(data, "schemaVersion"),
        "SourceCreatedUtc": json_get(data, "createdUtc"),
        "SourceFromJournal": json_bool(data, "fromJournal", False),
        "Predicate": {
            "EntityKind": "feature",
            "HasSubFeatures": True,
            "FeatureTypeNameIn": sorted(FILTERED_FEATURE_TREE_TYPE_NAMES),
        },
        "DocumentCount": len(documents),
        "SourceFeatureCount": count_mixed_tree_features(root),
        "TargetCount": len(targets),
        "Targets": targets,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        "Filtered feature tree targets: "
        f"documents={result['DocumentCount']}, "
        f"sourceFeatures={result['SourceFeatureCount']}, "
        f"targets={result['TargetCount']}, "
        f"path={output_path}"
    )
    return result


def count_feature_tree_document_journal_entries(output_directory: Path) -> int:
    path = output_directory / "feature-tree-documents.jsonl"
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
        return sum(1 for line in handle if line.strip())


def load_feature_tree_document_journal(output_directory: Path) -> list[dict[str, Any]]:
    path = output_directory / "feature-tree-documents.jsonl"
    if not path.exists():
        return []

    entries: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            item = loads_json(line)
            if isinstance(item, dict):
                entries.append(item)
    return entries


def feature_tree_document_path_parts(document: dict[str, Any]) -> list[str]:
    hierarchy_path = str(json_get(document, "hierarchyPath", "") or "").strip()
    if not hierarchy_path:
        return []
    return [part for part in hierarchy_path.replace("\\", "/").split("/") if part]


def clone_feature_tree_document_for_journal(document: dict[str, Any]) -> dict[str, Any]:
    cloned = json.loads(json.dumps(document, ensure_ascii=False))
    children = feature_tree_children(cloned)
    cloned.pop("children", None)
    cloned["Children"] = [
        child
        for child in children
        if feature_tree_node_kind(child) != "document"
    ]
    return cloned


def create_journal_placeholder_document(name: str, hierarchy_path: str, depth: int) -> dict[str, Any]:
    return {
        "EntityKind": "document",
        "DocumentId": f"journal_placeholder_{abs(hash(hierarchy_path))}",
        "Role": "journal-placeholder",
        "Title": name,
        "ComponentName": name,
        "ComponentRawName": name,
        "HierarchyPath": hierarchy_path,
        "ComponentDepth": depth,
        "IsLoaded": False,
        "LoadStatus": "journal parent placeholder",
        "FeaturesSkipped": True,
        "FeaturesSkippedReason": "This parent document was reconstructed from child journal paths but was not present as a completed journal document.",
        "Children": [],
    }


def count_feature_tree_documents_from_node(node: dict[str, Any]) -> int:
    total = 1 if feature_tree_node_kind(node) == "document" else 0
    for child in feature_tree_children(node):
        total += count_feature_tree_documents_from_node(child)
    return total


def rebuild_feature_tree_from_document_journal(
    output_directory: Path,
    output_filename: str = "feature-tree-from-journal.json",
) -> dict[str, Any]:
    entries = load_feature_tree_document_journal(output_directory)
    if not entries:
        raise FileNotFoundError(
            f"No feature-tree-documents.jsonl entries found in {output_directory}"
        )

    documents_by_path: dict[str, dict[str, Any]] = {}
    path_order: list[str] = []
    for entry in entries:
        document = json_get(entry, "document")
        if not isinstance(document, dict):
            continue
        hierarchy_path = str(json_get(document, "hierarchyPath", "") or "").strip()
        if not hierarchy_path:
            hierarchy_path = str(json_get(entry, "hierarchyPath", "") or "").strip()
            if hierarchy_path:
                document["hierarchyPath"] = hierarchy_path
        if not hierarchy_path:
            continue
        cloned = clone_feature_tree_document_for_journal(document)
        if hierarchy_path not in documents_by_path:
            path_order.append(hierarchy_path)
        documents_by_path[hierarchy_path] = cloned

    if not documents_by_path:
        raise RuntimeError("feature-tree-documents.jsonl did not contain reconstructable document entries.")

    root = {
        "EntityKind": "document",
        "DocumentId": "journal_reconstructed_root",
        "Role": "journal-root",
        "Title": "journal-reconstructed-root",
        "Type": 0,
        "TypeName": "Unknown",
        "IsLoaded": False,
        "LoadStatus": "journal reconstructed",
        "FeaturesSkipped": True,
        "FeaturesSkippedReason": "Synthetic root for a feature tree reconstructed from feature-tree-documents.jsonl.",
        "Children": [],
    }
    nodes_by_path: dict[str, dict[str, Any]] = {"": root}

    def ensure_parent(path: str) -> dict[str, Any]:
        if path in nodes_by_path:
            return nodes_by_path[path]
        parts = [part for part in path.split("/") if part]
        if not parts:
            return root
        parent_path = "/".join(parts[:-1])
        parent = ensure_parent(parent_path)
        placeholder = create_journal_placeholder_document(parts[-1], path, len(parts) - 1)
        parent.setdefault("Children", []).append(placeholder)
        nodes_by_path[path] = placeholder
        return placeholder

    for hierarchy_path in sorted(path_order, key=lambda value: (value.count("/"), value)):
        document = documents_by_path[hierarchy_path]
        parts = feature_tree_document_path_parts(document)
        parent_path = "/".join(parts[:-1])
        parent = ensure_parent(parent_path)
        existing = nodes_by_path.get(hierarchy_path)
        if existing is not None and json_get(existing, "role") == "journal-placeholder":
            existing.clear()
            existing.update(document)
        else:
            parent.setdefault("Children", []).append(document)
        nodes_by_path[hierarchy_path] = document

    output_path = output_directory / output_filename
    result = {
        "SchemaVersion": "solidworks-mcp.feature-tree.v4",
        "CreatedUtc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "OutputDirectory": str(output_directory.resolve()),
        "FeatureTreePath": str(output_path.resolve()),
        "DocumentJournalPath": str((output_directory / "feature-tree-documents.jsonl").resolve()),
        "FromJournal": True,
        "DocumentJournalEntries": len(entries),
        "DocumentCount": count_feature_tree_documents_from_node(root),
        "FeatureCount": count_mixed_tree_features(root),
        "RootDocument": root,
    }
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        "Feature tree rebuilt from document journal: "
        f"documentJournalEntries={len(entries)}, "
        f"documentCount={result['DocumentCount']}, "
        f"featureCount={result['FeatureCount']}, "
        f"path={output_path}"
    )
    print_feature_tree_file_summary(output_directory, output_filename)
    return result


def restart_solidworks_mcp_app(exe_path: Path) -> None:
    if os.name != "nt":
        return
    subprocess.run(
        ["taskkill", "/F", "/T", "/IM", exe_path.name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def close_client(client: McpClient | None) -> None:
    if client is not None:
        client.close()


def build_index_arguments(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "outputDirectory": str(args.output_dir),
        "includeComponents": args.include_components,
        "includeFeatures": args.include_features,
        "includeBodies": args.include_bodies,
        "requireFeatureTypeName": args.require_feature_type_name,
        "overwrite": args.rebuild_index,
    }


def build_feature_tree_arguments(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "outputDirectory": str(args.output_dir),
        "overwrite": args.overwrite_feature_tree,
        "expandManagementFeatureSubTrees": args.expand_management_feature_subtrees,
        "expandFeatureSubTrees": args.expand_feature_subtrees,
        "includeComponentFeatures": args.include_component_features,
        "includePartFeatures": args.include_part_features,
        "activeDocumentOnly": args.active_document_only,
        "skipFeatureDocumentPaths": [str(path.resolve()) for path in args.skip_feature_document],
        "appendDocumentJournal": args.append_document_journal,
        "skipImportedFeatureDocuments": args.skip_imported_feature_documents,
        "exportDuplicateSourceDocumentFeatures": args.export_duplicate_source_document_features,
    }


def build_capture_arguments(args: argparse.Namespace, start_index: int, batch_size: int) -> dict[str, Any]:
    common = {
        "outputDirectory": str(args.output_dir),
        "width": args.width,
        "height": args.height,
        "maxTargets": batch_size,
        "startIndex": start_index,
        "skipExistingTargets": True,
        "writeManifestAfterEachTarget": True,
        "maxDurationSeconds": args.tool_time_budget,
        "useCleanDisplayMode": args.clean_display,
        "capturePaddingFactor": args.padding,
    }
    if args.legacy_capture:
        common.update(
            {
                "includeComponents": args.include_components,
                "includeFeatures": args.include_features,
                "includeBodies": args.include_bodies,
                "requireFeatureTypeName": args.require_feature_type_name,
            }
        )
    else:
        common["sourceIndex"] = start_index
        common["maxTargets"] = 1
    return common


def build_feature_three_views_arguments(args: argparse.Namespace, start_index: int) -> dict[str, Any]:
    filtered_path = resolve_feature_tree_output_path(args.output_dir, args.filtered_feature_tree_output)
    return {
        "filteredFeatureTreePath": str(filtered_path),
        "width": args.width,
        "height": args.height,
        "startIndex": start_index,
        "maxTargets": args.batch_size,
        "skipExistingTargets": args.resume,
        "writeManifestAfterEachTarget": True,
        "maxDurationSeconds": args.tool_time_budget,
        "capturePaddingFactor": args.padding,
        "overwrite": args.overwrite_three_views and start_index == args.start_index,
        "maxTransparentFaces": args.max_transparent_faces,
    }


def ensure_target_index(client: McpClient, args: argparse.Namespace) -> dict[str, Any] | None:
    if args.legacy_capture:
        return None

    existing = None if args.rebuild_index else load_target_index(args.output_dir)
    if existing:
        print(
            "Using existing target index: "
            f"targetCount={json_int(existing, 'targetCount', 0)}, "
            f"path={json_get(existing, 'indexPath', str(args.output_dir / 'target-index.json'))}"
        )
        return existing

    print("Building target index without screenshots...")
    result = client.call_tool(BUILD_INDEX_TOOL, build_index_arguments(args))
    print(
        "Target index ready: "
        f"targetCount={json_int(result, 'targetCount', 0)}, "
        f"path={json_get(result, 'indexPath', str(args.output_dir / 'target-index.json'))}"
    )
    return result


def export_feature_tree(client: McpClient, args: argparse.Namespace) -> dict[str, Any]:
    mode = "active document only" if args.active_document_only else "recursive"
    print(f"Exporting active document feature tree ({mode})...")
    started = time.perf_counter()
    result = client.call_tool(FEATURE_TREE_TOOL, build_feature_tree_arguments(args))
    elapsed_seconds = time.perf_counter() - started
    print(
        "Feature tree ready: "
        f"documentCount={json_int(result, 'documentCount', 0)}, "
        f"featureCount={json_int(result, 'featureCount', 0)}, "
        f"elapsed={elapsed_seconds:.3f}s, "
        f"path={json_get(result, 'featureTreePath', str(args.output_dir / 'feature-tree.json'))}, "
        f"documentJournalPath={json_get(result, 'documentJournalPath', str(args.output_dir / 'feature-tree-documents.jsonl'))}, "
        f"documentJournalEntries={count_feature_tree_document_journal_entries(args.output_dir)}"
    )
    print_feature_tree_file_summary(args.output_dir)
    return result


def normalize_feature_tree_output_directory(args: argparse.Namespace, result: dict[str, Any]) -> dict[str, Any]:
    if not args.use_active_document_output_dir:
        return result

    active_stem = active_document_stem_from_feature_tree_result(result)
    desired_dir = default_feature_tree_output_directory(args.output_dir, active_stem)
    current_dir = args.output_dir.resolve()
    if desired_dir == current_dir:
        return result

    desired_dir.mkdir(parents=True, exist_ok=True)
    for filename in (
        "feature-tree.json",
        "feature-tree-from-journal.json",
        "feature-tree-documents.jsonl",
        "feature-tree-progress.json",
    ):
        source = current_dir / filename
        if source.exists():
            shutil.copy2(source, desired_dir / filename)

    args.output_dir = desired_dir
    print(f"Feature tree output directory normalized: {desired_dir}")
    return result


def load_feature_tree_progress(output_directory: Path) -> dict[str, Any] | None:
    path = output_directory / "feature-tree-progress.json"
    if not path.exists():
        return None
    try:
        return loads_json(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def current_progress_document_path(output_directory: Path) -> str | None:
    progress = load_feature_tree_progress(output_directory)
    if not progress:
        return None
    value = json_get(progress, "currentDocumentPath")
    return str(value) if value else None


def run_feature_tree_export_with_recovery(exe_path: Path, args: argparse.Namespace) -> None:
    args.output_dir.mkdir(parents=True, exist_ok=True)
    skipped_paths = {str(path.resolve()) for path in args.skip_feature_document}
    attempts = 0
    last_journal_entries = count_feature_tree_document_journal_entries(args.output_dir)

    while True:
        attempts += 1
        args.skip_feature_document = [Path(path) for path in sorted(skipped_paths)]
        client: McpClient | None = None
        try:
            client = McpClient(exe_path, "Python Feature Tree Export", args.request_timeout, args.framing)
            client.start()
            prepare_solidworks_session(client, args)
            result = export_feature_tree(client, args)
            normalize_feature_tree_output_directory(args, result)
            if skipped_paths:
                print("Skipped feature documents:")
                for path in sorted(skipped_paths):
                    print(f"  {path}")
            return
        except TimeoutError as exc:
            print(f"Feature tree export timed out: {exc}")
            journal_entries = count_feature_tree_document_journal_entries(args.output_dir)
            if journal_entries:
                print(f"Feature tree document journal entries written so far: {journal_entries}")
            stuck_path = current_progress_document_path(args.output_dir)
            close_client(client)
            client = None
            if args.restart_mcp_after_feature_timeout:
                print("Restarting SolidWorksMcpApp.exe before retrying; SolidWorks itself is left running.")
                restart_solidworks_mcp_app(exe_path)
            if not args.auto_skip_stuck_feature_documents:
                raise
            if not stuck_path:
                raise RuntimeError(
                    "Feature tree export timed out, but feature-tree-progress.json did not contain currentDocumentPath."
                ) from exc

            normalized_stuck_path = str(Path(stuck_path).resolve())
            if normalized_stuck_path in skipped_paths:
                if journal_entries <= last_journal_entries:
                    if args.rebuild_feature_tree_from_journal_on_failure and journal_entries:
                        rebuild_feature_tree_from_document_journal(
                            args.output_dir,
                            args.rebuild_feature_tree_from_journal_output,
                        )
                    raise RuntimeError(
                        "Feature tree export timed out again on a document that is already in the skip list: "
                        f"{normalized_stuck_path}"
                    ) from exc
                print(
                    "Feature tree export made journal progress despite the stale progress path; "
                    f"continuing retry loop ({last_journal_entries} -> {journal_entries} entries)."
                )
            else:
                skipped_paths.add(normalized_stuck_path)
                print(f"Auto-skipping stuck feature document and retrying: {normalized_stuck_path}")
            if args.max_auto_skips and len(skipped_paths) > args.max_auto_skips:
                raise RuntimeError(
                    f"Exceeded --max-auto-skips={args.max_auto_skips}; last stuck document: {normalized_stuck_path}"
                ) from exc
            args.overwrite_feature_tree = True
            args.append_document_journal = True
            last_journal_entries = max(last_journal_entries, journal_entries)
            time.sleep(args.retry_delay)
        finally:
            close_client(client)


def run_filtered_feature_three_views(exe_path: Path, args: argparse.Namespace) -> None:
    filtered_path = resolve_feature_tree_output_path(args.output_dir, args.filtered_feature_tree_output)
    if not filtered_path.exists():
        raise FileNotFoundError(
            f"Filtered feature tree target file does not exist: {filtered_path}. "
            "Run --filter-feature-tree first."
        )

    start_index = args.start_index
    client: McpClient | None = None
    batches = 0
    attempts = 0
    try:
        while True:
            if args.max_batches and batches >= args.max_batches:
                print(f"Reached --max-batches={args.max_batches}; stopping.")
                return

            batches += 1
            attempts += 1
            print(
                f"\nThree-view batch {batches}: startIndex={start_index}, "
                f"batchSize={args.batch_size}, toolBudget={args.tool_time_budget}s"
            )
            try:
                if client is None:
                    client = McpClient(exe_path, "Python Filtered Feature Three Views", args.request_timeout, args.framing)
                    client.start()
                    prepare_solidworks_session(client, args)

                result = client.call_tool(FEATURE_THREE_VIEWS_TOOL, build_feature_three_views_arguments(args, start_index))
            except TimeoutError as exc:
                print(f"Three-view capture timed out: {exc}")
                close_client(client)
                client = None
                if attempts > args.max_retries:
                    raise
                time.sleep(args.retry_delay)
                continue
            except Exception as exc:
                print(f"Three-view capture failed: {exc}")
                close_client(client)
                client = None
                if attempts > args.max_retries:
                    raise
                time.sleep(args.retry_delay)
                continue

            attempts = 0
            processed = json_int(result, "processedThisRun", 0)
            total = json_int(result, "totalTargetCount", 0)
            start_index = json_int(result, "nextStartIndex", start_index + processed)
            stopped_reason = json_get(result, "stoppedReason")
            output_directory = json_get(result, "outputDirectory", str(args.output_dir / "three_views"))
            print(
                "Three-view batch result: "
                f"processed={processed}, "
                f"total={total}, "
                f"nextStartIndex={start_index}, "
                f"stoppedReason={stopped_reason}, "
                f"outputDirectory={output_directory}"
            )
            if stopped_reason == "completed" or start_index >= total > 0:
                return
    finally:
        close_client(client)


def prepare_solidworks_session(client: McpClient, args: argparse.Namespace) -> None:
    if args.force_reconnect:
        try:
            client.call_tool_text("SolidWorksDisconnect", {})
        except Exception as exc:
            if args.verbose:
                print(f"Disconnect before reconnect ignored: {exc}")

    if args.connect:
        connect_result = client.call_tool("SolidWorksConnect", {})
        if args.verbose:
            print(f"Connect result: {json.dumps(connect_result, ensure_ascii=False)}")

    if args.document:
        open_result = client.call_tool("OpenDocument", {"path": str(args.document.resolve())})
        if args.verbose:
            print(f"OpenDocument result: {json.dumps(open_result, ensure_ascii=False)}")

    list_result = client.call_tool("ListDocuments", {})
    documents = list_result if isinstance(list_result, list) else []
    active_text = client.call_tool_text("GetActiveDocument", {})
    active_doc = parse_nullable_json(active_text)

    print(f"SolidWorks documents visible to MCP: {len(documents)}")
    if active_doc:
        print(
            "Active document visible to MCP: "
            f"{json_get(active_doc, 'title', '<untitled>')} | {json_get(active_doc, 'path', '')}"
        )
    else:
        print("Active document visible to MCP: <none>")

    if not active_doc:
        document_paths = [
            str(json_get(item, "path", ""))
            for item in documents
            if isinstance(item, dict) and json_get(item, "path")
        ]
        hint = (
            "MCP is connected to a SolidWorks session with no active document. "
            "Pass --document <full .SLDASM path>, or close/restart the MCP tray/Hub and SolidWorks so they run in the same user session."
        )
        if document_paths:
            hint += " Documents visible to MCP: " + "; ".join(document_paths)
        raise RuntimeError(hint)


def parse_nullable_json(text: str) -> Any:
    stripped = text.strip()
    if not stripped or stripped.lower() == "null":
        return None
    return loads_json(stripped)


def wait_for_manifest_progress(
    output_directory: Path,
    previous_count: int,
    previous_start_index: int,
    timeout_seconds: float,
) -> tuple[dict[str, Any] | None, int, int]:
    deadline = time.monotonic() + max(0, timeout_seconds)
    manifest = load_manifest(output_directory)
    current_count = len(json_get(manifest or {}, "targets", []))
    next_start_index = json_int(manifest or {}, "nextStartIndex", previous_start_index)

    while time.monotonic() < deadline:
        if current_count > previous_count or next_start_index > previous_start_index:
            return manifest, current_count, next_start_index
        time.sleep(0.25)
        manifest = load_manifest(output_directory)
        current_count = len(json_get(manifest or {}, "targets", []))
        next_start_index = json_int(manifest or {}, "nextStartIndex", previous_start_index)

    return manifest, current_count, next_start_index


def run_capture(args: argparse.Namespace) -> int:
    args.output_dir = args.output_dir.resolve()
    offline_only = args.rebuild_feature_tree_from_journal or args.filter_feature_tree
    exe_path: Path | None = None if offline_only else resolve_exe_path(args.exe)

    start_index = args.start_index
    if not args.probe_only:
        args.output_dir.mkdir(parents=True, exist_ok=True)

    if args.resume and not args.probe_only:
        manifest = load_manifest(args.output_dir)
        if manifest:
            start_index = json_int(manifest, "nextStartIndex", start_index)

    print(f"Output directory: {args.output_dir}")
    print(f"Starting at target index: {start_index}")
    if exe_path is not None:
        print(f"Using MCP app: {exe_path}")

    if args.probe_only:
        with McpClient(exe_path, "Python Entity Annotation Capture Probe", args.request_timeout, args.framing) as client:
            tools = client.list_tools()
            names = sorted(tool.get("name", "") for tool in tools)
            build_index_tool = to_mcp_tool_name(BUILD_INDEX_TOOL)
            index_capture_tool = to_mcp_tool_name(INDEX_CAPTURE_TOOL)
            legacy_capture_tool = to_mcp_tool_name(LEGACY_CAPTURE_TOOL)
            print(f"Connected. Tool count: {len(names)}")
            print(f"Build index tool available: {build_index_tool in names} ({build_index_tool})")
            print(f"Index capture tool available: {index_capture_tool in names} ({index_capture_tool})")
            print(f"Legacy capture tool available: {legacy_capture_tool in names} ({legacy_capture_tool})")
            feature_tree_tool = to_mcp_tool_name(FEATURE_TREE_TOOL)
            print(f"Feature tree export tool available: {feature_tree_tool in names} ({feature_tree_tool})")
            feature_three_views_tool = to_mcp_tool_name(FEATURE_THREE_VIEWS_TOOL)
            print(f"Filtered feature three-view tool available: {feature_three_views_tool in names} ({feature_three_views_tool})")
            if args.verbose:
                for name in names:
                    print(f"  {name}")
        return 0

    if args.document and not args.document.exists():
        raise FileNotFoundError(f"Document path does not exist: {args.document}")

    if args.rebuild_feature_tree_from_journal:
        rebuild_feature_tree_from_document_journal(
            args.output_dir,
            args.rebuild_feature_tree_from_journal_output,
        )
        return 0

    if args.filter_feature_tree:
        feature_tree_path = resolve_feature_tree_input_path(args.output_dir, args.feature_tree_path)
        filtered_output_path = resolve_feature_tree_output_path(
            args.output_dir,
            args.filtered_feature_tree_output,
        )
        filter_feature_tree_targets(feature_tree_path, filtered_output_path)
        return 0

    if args.feature_tree_only:
        if exe_path is None:
            raise RuntimeError("SolidWorksMcpApp.exe is required for --feature-tree-only.")
        run_feature_tree_export_with_recovery(exe_path, args)
        return 0

    if args.capture_filtered_feature_three_views:
        if exe_path is None:
            raise RuntimeError("SolidWorksMcpApp.exe is required for --capture-filtered-feature-three-views.")
        run_filtered_feature_three_views(exe_path, args)
        return 0

    completed = False
    attempts = 0
    batches = 0
    last_target_count = count_existing_targets(args.output_dir)
    effective_batch_size = args.batch_size if args.legacy_capture else 1
    stuck_timeouts: dict[int, int] = {}
    client: McpClient | None = None
    indexed_target_count = 0
    index_prepared = False

    try:
        while not completed:
            if args.max_batches and batches >= args.max_batches:
                print(f"Reached --max-batches={args.max_batches}; stopping.")
                break

            batches += 1
            attempts += 1
            capture_args = build_capture_arguments(args, start_index, effective_batch_size)
            print(
                f"\nBatch {batches}: startIndex={start_index}, "
                f"batchSize={effective_batch_size}, toolBudget={args.tool_time_budget}s"
            )

            phase = "capture"
            try:
                if client is None:
                    client = McpClient(exe_path, "Python Entity Annotation Capture", args.request_timeout, args.framing)
                    client.start()
                    prepare_solidworks_session(client, args)
                    phase = "index"
                    if args.rebuild_index and index_prepared:
                        args.rebuild_index = False
                    index = ensure_target_index(client, args)
                    indexed_target_count = json_int(index or {}, "targetCount", indexed_target_count)
                    index_prepared = True

                phase = "capture"
                capture_tool = LEGACY_CAPTURE_TOOL if args.legacy_capture else INDEX_CAPTURE_TOOL
                result = client.call_tool(capture_tool, capture_args)
            except TimeoutError as exc:
                print(f"Timed out: {exc}")
                close_client(client)
                client = None
                if phase == "index":
                    if attempts > args.max_retries:
                        raise
                    print("Target index build timed out before capture started; retrying index build.")
                    time.sleep(args.retry_delay)
                    continue

                previous_start_index = start_index
                manifest, current_count, manifest_next_start = wait_for_manifest_progress(
                    args.output_dir,
                    last_target_count,
                    previous_start_index,
                    args.progress_wait_after_timeout,
                )
                if manifest:
                    start_index = manifest_next_start
                    print(
                        "Recovered progress from manifest: "
                        f"targets={current_count}, nextStartIndex={start_index}, "
                        f"stoppedReason={json_get(manifest, 'stoppedReason')}"
                    )
                elif attempts > args.max_retries:
                    raise

                if current_count <= last_target_count and manifest_next_start <= previous_start_index:
                    effective_batch_size = 1
                    stuck_timeouts[previous_start_index] = stuck_timeouts.get(previous_start_index, 0) + 1
                    if stuck_timeouts[previous_start_index] >= args.skip_stuck_target_after:
                        skipped_index = previous_start_index
                        start_index = previous_start_index + 1
                        attempts = 0
                        stuck_timeouts.pop(previous_start_index, None)
                        print(
                            "No manifest progress after repeated timeouts; "
                            f"skipping source index {skipped_index} and continuing at {start_index}."
                        )

                if current_count <= last_target_count and attempts > args.max_retries:
                    raise RuntimeError(
                        "Timed out repeatedly without manifest progress. "
                        "Reduce --batch-size or --tool-time-budget."
                    ) from exc

                last_target_count = current_count
                time.sleep(args.retry_delay)
                continue
            except Exception as exc:
                print(f"Batch failed: {exc}")
                close_client(client)
                client = None
                if attempts > args.max_retries:
                    raise
                if phase == "index":
                    print("Target index build failed before capture started; retrying index build.")
                time.sleep(args.retry_delay)
                continue

            attempts = 0
            target_count = json_int(result, "targetCount", last_target_count)
            processed_this_run = json_get(result, "processedThisRun")
            total_target_count = json_int(result, "totalTargetCount", indexed_target_count)
            start_index = json_int(result, "nextStartIndex", start_index)
            effective_batch_size = args.batch_size if args.legacy_capture else 1
            stuck_timeouts.pop(start_index, None)
            stopped_reason = json_get(result, "stoppedReason")
            last_target_count = target_count
            print(
                "Batch result: "
                f"targetCount={target_count}, "
                f"processedThisRun={processed_this_run}, "
                f"totalTargetCount={total_target_count}, "
                f"nextStartIndex={start_index}, "
                f"stoppedReason={stopped_reason}"
            )

            completed = stopped_reason == "completed"
            if not completed and start_index >= total_target_count > 0:
                completed = True
    finally:
        close_client(client)

    manifest_path = args.output_dir / "manifest.json"
    print(f"\nDone. Manifest: {manifest_path}")
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a SolidWorks assembly target index, then capture entity annotation images in resumable MCP calls."
    )
    parser.add_argument("--output-dir", required=True, type=Path, help="Directory for manifest.json and entity images.")
    parser.add_argument("--exe", help="Path to SolidWorksMcpApp.exe. Defaults to latest artifacts/solidworks-mcp* exe.")
    parser.add_argument("--document", type=Path, help="Optional full path to a .SLDASM/.SLDPRT document to open and activate before capture.")
    parser.add_argument("--width", type=int, default=800)
    parser.add_argument("--height", type=int, default=600)
    parser.add_argument("--batch-size", type=int, default=10, help="Targets per MCP call. Smaller values reduce timeout risk.")
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--tool-time-budget", type=int, default=30, help="maxDurationSeconds passed to the MCP tool.")
    parser.add_argument("--request-timeout", type=float, default=90, help="Python-side timeout per MCP request.")
    parser.add_argument(
        "--framing",
        choices=["content-length", "newline"],
        default="newline",
        help="MCP stdio framing used by SolidWorksMcpApp proxy.",
    )
    parser.add_argument("--max-retries", type=int, default=5)
    parser.add_argument("--retry-delay", type=float, default=0.5)
    parser.add_argument(
        "--progress-wait-after-timeout",
        type=float,
        default=2.0,
        help="Seconds to poll manifest.json after a timeout before retrying or skipping.",
    )
    parser.add_argument(
        "--skip-stuck-target-after",
        type=int,
        default=3,
        help="Skip a source index after this many no-progress timeouts at that same index.",
    )
    parser.add_argument("--max-batches", type=int, default=0, help="0 means unlimited.")
    parser.add_argument("--padding", type=float, default=1.35, help="capturePaddingFactor.")
    parser.add_argument("--clean-display", action="store_true", help="Use hidden-lines-removed clean display.")
    parser.add_argument("--connect", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--force-reconnect", action="store_true", help="Disconnect the MCP COM session before connecting, useful when the Hub is attached to a stale SolidWorks instance.")
    parser.add_argument("--probe-only", action="store_true", help="Only initialize MCP and list tools; do not capture.")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--rebuild-index", action="store_true", help="Rebuild target-index.json before capture.")
    parser.add_argument(
        "--feature-tree-only",
        action="store_true",
        help="Only export the recursive feature-tree.json for the active part/assembly; do not build target-index.json or capture screenshots.",
    )
    parser.add_argument(
        "--use-active-document-output-dir",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="After --feature-tree-only succeeds, copy feature-tree outputs into featru_tree_<active top document stem> next to the requested output directory. Defaults to true.",
    )
    parser.add_argument(
        "--overwrite-feature-tree",
        action="store_true",
        help="Rebuild feature-tree.json even if it already exists.",
    )
    parser.add_argument(
        "--expand-management-feature-subtrees",
        action="store_true",
        help="Also expand SolidWorks management FeatureManager folders. Defaults to false because large assemblies can hang or take a very long time.",
    )
    parser.add_argument(
        "--expand-feature-subtrees",
        action="store_true",
        help="Recursively expand feature subfeatures. Defaults to false; HasSubFeatures is still exported for filtering.",
    )
    parser.add_argument(
        "--include-component-features",
        action="store_true",
        help="Also enumerate features inside loaded child assembly documents. Child part features are included by default.",
    )
    parser.add_argument(
        "--include-part-features",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enumerate features inside loaded child part documents. Defaults to true; use --no-include-part-features if an imported part blocks export.",
    )
    parser.add_argument(
        "--active-document-only",
        action="store_true",
        help="For --feature-tree-only, export only the current active document's own feature tree and do not recurse into child components/subassemblies.",
    )
    parser.add_argument(
        "--skip-feature-document",
        action="append",
        type=Path,
        default=[],
        help="Full path to a part/assembly document whose features should be skipped while keeping its address node. Can be passed multiple times.",
    )
    parser.add_argument(
        "--auto-skip-stuck-feature-documents",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="When feature-tree-only export times out, read feature-tree-progress.json, skip that document's features, and retry.",
    )
    parser.add_argument(
        "--max-auto-skips",
        type=int,
        default=25,
        help="Maximum number of feature documents to auto-skip during feature-tree-only recovery. 0 means unlimited.",
    )
    parser.add_argument(
        "--restart-mcp-after-feature-timeout",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Restart SolidWorksMcpApp.exe after a feature-tree-only request timeout so a stuck COM call does not block retries. SolidWorks itself is not terminated.",
    )
    parser.add_argument(
        "--append-document-journal",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Keep appending to feature-tree-documents.jsonl even when --overwrite-feature-tree is set. The retry loop enables this automatically after the first timeout.",
    )
    parser.add_argument(
        "--skip-imported-feature-documents",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Skip feature enumeration for imported neutral-format documents such as Open CASCADE STEP translator files while keeping their document/component nodes.",
    )
    parser.add_argument(
        "--export-duplicate-source-document-features",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Export feature nodes for every repeated instance of the same source document. Defaults to false so repeated parts/subassemblies keep address nodes but only the first instance exports features.",
    )
    parser.add_argument(
        "--rebuild-feature-tree-from-journal",
        action="store_true",
        help="Rebuild a feature-tree JSON file from feature-tree-documents.jsonl without connecting to SolidWorks.",
    )
    parser.add_argument(
        "--rebuild-feature-tree-from-journal-on-failure",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="When feature-tree-only recovery gives up, write a partial tree reconstructed from feature-tree-documents.jsonl.",
    )
    parser.add_argument(
        "--rebuild-feature-tree-from-journal-output",
        default="feature-tree-from-journal.json",
        help="Output filename used by --rebuild-feature-tree-from-journal and failure recovery.",
    )
    parser.add_argument(
        "--filter-feature-tree",
        action="store_true",
        help="Read feature-tree.json and write a flat target list of feature nodes where EntityKind is feature and HasSubFeatures is true.",
    )
    parser.add_argument(
        "--feature-tree-path",
        type=Path,
        help="Input feature tree JSON for --filter-feature-tree. Defaults to <output-dir>/feature-tree.json.",
    )
    parser.add_argument(
        "--filtered-feature-tree-output",
        default="feature-tree-filtered-features.json",
        help="Output filename or path for --filter-feature-tree.",
    )
    parser.add_argument(
        "--capture-filtered-feature-three-views",
        action="store_true",
        help="Read feature-tree-filtered-features.json, highlight each filtered feature, and export normal plus transparent-context front/top/right PNGs under three_views.",
    )
    parser.add_argument(
        "--overwrite-three-views",
        action="store_true",
        help="Ignore an existing three_views/three-view-manifest.json and overwrite images as targets are processed.",
    )
    parser.add_argument(
        "--max-transparent-faces",
        type=int,
        default=1000,
        help="Maximum non-target faces to make transparent for each transparent-context capture. Use 0 to skip context transparency and only export the second view set with edge display.",
    )
    parser.add_argument(
        "--legacy-capture",
        action="store_true",
        help="Use the older CaptureActiveAssemblyEntityAnnotationSet tool instead of the two-stage target-index flow.",
    )
    parser.add_argument("--include-components", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--include-features", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--include-bodies", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument(
        "--require-feature-type-name",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Only keep targets with a valid FeatureTypeName; filters ProfileFeature and OneBend.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    try:
        raise SystemExit(run_capture(parse_args(sys.argv[1:])))
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
