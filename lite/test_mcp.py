"""Quick test for MCP tools via SSE transport with proper initialization."""
import json
import sys
import threading
import time
import sseclient
import requests

BASE = "http://localhost:6005"


def test_tool(tool_name, query_dict):
    """Call an MCP tool and return the result."""
    resp = requests.get(f"{BASE}/sse", stream=True, timeout=30)
    client = sseclient.SSEClient(resp)

    session_id = None
    results = []
    initialized = threading.Event()
    got_result = threading.Event()

    def listen():
        for event in client.events():
            if event.event == "endpoint":
                nonlocal session_id
                session_id = event.data.split("session_id=")[-1]
            elif event.event == "message":
                data = json.loads(event.data)
                msg_id = data.get("id")
                if msg_id == "init":
                    initialized.set()
                elif msg_id == "call":
                    results.append(data)
                    got_result.set()
                    return

    t = threading.Thread(target=listen, daemon=True)
    t.start()

    # Wait for session ID
    for _ in range(50):
        if session_id:
            break
        time.sleep(0.1)

    if not session_id:
        print("ERROR: No session ID")
        return None

    post_url = f"{BASE}/messages/?session_id={session_id}"

    # Send initialize
    requests.post(post_url, json={
        "jsonrpc": "2.0",
        "id": "init",
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1.0"},
        },
    }, timeout=10)

    initialized.wait(timeout=10)

    # Send initialized notification
    requests.post(post_url, json={
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
    }, timeout=10)

    time.sleep(0.3)

    # Send tool call
    requests.post(post_url, json={
        "jsonrpc": "2.0",
        "id": "call",
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": {"query": json.dumps(query_dict, ensure_ascii=False)},
        },
    }, timeout=10)

    got_result.wait(timeout=15)

    try:
        resp.close()
    except Exception:
        pass

    if results:
        return results[0]
    return None


if __name__ == "__main__":
    tests = [
        ("search_metadata", {"op": "list_categories"}),
        ("search_metadata", {"op": "list_objects_by_name", "name": "ДокументыПредприятия"}),
        ("search_metadata", {"op": "find_routines_by_name", "name": "ОтправитьЗапрос"}),
        ("search_code", {"op": "get_routine_body", "name": "ВыполнитьЗапрос"}),
        ("search_metadata_by_description", {"text": "документы"}),
    ]

    for tool, query in tests:
        print(f"\n{'='*60}")
        print(f"Tool: {tool}")
        print(f"Query: {json.dumps(query, ensure_ascii=False)}")
        print("-" * 60)
        try:
            result = test_tool(tool, query)
            if result:
                content = result.get("result", {}).get("content", [])
                for item in content:
                    if item.get("type") == "text":
                        parsed = json.loads(item["text"])
                        print(json.dumps(parsed, ensure_ascii=False, indent=2)[:800])
            else:
                print("No result received")
        except Exception as e:
            print(f"Error: {e}")
