import os

from mcp.server.fastmcp import FastMCP

from backend import sources

server = FastMCP("ADR research")
JOB_ID = os.environ.get("ADR_JOB_ID")


@server.tool()
async def search_github(query: str, repo: str | None = None) -> dict:
    """Search the ADR owner's code/docs. Results are leads: read relevant files before citing."""
    return await sources.search_github(query, repo)


@server.tool()
async def read_github_file(repo: str, path: str, start_line: int = 1, end_line: int = 220) -> dict:
    """Read a GitHub text file at an immutable commit, recording line-linked citation evidence."""
    return await sources.read_github_file(repo, path, start_line, end_line, JOB_ID)


if __name__ == "__main__":
    server.run(transport="stdio")
