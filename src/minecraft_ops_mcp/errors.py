class OpsError(Exception):
    """Base class for expected user-facing errors."""


class ConfigError(OpsError):
    """Raised when an adapter is called without the required configuration."""


class SafetyError(OpsError):
    """Raised when a tool call is blocked by the safety policy."""


class MethodNotFoundError(OpsError):
    """Raised when a JSON-RPC method is not supported by this MCP server."""


class InvalidParamsError(OpsError):
    """Raised when JSON-RPC params are well-formed JSON but invalid for the method."""
