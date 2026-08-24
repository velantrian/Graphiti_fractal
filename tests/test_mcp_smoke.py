import json
import subprocess
import sys


EXPECTED_TOOLS = {
    "memory.search_knowledge",
    "memory.search_experience",
    "memory.remember",
    "memory.upload",
    "memory.delete",
}


def _frame(obj):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body


def _read_response(stream):
    headers = {}
    while True:
        line = stream.readline()
        assert line, "MCP server closed stdout unexpectedly"
        decoded = line.decode("ascii", "ignore").strip()
        if not decoded:
            break
        if ":" in decoded:
            key, value = decoded.split(":", 1)
            headers[key.lower().strip()] = value.strip()
    length = int(headers.get("content-length", "0"))
    assert length > 0
    return json.loads(stream.read(length).decode("utf-8"))


def test_mcp_initialize_and_tools_contract():
    process = subprocess.Popen(
        [sys.executable, "-m", "mcp_server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        process.stdin.write(
            _frame(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05"},
                }
            )
        )
        process.stdin.write(
            _frame({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        )
        process.stdin.flush()

        initialize = _read_response(process.stdout)
        tools_list = _read_response(process.stdout)

        assert initialize["jsonrpc"] == "2.0"
        assert initialize["result"]["serverInfo"]["name"] == "fractal-memory-mcp"
        assert initialize["result"]["capabilities"] == {"tools": {}}

        tools = tools_list["result"]["tools"]
        names = {tool["name"] for tool in tools}
        assert names == EXPECTED_TOOLS
        assert len(tools) == len(EXPECTED_TOOLS)
    finally:
        process.terminate()
        process.wait(timeout=5)
