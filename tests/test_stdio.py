"""Verify the public stdio transport with a real MCP client and server process."""
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from mcp_incident_server.server import Incidents


def test_stdio_client_can_discover_tools_and_consume_approval_once(tmp_path):
    path = tmp_path / "incidents.db"
    repo = Incidents(str(path))
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO incidents(id,title) VALUES (7,'API latency')")
    token = repo.approve_note(7, "Rollback completed", "reviewer")
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_incident_server.server"],
        cwd=Path(__file__).resolve().parents[1],
        env={"PORTFOLIO_DB": str(path)},
    )

    async def exercise_client():
        async with (
            asyncio.timeout(20),
            stdio_client(server) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            initialized = await session.initialize()
            assert initialized.capabilities.tools is not None
            tools = await session.list_tools()
            assert {tool.name for tool in tools.tools} == {
                "list_open_incidents", "add_incident_note",
            }
            listed = await session.call_tool("list_open_incidents", {})
            assert not listed.isError
            assert listed.structuredContent == {"result": [
                {"id": 7, "title": "API latency", "status": "open"},
            ]}
            arguments = {"incident_id": 7, "body": "Rollback completed", "actor": "reviewer"}
            missing = await session.call_tool("add_incident_note", arguments)
            assert missing.isError
            assert "approval_token" in missing.content[0].text
            arguments["approval_token"] = token
            added = await session.call_tool("add_incident_note", arguments)
            assert not added.isError
            assert json.loads(added.content[0].text) == {"note_id": 1}
            reused = await session.call_tool("add_incident_note", arguments)
            assert reused.isError
            assert "reused" in reused.content[0].text

    asyncio.run(exercise_client())
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT incident_id,body,actor FROM notes").fetchall() == [
            (7, "Rollback completed", "reviewer"),
        ]
        assert db.execute("SELECT used FROM approvals").fetchall() == [(1,)]
