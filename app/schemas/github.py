from pydantic import BaseModel

# Represents an individual file modified within a pull request
class PullRequestFile(BaseModel):
    filename: str       # Path to the file
    status: str         # File status (e.g., "modified", "added", "deleted")
    additions: int      # Number of lines added
    deletions: int      # Number of lines removed
    changes: int        # Total lines changed (additions + deletions)
    patch: str | None = None  # The actual code diff (optional if file is large/binary)

# Represents the complete metadata and content of a pull request
class PullRequestData(BaseModel):
    owner: str          # GitHub organization or user account name
    repository: str     # Repository name
    number: int         # Pull request ID number

    title: str          # PR title
    body: str | None    # PR description summary (can be empty)

    base_branch: str    # Target branch receiving changes (e.g., "main")
    head_branch: str    # Source branch containing new code (e.g., "feature-xyz")

    base_sha: str       # Latest commit hash of the base branch
    head_sha: str       # Latest commit hash of the head branch

    files: list[PullRequestFile]  # List of all files changed in this PR
