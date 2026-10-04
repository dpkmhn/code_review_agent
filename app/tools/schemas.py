REPOSITORY_TOOLS = [
    {
        "type": "function",
        "name": "read_file",
        "description": (
            "Read a repository file when more context is needed "
            "to verify a potential bug."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Repository-relative file path.",
                }
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "search_code",
        "description": (
            "Search the repository for code matching a symbol or text."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Text or symbol to search for.",
                }
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "find_references",
        "description": (
            "Find repository locations that reference a symbol."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Function, class, or variable name.",
                }
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
]