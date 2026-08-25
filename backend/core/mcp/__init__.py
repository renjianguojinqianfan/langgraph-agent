"""MCP (Model Context Protocol) client package (P2 item 1).

The client connects to external MCP servers over stdio, discovers the tools
they expose and lets the kernel wrap them as ordinary :class:`BaseTool`
instances. Only the *tools* primitive is consumed in P2 (resources / prompts
are out of scope).
"""
