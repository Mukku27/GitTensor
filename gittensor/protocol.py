import typing
import bittensor as bt

# Available Radicle operations
# Expanded to include more granular operations for better testing and control
AVAILABLE_OPERATIONS = [
    "init",                 # Initialize a new Radicle repo
    "clone",                # rad clone <RID>
    "seed",                 # rad seed <RID> --scope <scope>
    "unseed",               # rad unseed <RID>
    "git_add_commit_push",  # git add . && git commit -m <message> && git push rad <branch>
    "git_pull",             # git pull (assuming rad remote) or rad sync --fetch; git merge
    "rad_sync",             # rad sync [--fetch]
    "rad_inspect",          # rad inspect <RID> [--policy, --delegates, etc.]
    "rad_follow",           # rad follow <NodeID>
    "rad_unfollow",         # rad unfollow <NodeID>
    "rad_node_status",      # rad node status
    "rad_ls",               # rad ls
    "rad_self",             # rad self
    # Add more specific git/rad operations as needed
]

class GitOpSynapse(bt.Synapse):
    """
    A Synapse for performing Git and Radicle operations.
    It encapsulates the details of the operation to be performed by the miner
    and the results of that operation.
    """

    # Required request fields
    request_id: str  # Unique identifier for the request
    operation: str   # The operation to perform, e.g., "clone", "push"
    timestamp: float # Timestamp of the request initiation

    # Optional request fields, depending on the operation
    rid: typing.Optional[str] = None          # Radicle Repository ID
    branch: typing.Optional[str] = None       # Git branch
    commit_hash: typing.Optional[str] = None  # Specific commit hash (e.g., for checkout)
    message: typing.Optional[str] = None      # Commit message for "git_add_commit_push"
    scope: typing.Optional[str] = "all"       # Scope for "rad seed" (e.g., "all", "followed")
    node_id: typing.Optional[str] = None      # Node ID for "rad_follow", "rad_unfollow"
    repo_name: typing.Optional[str] = None    # Name for new repo during "init"
    fetch_only: bool = False                  # For "rad_sync" if only fetch is desired
    inspect_args: typing.Optional[list[str]] = None # Extra args for rad inspect e.g. ["--policy"]

    # Response fields, filled by the miner
    status: typing.Optional[str] = None        # "success" or "failure"
    stdout: typing.Optional[str] = None        # Standard output from the command
    stderr: typing.Optional[str] = None        # Standard error from the command
    miner_timestamp: typing.Optional[float] = None # Timestamp of the miner's response completion
    error_message: typing.Optional[str] = None # Detailed error message if status is "failure"

    def deserialize(self) -> "GitOpSynapse":
        """
        Deserialize the miner response.
        No special deserialization logic needed for these basic types if using default bittensor serialization.
        This method can be extended if complex data types are added.
        """
        return self

    def __str__(self):
        return (
            f"GitOpSynapse(request_id={self.request_id}, operation='{self.operation}', "
            f"rid='{self.rid}', branch='{self.branch}', status='{self.status}', "
            f"stdout_len={len(self.stdout or '')}, stderr_len={len(self.stderr or '')})"
        )

