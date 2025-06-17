import time
import uuid
import random
import asyncio
import traceback # For detailed exception logging
import bittensor as bt
from pathlib import Path

from gittensor.base.validator import BaseValidatorNeuron
from gittensor.protocol import GitOpSynapse,AVAILABLE_OPERATIONS
from gittensor.utils.uids import get_random_uids 

# A small helper to manage test repository state for the validator
class TestRepo:
    def __init__(self, name: str, path: Path, rid: typing.Optional[str] = None):
        self.name = name
        self.path = path
        self.rid: typing.Optional[str] = rid
        self.initialized_by_validator = False
        self.last_commit_hash: typing.Optional[str] = None
        self.current_branch: str = "main" # Or your default

    def __str__(self):
        return f"TestRepo(name={self.name}, rid={self.rid}, path={self.path}, initialized={self.initialized_by_validator})"

class Validator(BaseValidatorNeuron):
    def __init__(self, config=None):
        super().__init__(config=config)
        self.rad_alias = self.config.neuron.rad_alias_validator # For local ops if needed
        self.repo_base_dir = Path(self.config.neuron.validator_repo_base_dir).expanduser()
        self.repo_base_dir.mkdir(parents=True, exist_ok=True)
        self.test_repos: dict[str, TestRepo] = {} # Store test repo info, key is repo name
        
        # Try to initialize Radicle identity for the validator (mainly for local inspection)
        self._ensure_validator_rad_identity()

        # Initialize or load test repositories state
        self._initialize_test_repos_state()
        bt.logging.info(f"Validator initialized with {len(self.test_repos)} test repos. Radicle alias: {self.rad_alias}")

    def _run_local_rad_command(self, command_args: list[str], cwd: typing.Optional[Path] = None, timeout_seconds=60) -> tuple[int, str, str]:
        """Helper to run Radicle/Git commands locally for validator's own management."""
        full_command = ["rad"] + command_args
        if command_args[0] == "git":
            full_command = command_args
        try:
            process = subprocess.run(
                full_command,
                capture_output=True,
                text=True,
                cwd=cwd if cwd else self.repo_base_dir,
                timeout=timeout_seconds,
                check=False,
            )
            stdout = process.stdout.strip()
            stderr = process.stderr.strip()
            if process.returncode != 0:
                bt.logging.debug(f"Local command '{' '.join(full_command)}' failed with code {process.returncode}. stderr: {stderr}")
            return process.returncode, stdout, stderr
        except subprocess.TimeoutExpired:
            bt.logging.warning(f"Local command '{' '.join(full_command)}' timed out.")
            return -1, "", "Command timed out."
        except Exception as e:
            bt.logging.warning(f"Error running local command '{' '.join(full_command)}': {e}")
            return -1, "", str(e)

    def _ensure_validator_rad_identity(self):
        """Ensures Radicle identity is set up for the validator."""
        returncode, stdout, _ = self._run_local_rad_command(["identity", "list"])
        if returncode == 0 and self.rad_alias in stdout:
            bt.logging.info(f"Validator Radicle identity '{self.rad_alias}' already exists.")
        else:
            bt.logging.info(f"Validator Radicle identity '{self.rad_alias}' not found. Attempting to create/auth...")
            auth_code, auth_stdout, auth_stderr = self._run_local_rad_command(["auth", "--alias", self.rad_alias, "--default"])
            if auth_code == 0:
                bt.logging.success(f"Validator Radicle identity '{self.rad_alias}' authenticated. Output: {auth_stdout}")
            else:
                bt.logging.warning(f"Failed to auth validator Radicle identity '{self.rad_alias}'. Stderr: {auth_stderr}")


    def _initialize_test_repos_state(self):
        """Initializes or loads state for validator's test repositories."""
        for i in range(self.config.neuron.num_test_repos):
            repo_name = f"{self.config.neuron.test_repo_prefix}{i}"
            repo_path = self.repo_base_dir / repo_name
            
            repo_obj = TestRepo(name=repo_name, path=repo_path)

            if not repo_path.exists() or not (repo_path / ".rad").exists():
                bt.logging.info(f"Test repo '{repo_name}' not found or not initialized. Will attempt to 'rad init'.")
                repo_path.mkdir(parents=True, exist_ok=True)
                # Initialize it locally for the validator
                init_code, init_stdout, init_stderr = self._run_local_rad_command(["init", "--no-confirm"], cwd=repo_path)
                if init_code == 0:
                    repo_obj.initialized_by_validator = True
                    # Try to get the RID after init (rad self from within the dir, or parse from init_stdout)
                    inspect_code, inspect_stdout, _ = self._run_local_rad_command(["inspect", "."], cwd=repo_path)
                    if inspect_code == 0:
                        for line in inspect_stdout.splitlines():
                            if line.startswith("ID"): # Radicle inspect output format
                                repo_obj.rid = line.split()[-1]
                                bt.logging.info(f"Initialized test repo '{repo_name}' with RID: {repo_obj.rid} at {repo_path}")
                                break
                    if not repo_obj.rid:
                         bt.logging.warning(f"Could not determine RID for initialized test repo {repo_name}")
                else:
                    bt.logging.warning(f"Failed to 'rad init' test repo '{repo_name}': {init_stderr}")
            else:
                # Repo exists, try to get its RID
                inspect_code, inspect_stdout, _ = self._run_local_rad_command(["inspect", "."], cwd=repo_path)
                if inspect_code == 0:
                    for line in inspect_stdout.splitlines():
                        if line.startswith("ID"):
                            repo_obj.rid = line.split()[-1]
                            bt.logging.info(f"Loaded existing test repo '{repo_name}' with RID: {repo_obj.rid} at {repo_path}")
                            break
                if not repo_obj.rid:
                    bt.logging.warning(f"Could not determine RID for existing test repo {repo_name}")
            
            if repo_obj.rid : # Only add if we have an RID
                self.test_repos[repo_name] = repo_obj


    async def forward(self):
        """
        Validator forward pass. Consists of:
        1. Selecting miners to query.
        2. Generating a Git/Radicle operation.
        3. Querying miners.
        4. Scoring responses.
        """
        try:
            miner_uids = get_random_uids(self, k=self.config.neuron.sample_size, exclude=[self.uid]) # Pass self for metagraph etc.
            if not miner_uids.numel():
                bt.logging.info("No available miners to query.")
                await asyncio.sleep(self.config.neuron.update_interval / 2) # shorter sleep if no miners
                return

            if not self.test_repos:
                bt.logging.warning("Validator has no test repositories with RIDs. Cannot generate RID-based operations.")
                await asyncio.sleep(self.config.neuron.update_interval)
                self._initialize_test_repos_state() # Try to re-init
                return

            # Choose a random test repo that has an RID
            valid_test_repos = [repo for repo in self.test_repos.values() if repo.rid]
            if not valid_test_repos:
                bt.logging.warning("Validator has no test repositories with RIDs after initialization. Cannot proceed.")
                await asyncio.sleep(self.config.neuron.update_interval)
                return
            
            current_test_repo = random.choice(valid_test_repos)
            operation = random.choice(AVAILABLE_OPERATIONS)
            
            # Prepare the synapse based on the operation
            synapse = GitOpSynapse(
                request_id=str(uuid.uuid4()),
                operation=operation,
                timestamp=time.time(),
                rid=current_test_repo.rid # Default to current test repo's RID
            )

            bt.logging.info(f"Validator: Preparing operation '{operation}' for RID '{current_test_repo.rid}' ({current_test_repo.name})")

            # Customize synapse for specific operations
            if operation == "init":
                # Miner 'init' requires a name, validator might ask miner to init a new repo
                # This is tricky because the RID won't be known beforehand by the validator.
                # For now, let's focus on ops with known RIDs or global ops.
                # We can choose a new name for the miner to init.
                synapse.repo_name = f"miner-test-init-{random.randint(1000,9999)}"
                synapse.rid = None # Miner will create this
                bt.logging.info(f"Validator: Testing 'init' with repo_name '{synapse.repo_name}'")

            elif operation == "clone":
                # RID is already set from current_test_repo.rid
                pass # Already set

            elif operation == "seed":
                synapse.scope = random.choice(["all", "followed"])

            elif operation == "git_add_commit_push":
                synapse.branch = current_test_repo.current_branch
                # Create a dummy file change in the validator's local copy of the test repo
                # So there's something to push for the test.
                dummy_file = current_test_repo.path / f"val_change_{int(time.time())}.txt"
                with open(dummy_file, "w") as f:
                    f.write(f"Validator change at {time.time()}")
                
                self._run_local_rad_command(["git", "add", "."], cwd=current_test_repo.path)
                commit_msg = f"Validator test commit {int(time.time())}"
                val_commit_code, val_commit_stdout, val_commit_stderr = self._run_local_rad_command(
                    ["git", "commit", "-m", commit_msg], cwd=current_test_repo.path
                )
                if val_commit_code == 0:
                    # Get commit hash
                    hash_code, hash_stdout, _ = self._run_local_rad_command(["git", "rev-parse", "HEAD"], cwd=current_test_repo.path)
                    if hash_code == 0: current_test_repo.last_commit_hash = hash_stdout
                    
                    # Validator pushes to its own rad remote to make it available for miners to pull/clone
                    val_push_code, _, val_push_stderr = self._run_local_rad_command(["git", "push", "rad", current_test_repo.current_branch], cwd=current_test_repo.path)
                    if val_push_code != 0:
                        bt.logging.warning(f"Validator failed to push its own test commit for {current_test_repo.name}: {val_push_stderr}")
                        # Don't test miner push if validator can't even push its setup.
                        await asyncio.sleep(5)
                        return
                    bt.logging.info(f"Validator pushed test commit {current_test_repo.last_commit_hash} to {current_test_repo.name} ({current_test_repo.rid})")
                else: # Failed to make a local commit for test
                     bt.logging.warning(f"Validator failed to make local test commit for {current_test_repo.name}: {val_commit_stderr}")
                     await asyncio.sleep(5)
                     return

                synapse.message = f"Miner commit for {current_test_repo.name} at {int(time.time())}"
            
            elif operation == "git_pull":
                synapse.branch = current_test_repo.current_branch
                # Could make a change in validator's local and push it first, so miner has something to pull.
                # This is partly covered by the git_add_commit_push logic.

            elif operation == "rad_sync":
                synapse.fetch_only = random.choice([True, False])

            elif operation == "rad_inspect":
                synapse.inspect_args = random.choice([None, ["--policy"], ["--delegates"]])

            elif operation == "rad_follow" or operation == "rad_unfollow":
                # For follow/unfollow, we need a NodeID. Validator could 'rad self' to get its own node ID
                # or query a known peer. For simplicity, we might skip testing this often, or use a fixed one.
                # Let's try to get the validator's own Node ID for this test.
                code, stdout, _ = self._run_local_rad_command(["self"])
                if code == 0:
                    for line in stdout.splitlines():
                        if line.strip().startswith("Node ID"):
                            synapse.node_id = line.split()[-1]
                            break
                if not synapse.node_id:
                    bt.logging.warning("Could not get validator NodeID for follow/unfollow test. Skipping this op round.")
                    await asyncio.sleep(1)
                    return
            
            # Global operations don't need current_test_repo.rid
            elif operation in ["rad_node_status", "rad_ls", "rad_self"]:
                synapse.rid = None


            bt.logging.info(f"Validator: Sending {synapse.operation} to miners: {miner_uids.tolist()}")
            responses = await self.dendrite(
                axons=[self.metagraph.axons[uid] for uid in miner_uids],
                synapse=synapse,
                timeout=self.config.neuron.timeout  # Use configured timeout
            )

            rewards = []
            processed_uids = [] # UIDs that actually responded

            for i, response_synapse in enumerate(responses):
                uid = miner_uids[i].item()
                processed_uids.append(uid)
                reward = 0.0

                if response_synapse.dendrite.status_code != 200:
                    reward = 0.0 # Penalize for network errors
                    bt.logging.info(f"Validator: Miner UID {uid} failed with dendrite status {response_synapse.dendrite.status_code}. Error: {response_synapse.dendrite.status_message}")
                elif response_synapse.status == "success":
                    reward = 1.0
                    bt.logging.info(f"Validator: Miner UID {uid} succeeded operation '{response_synapse.operation}'. Stdout: {response_synapse.stdout[:100]}...")
                    # TODO: More nuanced scoring based on operation and output
                    # For "git_add_commit_push", could try to 'rad sync' the RID locally and check for the new commit.
                    # For "rad_node_status", check if stdout contains "running".
                    if response_synapse.operation == "rad_node_status" and "running" not in (response_synapse.stdout or "").lower():
                        reward = 0.1 # Succeeded but node not actually running
                        bt.logging.warning(f"Validator: Miner UID {uid} 'rad_node_status' was success, but output suggests node not running: {response_synapse.stdout}")
                    elif response_synapse.operation == "init" and not response_synapse.stdout : # rad init should output RID
                        reward = 0.2
                        bt.logging.warning(f"Validator: Miner UID {uid} 'init' was success, but no stdout (expected RID).")

                else: # status == "failure"
                    reward = 0.1 # Small reward for responding, even if op failed, or 0 for stricter.
                    bt.logging.info(f"Validator: Miner UID {uid} failed operation '{response_synapse.operation}'. Error: {response_synapse.error_message}. Stderr: {response_synapse.stderr[:100]}...")
                
                rewards.append(reward)

            if rewards: # Check if processed_uids is not empty
                bt.logging.info(f"Validator: Rewards for this step: UIDs {processed_uids}, Values {rewards}")
                self.update_scores(torch.FloatTensor(rewards), processed_uids)
            else:
                bt.logging.info("Validator: No responses received for this step.")

        except Exception as e:
            bt.logging.error(f"Validator forward loop error: {e}\n{traceback.format_exc()}")

        finally:
            # Wait before next iteration
            await asyncio.sleep(self.config.neuron.update_interval)


    def __enter__(self):
        super().__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        super().__exit__(exc_type, exc_value, traceback)