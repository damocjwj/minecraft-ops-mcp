from __future__ import annotations

import unittest

import mcp.types as types

from minecraft_ops_mcp.config import AppConfig
from minecraft_ops_mcp.models import Prompt, Resource, Tool
from minecraft_ops_mcp.server import make_mcp_server


class SdkServerTests(unittest.IsolatedAsyncioTestCase):
    def make_server(self):
        tools = [
            Tool(
                name="demo.ok",
                title="Demo OK",
                description="Demo tool.",
                input_schema={
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                    "additionalProperties": False,
                },
                output_schema={"type": "object", "additionalProperties": True},
                annotations={"readOnlyHint": True},
                handler=lambda args: {"hello": args["name"]},
            )
        ]
        resources = [
            Resource(
                uri="demo://config",
                name="config",
                title="Config",
                description="Demo resource.",
                mime_type="application/json",
                read=lambda: "{}",
            )
        ]
        prompts = [
            Prompt(
                name="demo_prompt",
                title="Demo Prompt",
                description="Demo prompt.",
                arguments=[],
                get=lambda args: [{"role": "user", "content": {"type": "text", "text": "demo"}}],
            )
        ]
        return make_mcp_server(AppConfig.from_env(), tools, resources, prompts)

    async def test_handlers_are_registered_with_sdk_server(self) -> None:
        server = self.make_server()
        self.assertIn(types.ListToolsRequest, server.request_handlers)
        self.assertIn(types.CallToolRequest, server.request_handlers)
        self.assertIn(types.ListResourcesRequest, server.request_handlers)
        self.assertIn(types.ReadResourceRequest, server.request_handlers)
        self.assertIn(types.ListPromptsRequest, server.request_handlers)
        self.assertIn(types.GetPromptRequest, server.request_handlers)

    async def test_list_tools_includes_modern_metadata(self) -> None:
        server = self.make_server()
        response = await server.request_handlers[types.ListToolsRequest](types.ListToolsRequest())
        result = response.root
        tool = result.tools[0]
        self.assertEqual(tool.name, "demo.ok")
        self.assertEqual(tool.title, "Demo OK")
        self.assertEqual(tool.outputSchema, {"type": "object", "additionalProperties": True})
        self.assertTrue(tool.annotations.readOnlyHint)

    async def test_list_tools_honors_cursor(self) -> None:
        tools = [
            Tool(
                name=f"demo.{index}",
                title=f"Demo {index}",
                description="Demo tool.",
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                output_schema={"type": "object", "additionalProperties": True},
                handler=lambda args: {"ok": True},
            )
            for index in range(101)
        ]
        server = make_mcp_server(AppConfig.from_env(), tools, [], [])
        first_response = await server.request_handlers[types.ListToolsRequest](types.ListToolsRequest())
        self.assertEqual(len(first_response.root.tools), 100)
        self.assertEqual(first_response.root.nextCursor, "100")
        second_request = types.ListToolsRequest(params={"cursor": first_response.root.nextCursor})
        second_response = await server.request_handlers[types.ListToolsRequest](second_request)
        self.assertEqual([tool.name for tool in second_response.root.tools], ["demo.100"])
        self.assertIsNone(second_response.root.nextCursor)

    async def test_tool_call_success_returns_structured_content(self) -> None:
        server = self.make_server()
        await server.request_handlers[types.ListToolsRequest](types.ListToolsRequest())
        request = types.CallToolRequest(params={"name": "demo.ok", "arguments": {"name": "Alex"}})
        response = await server.request_handlers[types.CallToolRequest](request)
        result = response.root
        self.assertFalse(result.isError)
        self.assertEqual(result.structuredContent, {"hello": "Alex"})
        self.assertEqual(result.content[0].type, "text")

    async def test_sdk_input_validation_returns_tool_error(self) -> None:
        server = self.make_server()
        await server.request_handlers[types.ListToolsRequest](types.ListToolsRequest())
        request = types.CallToolRequest(params={"name": "demo.ok", "arguments": {}})
        response = await server.request_handlers[types.CallToolRequest](request)
        result = response.root
        self.assertTrue(result.isError)
        self.assertIn("Input validation error", result.content[0].text)

    async def test_resource_and_prompt_handlers(self) -> None:
        server = self.make_server()
        resource_response = await server.request_handlers[types.ListResourcesRequest](types.ListResourcesRequest())
        self.assertEqual(str(resource_response.root.resources[0].uri), "demo://config")
        read_request = types.ReadResourceRequest(params={"uri": "demo://config"})
        read_response = await server.request_handlers[types.ReadResourceRequest](read_request)
        self.assertEqual(read_response.root.contents[0].text, "{}")

        prompt_response = await server.request_handlers[types.ListPromptsRequest](types.ListPromptsRequest())
        self.assertEqual(prompt_response.root.prompts[0].name, "demo_prompt")
        get_request = types.GetPromptRequest(params={"name": "demo_prompt", "arguments": {}})
        get_response = await server.request_handlers[types.GetPromptRequest](get_request)
        self.assertEqual(get_response.root.messages[0].content.text, "demo")


if __name__ == "__main__":
    unittest.main()
