import asyncio
import json
import subprocess
from typing import Any, Dict

class McpClient:
    """
    A client for communicating with the SolidWorksMcpApp in --proxy mode.
    It handles starting the process and sending/receiving JSON-RPC messages.
    """
    def __init__(self, exe_path: str):
        self.exe_path = exe_path
        self.process: asyncio.subprocess.Process | None = None
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.request_id = 0
        self.pending_responses: Dict[int, asyncio.Future] = {}
        self._stderr_task: asyncio.Task | None = None
        self._reader_task: asyncio.Task | None = None

    async def connect(self):
        """Starts the SolidWorksMcpApp.exe in proxy mode and establishes communication."""
        if self.process and self.process.returncode is None:
            print("MCP client is already connected.")
            return

        print(f"Starting MCP proxy: {self.exe_path}")
        self.process = await asyncio.create_subprocess_exec(
            self.exe_path,
            '--proxy',
            '--client', 'PythonCacheServer',
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.reader = self.process.stdout
        self.writer = self.process.stdin
        self.stderr_reader = self.process.stderr
        
        self._reader_task = asyncio.create_task(self._read_responses())
        self._stderr_task = asyncio.create_task(self._read_stderr())
        print("MCP client process started.")

    async def _read_responses(self):
        """Reads JSON responses from the MCP server and resolves pending futures."""
        while self.process and self.process.returncode is None:
            try:
                line = await self.reader.readline()
                if not line:
                    print("MCP process stdout closed.")
                    break
                response = json.loads(line)
                
                if response.get("type") == "ready":
                    print("MCP client connected to hub.")
                    continue

                if 'id' in response and response['id'] in self.pending_responses:
                    future = self.pending_responses.pop(response['id'])
                    if 'error' in response:
                        future.set_exception(RuntimeError(f"MCP Tool Error: {response['error']}"))
                    else:
                        future.set_result(response.get('result'))
            except (json.JSONDecodeError, BrokenPipeError) as e:
                print(f"Error reading from MCP process: {e}")
                break
            except Exception as e:
                print(f"Unexpected error in response reader: {e}")
                break
        
        for future in self.pending_responses.values():
            if not future.done():
                future.set_exception(ConnectionAbortedError("MCP process terminated unexpectedly."))
        self.pending_responses.clear()

    async def _read_stderr(self):
        """Reads from the MCP process's stderr stream and logs it."""
        while self.process and self.process.returncode is None:
            try:
                line = await self.stderr_reader.readline()
                if not line:
                    break
                print(f"[MCP Stderr]: {line.decode('utf-8').strip()}")
            except Exception as e:
                print(f"Error reading MCP stderr: {e}")
                break

    async def call_tool(self, tool_name: str, **kwargs: Any) -> Any:
        """Calls a tool on the MCP server and waits for the result."""
        if not self.process or self.process.returncode is not None:
            raise ConnectionError("MCP process is not running. Call connect() first.")

        self.request_id += 1
        request = { "jsonrpc": "2.0", "method": tool_name, "params": kwargs, "id": self.request_id }
        
        future = asyncio.get_running_loop().create_future()
        self.pending_responses[self.request_id] = future
        
        self.writer.write(json.dumps(request).encode('utf-8') + b'\n')
        await self.writer.drain()
        
        result = await future
        
        if isinstance(result, str):
            try:
                return json.loads(result)
            except json.JSONDecodeError:
                return result
        return result

    async def disconnect(self):
        """Terminates the MCP process and closes the streams."""
        if self.process and self.process.returncode is None:
            if self._reader_task: self._reader_task.cancel()
            if self._stderr_task: self._stderr_task.cancel()
            self.process.terminate()
            await self.process.wait()
        self.process = None