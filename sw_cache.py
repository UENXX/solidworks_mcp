import asyncio
import json
import os
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

from mcp_client import McpClient

# Mock MCP tool decorator for demonstration purposes
class MockMcp:
    def tool(self):
        def decorator(f):
            return f
        return decorator

mcp = MockMcp()

class SWCacheManager:
    def __init__(self, mcp_client: McpClient, working_directory: str, master_file_path: str = "master_cache.json"):
        # The master file representation in cache
        # Structure: { "C:/path/to/file.sldasm": {"type": "assembly", "tag": None, "children": [...], "last_accessed": datetime, "level_1_scanned": bool} }
        self.mcp_client = mcp_client
        self.working_directory = working_directory
        self.tree_cache: Dict[str, Any] = {}
        self.master_file_path = master_file_path
        self.is_dirty = False
    
    async def get_or_create_node(self, file_path: str):
        """Handles the Exist / Not Exist logic with a shallow scan."""
        if file_path in self.tree_cache:
            # Update idle timer
            self.tree_cache[file_path]["last_accessed"] = datetime.now()
            self.is_dirty = True # Mark cache as changed
            return self.tree_cache[file_path]
        
        # NOT EXIST: Map info with master file using a shallow scan
        # This now calls the real MCP server.
        shallow_tree = await self._solidworks_shallow_scan(file_path)
        
        self.tree_cache[file_path] = {
            "path": file_path,
            "tag": None, # Wait for VLM
            "children": shallow_tree, # Store the whole tree structure
            "last_accessed": datetime.now(),
            "level_1_scanned": False # Not yet deep-scanned
        }
        self.is_dirty = True # Mark cache as changed
        return self.tree_cache[file_path]

    async def search_by_tag(self, target_tag: str) -> Optional[str]:
        """Searches the tree based on the VLM tag."""
        for path, node in self.tree_cache.items():
            # If tag is None, normal traversal (as noted in your journal)
            if node.get("tag") == target_tag:
                return path
            # Recursive search into children would go here
        return None

    def add_vlm_tag(self, file_path: str, tag: str):
        """Stores the path and tag together as per your 'IMP' note."""
        if file_path in self.tree_cache:
            self.tree_cache[file_path]["tag"] = tag
            self.is_dirty = True # Mark cache as changed
            return True
        return False

    async def _solidworks_shallow_scan(self, file_path: str):
        """
        Performs a shallow scan by calling the real SolidWorks MCP server.
        This involves opening the document and then invoking 'TraverseActiveFeatureManagerTree'.
        """
        print(f"PERFORMING SHALLOW SCAN ON: {file_path}")

        try:
            # First, ensure the correct document is open and active.
            print(f"  -> Activating document: {file_path}")
            open_result = await self.mcp_client.call_tool('open_document', path=file_path)

            # The MCP tool result object is ToolResult-like; if available, use .content or convert to dict.
            # We'll check if it's falsy or has an error field.
            if not open_result:
                 raise RuntimeError(f"Failed to open or activate document: {file_path}. Response: {open_result}")

            # The tool takes no arguments and operates on the active document.
            print(f"  -> Traversing feature tree for active document...")
            tree_data = await self.mcp_client.call_tool('traverse_active_feature_manager_tree')
            # The tool returns a JSON string which gets parsed into a list of dicts.
            # This is the "usable tree structure". We return it directly.
            return tree_data
        except Exception as e:
            print(f"Error during shallow scan for {file_path}: {e}")
            return [{"error": f"Error during scan for {file_path}: {e}"}]

    async def save_to_master_file(self):
        """Saves the current in-memory tree_cache to a physical JSON file."""
        if not self.is_dirty:
            return

        print(f"Saving cache to master file: {self.master_file_path}")
        serializable_cache = {}
        for path, node in self.tree_cache.items():
            node_copy = node.copy()
            if 'last_accessed' in node_copy and isinstance(node_copy['last_accessed'], datetime):
                node_copy['last_accessed'] = node_copy['last_accessed'].isoformat()
            serializable_cache[path] = node_copy

        try:
            with open(self.master_file_path, 'w', encoding='utf-8') as f:
                json.dump(serializable_cache, f, indent=2)
            self.is_dirty = False
            print("Cache saved successfully.")
        except Exception as e:
            print(f"Error saving cache to master file: {e}")

    async def load_from_master_file(self):
        """Loads the tree_cache from a physical JSON file if it exists."""
        if not os.path.exists(self.master_file_path):
            print("Master cache file not found. Starting with an empty cache.")
            return

        print(f"Loading cache from master file: {self.master_file_path}")
        try:
            with open(self.master_file_path, 'r', encoding='utf-8') as f:
                loaded_cache = json.load(f)
            
            for path, node in loaded_cache.items():
                if 'last_accessed' in node and isinstance(node['last_accessed'], str):
                    try:
                        node['last_accessed'] = datetime.fromisoformat(node['last_accessed'])
                    except ValueError:
                        node['last_accessed'] = datetime.now()
                self.tree_cache[path] = node
            print("Cache loaded successfully.")
        except Exception as e:
            print(f"Error loading cache from master file: {e}")

async def idle_sync_worker(cache_manager: SWCacheManager):
    """Background task to discover new files, perform 'level-1' deep scans, and sync cache to master file."""
    while True:
        # --- New File Discovery Step ---
        print("Worker: Starting periodic scan for new files...")
        if os.path.isdir(cache_manager.working_directory):
            for root, _, files in os.walk(cache_manager.working_directory):
                for file in files:
                    if file.lower().endswith(('.sldasm', '.sldprt')):
                        file_path = os.path.join(root, file)
                        if file_path not in cache_manager.tree_cache:
                            print(f"Worker: Discovered new file, performing initial shallow scan: {file_path}")
                            # This will trigger the shallow scan and add it to the cache
                            await cache_manager.get_or_create_node(file_path)
        else:
            print(f"Worker: Working directory '{cache_manager.working_directory}' not found. Skipping file discovery.")

        # --- Existing 'Level-1' Deep Scan Logic ---
        now = datetime.now()
        nodes_to_process = list(cache_manager.tree_cache.items())

        for file_path, node in nodes_to_process:
            idle_time = now - node.get("last_accessed", now)
            
            if idle_time > timedelta(minutes=1) and not node.get("level_1_scanned"):
                print(f"Cache idle for >1 min. Capturing Level 1 children for {file_path}...")
                
                child_component_paths = []
                if isinstance(node.get('children'), list):
                    for child_node in node['children']:
                        if child_node.get('type') == 'Component' and child_node.get('componentPath'):
                            child_component_paths.append(child_node['componentPath'])

                if child_component_paths:
                    print(f"Found {len(child_component_paths)} level-1 children to cache for {file_path}.")
                    for child_path in child_component_paths:
                        print(f"  -> Caching child: {child_path}")
                        await cache_manager.get_or_create_node(child_path)

                node["level_1_scanned"] = True
                cache_manager.is_dirty = True
        
        await cache_manager.save_to_master_file()
                
        await asyncio.sleep(60)

# --- HTTP Server Setup ---
# We use the built-in asyncio HTTP server to expose endpoints on port 8001
# This matches the URL configured in PythonCacheTools.cs

import http.server
import socketserver
from urllib.parse import urlparse

# Global cache instance for the HTTP handlers
http_cache: SWCacheManager = None

class CacheHTTPRequestHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f"[HTTP] {args[0]} {args[1]} {args[2]}")

    async def _handle_post_async(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        payload = json.loads(body) if body else {}
        path = urlparse(self.path).path
        response_data = {}

        try:
            if path == '/query_cad_file':
                file_path = payload.get('file_path', '')
                node = await http_cache.get_or_create_node(file_path)
                await http_cache.save_to_master_file()
                response_data = {
                    "status": "ok",
                    "message": f"Successfully scanned and cached '{os.path.basename(node['path'])}'.",
                    "path": node['path']
                }
            elif path == '/tag_cad_component':
                file_path = payload.get('file_path', '')
                tag = payload.get('tag', '')
                success = http_cache.add_vlm_tag(file_path, tag)
                await http_cache.save_to_master_file()
                if success:
                    response_data = {"status": "ok", "message": f"Tagged {file_path} as '{tag}'."}
                else:
                    response_data = {"status": "error", "message": "File not found in cache. Query it first."}
            elif path == '/search_cad_by_tag':
                tag = payload.get('tag', '')
                result_path = await http_cache.search_by_tag(tag)
                if result_path:
                    response_data = {"status": "ok", "path": result_path, "message": f"Found component for tag '{tag}'."}
                else:
                    response_data = {"status": "ok", "path": None, "message": f"No component found with tag '{tag}'."}
            else:
                response_data = {"status": "error", "message": f"Unknown endpoint: {path}"}
                self.send_response(404)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(response_data).encode('utf-8'))
                return
        except Exception as e:
            response_data = {"status": "error", "message": str(e)}
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(response_data).encode('utf-8'))
            return

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(response_data).encode('utf-8'))

    def do_POST(self):
        # Create a new event loop for this thread and run the async handler
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._handle_post_async())
        finally:
            loop.close()


async def start_http_server(host: str = 'localhost', port: int = 8001):
    """Start the HTTP server in a separate thread so it doesn't block the async loop."""
    server = socketserver.TCPServer((host, port), CacheHTTPRequestHandler)
    print(f"Python cache server listening on http://{host}:{port}")
    # Run the server in a thread to keep the main async loop free
    await asyncio.get_running_loop().run_in_executor(None, server.serve_forever)

# --- Main Entry Point ---
async def main():
    global http_cache

    # 1. Create the MCP client
    mcp_exe_path = r"artifacts\solidworks-mcp\SolidWorksMcpApp.exe"
    mcp_client = McpClient(mcp_exe_path)
    
    # 2. Connect to the MCP server (launches SolidWorksMcpApp.exe in --proxy mode)
    await mcp_client.connect()
    
    # 3. Create the cache manager
    working_directory = r"D:\Textbooks and Materials\CUHK 2026\SolidWorks Blueprints"
    master_file_path = "master_cache.json"
    http_cache = SWCacheManager(mcp_client, working_directory=working_directory, master_file_path=master_file_path)
    
    # 4. Load any existing cache from disk
    await http_cache.load_from_master_file()
    
    # 5. Start the background idle sync worker (discovers & caches files)
    asyncio.create_task(idle_sync_worker(http_cache))
    
    # 6. Start the HTTP server for PythonCacheTools.cs to connect to
    await start_http_server()

if __name__ == "__main__":
    asyncio.run(main())