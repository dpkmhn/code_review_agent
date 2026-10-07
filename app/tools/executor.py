from app.github_client import GitHubClient


async def execute_repository_tool(
    *,
    github_client: GitHubClient,
    tool_name: str,
    arguments: dict,
    owner: str,
    repository: str,
    ref: str,
):
    if tool_name == "read_file":
        return await github_client.read_file(
            owner=owner,
            repository=repository,
            path=arguments["path"],
            ref=ref,
        )

    if tool_name == "list_tree":
        return await github_client.list_tree(owner=owner, repository=repository, ref=ref)

    if tool_name == "search_code":
        return await github_client.search_code(
            owner=owner,
            repository=repository,
            query=arguments["query"],
        )

    if tool_name == "find_references":
        return await github_client.find_references(
            owner=owner,
            repository=repository,
            symbol=arguments["symbol"],
        )

    raise ValueError(f"Unknown repository tool: {tool_name}")
