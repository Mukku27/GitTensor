import time
import uuid
import random
import asyncio
import traceback 
import bittensor as bt
from pathlib import Path
import subprocess 
import typing

from gittensor.base.validator import BaseValidatorNeuron
from gittensor.protocol import gittensor, AVAILABLE_OPERATIONS
from gittensor.utils.uids import get_random_uids

class TestRepo: # Keep TestRepo class as is
    def __init__(self, name: str, path: Path, rid: typing.Optional[str] = None):
        self.name = name
        self.path = path
        self.rid: typing.Optional[str] = rid
        self.initialized_by_validator = False
        self.last_commit_hash: typing.Optional[str] = None
        self.current_branch: str = "main"

    def __str__(self):
        return f"TestRepo(name={self.name}, rid={self.rid}, path={self.path}, initialized={self.initialized_by_validator})"

class Validator(BaseValidatorNeuron):
    def __init__(self, config=None):
        super().__init__(config=config)
        self.rad_alias = self.config.neuron.rad_alias_validator
        self.repo_base_dir = Path(self.config.neuron.validator_repo_base_dir).expanduser()
        self.repo_base_dir.mkdir(parents=True, exist_ok=True)
        self.test_repos: dict[str, TestRepo] = {}
        
        self.radicle_available_for_validator = self._check_validator_rad_installed()
        if self.radicle_available_for_validator:
            self._ensure_validator_rad_identity()
            self._initialize_test_repos_state() # Only if rad is available
        else:
            bt.logging.warning("Radicle CLI not found for validator. Test repo initialization and some verification steps will be skipped.")

        bt.logging.info(f"Validator initialized. Test repos: {len(self.test_repos)}. Radicle CLI available: {self.radicle_available_for_validator}")

    def _check_validator_rad_installed(self) -> bool:
        """Checks if Radicle CLI is available for the validator's local operations."""
        if shutil.which("rad") is not None:
            bt.logging.info("Radicle CLI 'rad' found for validator local operations.")
            return True
        bt.logging.warning("'rad' command not found for validator. Local Radicle operations will be skipped.")
        return False
        

    def _run_local_rad_command(self, command_args: list[str], cwd: typing.Optional[Path] = None, timeout_seconds=60) -> tuple[int, str, str]:
        """Helper to run Radicle/Git commands locally for validator's own management."""
        if not self.radicle_available_for_validator:
            return -2, "", "Radicle CLI not available for validator."

        full_command = ["rad"] + command_args
        if command_args[0] == "git": full_command = command_args
        try:
            process = subprocess.run(
                full_command, capture_output=True, text=True, cwd=cwd or self.repo_base_dir,
                timeout=timeout_seconds, check=False,
            )
            stdout, stderr = process.stdout.strip(), process.stderr.strip()
            if process.returncode != 0:
                bt.logging.debug(f"Local val cmd failed (code {process.returncode}): {' '.join(full_command)}. stderr: {stderr[:200]}")
            return process.returncode, stdout, stderr
        except subprocess.TimeoutExpired:
            bt.logging.warning(f"Local val cmd timed out: {' '.join(full_command)}")
            return -1, "", "Command timed out."
        except FileNotFoundError: # Should be caught by initial check, but good to have
            bt.logging.error(f"Local val cmd not found: {full_command[0]}. This shouldn't happen if initial check passed.")
            self.radicle_available_for_validator = False # Update status
            return -2, "", f"Command not found: {full_command[0]}"
        except Exception as e:
            bt.logging.warning(f"Error running local val cmd '{' '.join(full_command)}': {e}")
            return -3, "", str(e)

    def _ensure_validator_rad_identity(self):
        """Ensures Radicle identity is set up for the validator."""
        if not self.radicle_available_for_validator: return

        returncode, stdout, _ = self._run_local_rad_command(["identity", "list"])
        if returncode == 0 and self.rad_alias in stdout:
            bt.logging.info(f"Validator Radicle identity '{self.rad_alias}' already exists.")
        else:
            bt.logging.info(f"Validator Radicle identity '{self.rad_alias}' not found/selected. Attempting to create/auth...")
            auth_code, auth_stdout, auth_stderr = self._run_local_rad_command(["auth", "--alias", self.rad_alias, "--default"]) # Using --default for validator simplicity
            if auth_code == 0:
                bt.logging.success(f"Validator Radicle identity '{self.rad_alias}' authenticated. Output: {auth_stdout}")
            else:
                bt.logging.warning(f"Failed to auth validator Radicle identity '{self.rad_alias}'. Stderr: {auth_stderr}")

    def _initialize_test_repos_state(self):
        """Initializes or loads state for validator's test repositories."""
        if not self.radicle_available_for_validator:
            bt.logging.info("Skipping test repo initialization as Radicle is not available to validator.")
            return

        for i in range(self.config.neuron.num_test_repos):
            repo_name = f"{self.config.neuron.test_repo_prefix}{i}"
            repo_path = self.repo_base_dir / repo_name
            repo_obj = TestRepo(name=repo_name, path=repo_path)

            if not repo_path.exists() or not (repo_path / ".rad").exists():
                bt.logging.info(f"Test repo '{repo_name}' not found/initialized by validator. Attempting 'rad init'.")
                repo_path.mkdir(parents=True, exist_ok=True)
                init_code, init_stdout, init_stderr = self._run_local_rad_command(["init", "--no-confirm"], cwd=repo_path)
                if init_code == 0:
                    repo_obj.initialized_by_validator = True
                    inspect_code, inspect_stdout, _ = self._run_local_rad_command(["inspect", "."], cwd=repo_path)
                    if inspect_code == 0:
                        for line in inspect_stdout.splitlines():
                            if line.startswith("ID"):
                                repo_obj.rid = line.split()[-1]
                                bt.logging.info(f"Initialized val test repo '{repo_name}' RID: {repo_obj.rid} at {repo_path}")
                                break
                    if not repo_obj.rid: bt.logging.warning(f"Could not get RID for val initialized test repo {repo_name}")
                else:
                    bt.logging.warning(f"Failed to 'rad init' val test repo '{repo_name}': {init_stderr}")
            else: # Repo exists, try to get its RID
                inspect_code, inspect_stdout, _ = self._run_local_rad_command(["inspect", "."], cwd=repo_path)
                if inspect_code == 0:
                    for line in inspect_stdout.splitlines():
                        if line.startswith("ID"):
                            repo_obj.rid = line.split()[-1]
                            bt.logging.info(f"Loaded existing val test repo '{repo_name}' RID: {repo_obj.rid} at {repo_path}")
                            break
                if not repo_obj.rid: bt.logging.warning(f"Could not get RID for existing val test repo {repo_name}")
            
            if repo_obj.rid : self.test_repos[repo_name] = repo_obj
    # --- End of pasted methods ---

    async def forward(self):
        try:
            miner_uids = get_random_uids(self, k=self.config.neuron.sample_size, exclude=[self.uid])
            if not miner_uids.numel():
                bt.logging.debug("No available miners to query.")
                await asyncio.sleep(10) # Shorter sleep if no miners
                return

            # If validator couldn't set up test repos (e.g. Radicle not installed locally),
            # it can only test global Radicle commands or very basic connectivity.
            operation = ""
            current_test_repo = None

            if self.radicle_available_for_validator and self.test_repos:
                valid_test_repos = [repo for repo in self.test_repos.values() if repo.rid]
                if not valid_test_repos:
                    bt.logging.warning("Validator has no usable test repos with RIDs. Testing limited operations.")
                    # Fallback to operations that don't strictly need a validator-managed RID
                    operation = random.choice(["rad_node_status", "rad_ls", "rad_self"])
                else:
                    current_test_repo = random.choice(valid_test_repos)
                    operation = random.choice(AVAILABLE_OPERATIONS)
            else:
                # Radicle not available to validator, or no test repos. Test only global/simple ops.
                bt.logging.debug("Validator Radicle tools unavailable or no test RIDs. Testing global/simple ops.")
                operation = random.choice(["rad_node_status", "rad_ls", "rad_self"]) # Add more non-RID ops if any
                # Alternatively, you could craft an "init" request for the miner here.

            synapse = gittensor(
                request_id=str(uuid.uuid4()),
                operation=operation,
                timestamp=time.time(),
                rid=current_test_repo.rid if current_test_repo else None
            )

            bt.logging.info(f"Validator: Preparing Op '{operation}' for RID '{synapse.rid or 'N/A'}'")

            # Customize synapse for specific operations (same logic as before)
            # Ensure this customization logic is also robust to current_test_repo being None
            # for global operations.
            if operation == "init":
                synapse.repo_name = f"miner-test-init-{random.randint(1000,9999)}"
                synapse.rid = None 
            elif current_test_repo : # Operations requiring a current_test_repo
                if operation == "seed":
                    synapse.scope = random.choice(["all", "followed"])
                elif operation == "git_add_commit_push":
                    synapse.branch = current_test_repo.current_branch
                    # Validator attempts to make its own local push to setup the test
                    if self.radicle_available_for_validator:
                        dummy_file = current_test_repo.path / f"val_change_{int(time.time())}.txt"
                        dummy_file.write_text(f"Validator change at {time.time()}")
                        self._run_local_rad_command(["git", "add", "."], cwd=current_test_repo.path)
                        commit_msg = f"Validator test commit {int(time.time())}"
                        val_commit_code, _, val_commit_stderr = self._run_local_rad_command(
                            ["git", "commit", "-m", commit_msg], cwd=current_test_repo.path
                        )
                        if val_commit_code == 0:
                            hash_code, hash_stdout, _ = self._run_local_rad_command(["git", "rev-parse", "HEAD"], cwd=current_test_repo.path)
                            if hash_code == 0: current_test_repo.last_commit_hash = hash_stdout
                            val_push_code, _, val_push_stderr = self._run_local_rad_command(["git", "push", "rad", current_test_repo.current_branch], cwd=current_test_repo.path)
                            if val_push_code != 0:
                                bt.logging.warning(f"Val failed to push its own test commit for {current_test_repo.name}: {val_push_stderr}")
                                # Don't proceed with this test if validator setup failed
                                await asyncio.sleep(5); return 
                            bt.logging.info(f"Val pushed test commit {current_test_repo.last_commit_hash} to {current_test_repo.name}")
                        else:
                             bt.logging.warning(f"Val failed local test commit for {current_test_repo.name}: {val_commit_stderr}")
                             await asyncio.sleep(5); return
                    synapse.message = f"Miner commit for {current_test_repo.name} at {int(time.time())}"
                elif operation == "git_pull":
                    synapse.branch = current_test_repo.current_branch
                elif operation == "rad_inspect" and synapse.rid: # Ensure RID is present
                     synapse.inspect_args = random.choice([None, ["--policy"], ["--delegates"]])

            # Operations potentially needing a Node ID
            if operation in ["rad_follow", "rad_unfollow"]:
                if self.radicle_available_for_validator:
                    code, stdout_self, _ = self._run_local_rad_command(["self"])
                    if code == 0:
                        for line in stdout_self.splitlines():
                            if line.strip().startswith("Node ID"): synapse.node_id = line.split()[-1]; break
                if not synapse.node_id:
                    bt.logging.warning("Val NodeID not found for follow/unfollow test. Op may fail for miner or use default.")
            
            bt.logging.info(f"Validator: Sending {synapse.operation} to miners: {miner_uids.tolist()}")
            responses = await self.dendrite(
                axons=[self.metagraph.axons[uid] for uid in miner_uids],
                synapse=synapse,
                timeout=self.config.neuron.timeout
            )

            rewards = []
            processed_uids = []

            for i, response_synapse in enumerate(responses):
                uid = miner_uids[i].item()
                processed_uids.append(uid)
                reward = 0.0

                if response_synapse.dendrite.status_code != 200:
                    reward = 0.0 
                    bt.logging.info(f"Validator: Miner UID {uid} network error {response_synapse.dendrite.status_code}. Error: {response_synapse.dendrite.status_message}")
                elif response_synapse.error_message and "Radicle environment not initialized" in response_synapse.error_message:
                    reward = 0.01 # Very low reward if miner explicitly states Radicle is not ready
                    bt.logging.info(f"Validator: Miner UID {uid} reports Radicle not ready. Error: {response_synapse.error_message}")
                elif response_synapse.status == "success":
                    reward = 1.0
                    bt.logging.info(f"Validator: Miner UID {uid} OK op '{response_synapse.operation}'. Stdout: {response_synapse.stdout[:100]}...")
                    # Add more specific checks here based on operation
                    if response_synapse.operation == "rad_node_status" and "running" not in (response_synapse.stdout or "").lower():
                        reward = 0.2 # Succeeded but node not actually running
                else: # status == "failure"
                    reward = 0.1 # Small reward for responding with operational failure
                    bt.logging.info(f"Validator: Miner UID {uid} FAIL op '{response_synapse.operation}'. Error: {response_synapse.error_message}. Stderr: {response_synapse.stderr[:100]}...")
                
                rewards.append(reward)

            if rewards and processed_uids:
                bt.logging.info(f"Validator: Rewards: UIDs {processed_uids}, Values {rewards}")
                self.update_scores(torch.FloatTensor(rewards), processed_uids)
            else:
                bt.logging.debug("Validator: No responses or UIDs for this step.")

        except Exception as e:
            bt.logging.error(f"Validator forward loop error: {e}\n{traceback.format_exc()}")
        finally:
            await asyncio.sleep(self.config.neuron.update_interval)

    def __enter__(self): # Keep as is
        super().__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback_obj): # Keep as is
        super().__exit__(exc_type, exc_value, traceback_obj)