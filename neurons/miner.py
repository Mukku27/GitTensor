import os
import time
import shutil
import asyncio
import subprocess
import bittensor as bt
from pathlib import Path
import json
import traceback
import typing 

from gittensor.base.miner import BaseMinerNeuron

from gittensor.protocol import gittensor, AVAILABLE_OPERATIONS


class Miner(BaseMinerNeuron):
    def __init__(self, config=None):
        super().__init__(config=config) # This calls BaseNeuron's init, then BaseMinerNeuron's
        
        self.repo_base_dir = Path(self.config.neuron.miner_repo_base_dir).expanduser()
        self.repo_base_dir.mkdir(parents=True, exist_ok=True)
        self.rad_alias = self.config.neuron.rad_alias_miner
        self.rad_config_path = Path.home() / ".radicle" / "config.json"
        self.rad_home_path = Path.home() / ".radicle"

        self.radicle_initialized_successfully = False # Flag to track Radicle setup

        bt.logging.info("Initializing GitTensor Miner...")

        if not self._check_rad_installed():
            bt.logging.error(
                "Radicle CLI ('rad' or 'radicle-node') not found in PATH. "
                "Git/Radicle operations will fail. Miner will run in degraded mode. "
                "Please install Radicle: https://radicle.xyz/install"
            )
            # Do not exit; allow miner to register on Bittensor network.
        else:
            # Only attempt Radicle setup if CLI is present
            identity_ok = False
            if self.config.neuron.initialize_rad_identity_auto:
                identity_ok = self._ensure_rad_identity() # Returns True on success
            
            config_ok = False
            if identity_ok: # Only proceed if identity was okay or skipped
                config_ok = self._ensure_rad_config() # Returns True on success
            
            node_ok = False
            if config_ok: # Only proceed if config was okay
                if self.config.neuron.start_rad_node_auto:
                    node_ok = self._ensure_rad_node_running() # Returns True on success
            
            self.radicle_initialized_successfully = identity_ok and config_ok and node_ok
            if self.radicle_initialized_successfully:
                bt.logging.success("Radicle environment initialized successfully for miner.")
            else:
                bt.logging.warning("Radicle environment setup incomplete. Git/Radicle operations may fail. Miner in degraded mode.")

        bt.logging.info(f"Miner ready. Repo base: {self.repo_base_dir}, Radicle alias: {self.rad_alias}. Radicle healthy: {self.radicle_initialized_successfully}")

    def _check_rad_installed(self) -> bool:
        rad_exists = shutil.which("rad") is not None
        rad_node_exists = shutil.which("radicle-node") is not None
        if not rad_exists: bt.logging.warning("'rad' command not found in PATH.")
        if not rad_node_exists: bt.logging.warning("'radicle-node' command not found in PATH.")
        return rad_exists and rad_node_exists

    def _run_rad_command(self, command_args: list[str], cwd: typing.Optional[Path] = None, timeout_seconds=60, pass_stdin: typing.Optional[str] = None) -> tuple[int, str, str]:
        base_command = command_args[0]
        if base_command == "git":
            full_command = command_args
        else:
            full_command = [base_command] + command_args[1:] if len(command_args) > 1 and base_command in ["rad", "radicle-node"] else ["rad"] + command_args

        bt.logging.debug(f"Executing: {' '.join(full_command)} {'in ' + str(cwd) if cwd else ''}")
        try:
            process = subprocess.run(
                full_command, capture_output=True, text=True, cwd=cwd or self.repo_base_dir,
                timeout=timeout_seconds, check=False, input=pass_stdin
            )
            stdout, stderr = process.stdout.strip(), process.stderr.strip()
            if process.returncode != 0:
                bt.logging.debug(f"Cmd failed (code {process.returncode}): {' '.join(full_command)}. stderr: {stderr[:200]}")
            return process.returncode, stdout, stderr
        except subprocess.TimeoutExpired:
            bt.logging.warning(f"Cmd timed out: {' '.join(full_command)}")
            return -1, "", "Command timed out."
        except FileNotFoundError:
            bt.logging.warning(f"Cmd not found: {full_command[0]}. Is Radicle installed?")
            return -2, "", f"Command not found: {full_command[0]}"
        except Exception as e:
            bt.logging.warning(f"Cmd error: {' '.join(full_command)}: {e}")
            return -3, "", str(e)

    def _ensure_rad_identity(self) -> bool:
        """Ensures Radicle identity is set up. Returns True on success."""
        if not self.config.neuron.initialize_rad_identity_auto:
            bt.logging.info("Automatic Radicle identity initialization disabled.")
            return True # Assume user handles it, or it's not strictly needed for degraded mode

        code, stdout_self, _ = self._run_rad_command(["self"])
        if code == -2: # Rad CLI not found
            bt.logging.error("Cannot ensure Radicle identity: 'rad' command not found.")
            return False 
            
        current_alias = None
        if code == 0:
            for line in stdout_self.splitlines():
                if "Alias" in line: current_alias = line.split(":")[-1].strip()
        
        if current_alias == self.rad_alias:
            bt.logging.info(f"Radicle identity already configured with alias '{self.rad_alias}'.")
            return True

        bt.logging.info(f"Current Radicle alias: '{current_alias}', desired: '{self.rad_alias}'. Attempting 'rad auth --alias {self.rad_alias}'.")
        auth_code, auth_stdout, auth_stderr = self._run_rad_command(["auth", "--alias", self.rad_alias], pass_stdin="yes\n")
        if auth_code == 0:
            bt.logging.success(f"Successfully ran 'rad auth' for alias '{self.rad_alias}'.")
            self._run_rad_command(["self"]) # Log current identity
            return True
        else:
            bt.logging.error(f"Failed 'rad auth' for alias '{self.rad_alias}'. Code: {auth_code}, Stdout: {auth_stdout}, Stderr: {auth_stderr}")
            bt.logging.warning("Manual 'rad auth' might be required.")
            return False

    def _ensure_rad_config(self) -> bool:
        """Ensures ~/.radicle/config.json has basic seed node settings. Returns True on success."""
        self.rad_home_path.mkdir(parents=True, exist_ok=True)
        config_data = {}
        if self.rad_config_path.exists():
            try:
                with open(self.rad_config_path, "r") as f: config_data = json.load(f)
            except json.JSONDecodeError:
                bt.logging.warning(f"Corrupt Radicle config at {self.rad_config_path}. Re-initializing.")
        
        node_config = config_data.setdefault("node", {})
        made_changes = False
        if node_config.get("alias") != self.rad_alias:
            node_config["alias"] = self.rad_alias; made_changes = True
        if node_config.get("policy") != "allow":
            node_config["policy"] = "allow"; made_changes = True
        if node_config.get("scope") != "all":
            node_config["scope"] = "all"; made_changes = True

        if "externalAddresses" not in node_config or not node_config["externalAddresses"]:
            bt.logging.warning(f"'node.externalAddresses' not set in {self.rad_config_path}. Configure manually for internet reachability.")
        
        if made_changes:
            try:
                with open(self.rad_config_path, "w") as f: json.dump(config_data, f, indent=2)
                bt.logging.info(f"Radicle config updated: {self.rad_config_path}")
            except IOError as e:
                bt.logging.error(f"Failed to write Radicle config {self.rad_config_path}: {e}")
                return False
        return True

    def _ensure_rad_node_running(self) -> bool:
        """Ensures the Radicle node daemon is running. Returns True on success."""
        status_code, stdout, _ = self._run_rad_command(["node", "status"])
        if status_code == -2: # Rad CLI not found
            bt.logging.error("Cannot ensure Radicle node status: 'rad' command not found.")
            return False

        if status_code == 0 and "running" in stdout.lower():
            bt.logging.info(f"Radicle node already running.")
            return True
        
        bt.logging.info(f"Radicle node not running or status check failed. Attempting 'rad node start'...")
        start_code, start_stdout, start_stderr = self._run_rad_command(["node", "start"])
        if start_code == 0:
            bt.logging.info(f"Radicle node start command issued. Output: {start_stdout}. Checking status after delay...")
            time.sleep(5) 
            retry_status_code, retry_stdout, _ = self._run_rad_command(["node", "status"])
            if retry_status_code == 0 and "running" in retry_stdout.lower():
                bt.logging.success(f"Radicle node is now running. Status: {retry_stdout}")
                return True
            else:
                 bt.logging.warning(f"Node start cmd OK, but status still not 'running'. Status: {retry_stdout}. Manual check needed.")
                 return False
        else:
            bt.logging.error(f"Failed 'rad node start'. Code: {start_code}, Stderr: {start_stderr}")
            return False

    async def forward(self, synapse: gittensor) -> gittensor:
        bt.logging.info(f"Miner RX: Op '{synapse.operation}', RID '{synapse.rid}', ReqID '{synapse.request_id}'")
        
        # Check if Radicle was initialized correctly before attempting operations
        if not self.radicle_initialized_successfully:
            synapse.status = "failure"
            synapse.error_message = "Miner Radicle environment not initialized. Cannot process Git/Radicle operations."
            synapse.stderr = "Radicle setup incomplete on miner."
            synapse.miner_timestamp = time.time()
            bt.logging.warning(f"Forward: {synapse.error_message} for ReqID {synapse.request_id}")
            return synapse

        repo_path: typing.Optional[Path] = None
        # ... (rest of the forward method logic remains the same as the "concise comments" version)
        # Ensure that _run_rad_command failures (like FileNotFoundError if rad disappears mid-run) are handled
        # and result in synapse.status = "failure"

        # (The existing forward logic from previous concise version)
        if synapse.operation == "init" and synapse.repo_name:
            repo_path = self.get_repo_path(synapse.repo_name)
        elif synapse.rid:
            repo_path = self.get_repo_path(synapse.rid)

        if repo_path:
            if synapse.operation == "init":
                 repo_path.mkdir(parents=True, exist_ok=True)
                 if any(repo_path.iterdir()) and not (repo_path / ".rad").exists():
                    synapse.status, synapse.error_message = "failure", f"Path {repo_path} exists, not empty, and not Radicle project."
                    synapse.miner_timestamp = time.time(); return synapse
            elif synapse.operation not in ["clone"] and not repo_path.exists():
                synapse.status, synapse.error_message = "failure", f"Repo path {repo_path} missing for op {synapse.operation}."
                synapse.miner_timestamp = time.time(); return synapse
        
        code, stdout, stderr = -1, "", "Op default error"

        try:
            # Check for Radicle again, in case it was removed after init.
            if not self._check_rad_installed():
                raise EnvironmentError("Radicle CLI became unavailable during operation.")

            if synapse.operation == "init":
                if not synapse.repo_name: raise ValueError("repo_name required for 'init'.")
                code, stdout, stderr = self._run_rad_command(["init", "--no-confirm"], cwd=repo_path)
            # ... (all other elif blocks for operations as in the concise version)
            elif synapse.operation == "clone":
                if not synapse.rid: raise ValueError("RID required for 'clone'.")
                target_path = self.get_repo_path(synapse.rid)
                if target_path.exists():
                     bt.logging.info(f"Repo {synapse.rid} exists. Syncing.")
                     code, stdout, stderr = self._run_rad_command(["sync", "--fetch"], cwd=target_path)
                     if code == 0: stdout = f"Existing repo synced: {stdout}"
                else:
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    code, stdout, stderr = self._run_rad_command(["clone", synapse.rid, target_path.name], cwd=target_path.parent)
            elif synapse.operation == "seed":
                if not synapse.rid: raise ValueError("RID required for 'seed'.")
                code, stdout, stderr = self._run_rad_command(["seed", synapse.rid, "--scope", synapse.scope or "all"])
            elif synapse.operation == "unseed":
                if not synapse.rid: raise ValueError("RID required for 'unseed'.")
                code, stdout, stderr = self._run_rad_command(["unseed", synapse.rid])
            elif synapse.operation == "git_add_commit_push":
                if not repo_path: raise ValueError("Repo context required.")
                if not synapse.branch: raise ValueError("Branch required for 'push'.")
                msg = synapse.message or f"GitTensor miner commit for {synapse.rid} at {time.time()}"
                (repo_path / f"miner_change_{int(time.time())}.txt").write_text(f"Auto-change for push test {time.time()}")
                add_c, add_o, add_e = self._run_rad_command(["git", "add", "."], cwd=repo_path)
                if add_c != 0: raise Exception(f"git add failed: {add_e}")
                commit_c, commit_o, commit_e = self._run_rad_command(["git", "commit", "-m", msg], cwd=repo_path)
                if commit_c != 0 and not ("nothing to commit" in commit_e.lower() or "no changes added" in commit_e.lower()):
                    raise Exception(f"git commit failed: {commit_e}")
                code, push_o, push_e = self._run_rad_command(["git", "push", "rad", synapse.branch], cwd=repo_path)
                stdout = f"Add: {add_o}\nCommit: {commit_o or 'No changes'}\nPush: {push_o}"
                stderr = f"Add: {add_e}\nCommit: {commit_e}\nPush: {push_e}"
            elif synapse.operation == "git_pull":
                if not repo_path: raise ValueError("Repo context required for git_pull.")
                if synapse.branch:
                    self._run_rad_command(["git", "checkout", synapse.branch], cwd=repo_path)
                code, stdout, stderr = self._run_rad_command(["rad", "pull"], cwd=repo_path)
            elif synapse.operation == "rad_sync":
                cwd = repo_path if repo_path and repo_path.exists() else None
                cmd = ["sync"] + (["--fetch"] if synapse.fetch_only else [])
                code, stdout, stderr = self._run_rad_command(cmd, cwd=cwd)
            elif synapse.operation == "rad_inspect":
                if not synapse.rid: raise ValueError("RID required for 'inspect'.")
                args = ["inspect", synapse.rid] + (synapse.inspect_args or [])
                code, stdout, stderr = self._run_rad_command(args)
            elif synapse.operation == "rad_follow":
                if not synapse.node_id: raise ValueError("NodeID required for 'follow'.")
                code, stdout, stderr = self._run_rad_command(["follow", synapse.node_id, "--yes"])
            elif synapse.operation == "rad_unfollow":
                if not synapse.node_id: raise ValueError("NodeID required for 'unfollow'.")
                code, stdout, stderr = self._run_rad_command(["unfollow", synapse.node_id])
            elif synapse.operation == "rad_node_status":
                code, stdout, stderr = self._run_rad_command(["node", "status"])
            elif synapse.operation == "rad_ls":
                code, stdout, stderr = self._run_rad_command(["ls"])
            elif synapse.operation == "rad_self":
                code, stdout, stderr = self._run_rad_command(["self"])

            else:
                synapse.status, synapse.error_message = "failure", f"Unknown operation: {synapse.operation}"
                synapse.miner_timestamp = time.time(); return synapse

        except ValueError as ve: 
            synapse.status, synapse.error_message = "failure", str(ve)
            bt.logging.debug(f"Miner ValueError for op {synapse.operation} (ReqID {synapse.request_id}): {ve}")
        except EnvironmentError as ee: # Catch Radicle CLI unavailability during operation
            synapse.status, synapse.error_message = "failure", str(ee)
            bt.logging.error(f"Miner EnvironmentError for op {synapse.operation} (ReqID {synapse.request_id}): {ee}")
            if code == 0: code = -2 # FileNotFoundError from _run_rad_command
            if not stderr: stderr = synapse.error_message
        except Exception as e:
            synapse.status, synapse.error_message = "failure", f"Miner op exception: {str(e)}"
            bt.logging.error(f"Miner Exception (ReqID {synapse.request_id}): {e}\n{traceback.format_exc()}")
            if code == 0: code = -3 
            if not stderr: stderr = synapse.error_message

        synapse.status = "success" if code == 0 else "failure"
        synapse.stdout, synapse.stderr = stdout, stderr
        if code != 0 and not synapse.error_message: 
            synapse.error_message = stderr or f"Cmd failed (code {code})"
        
        synapse.miner_timestamp = time.time()
        bt.logging.info(f"Miner TX: Op '{synapse.operation}', RID '{synapse.rid}', Status '{synapse.status}', ReqID '{synapse.request_id}'")
        return synapse

    # Blacklist and Priority can remain concise as before, or be expanded later.
    async def blacklist(self, synapse: gittensor) -> typing.Tuple[bool, str]:
        if synapse.operation not in AVAILABLE_OPERATIONS:
            return True, f"Unsupported operation: {synapse.operation}"
        # Potentially blacklist if self.radicle_initialized_successfully is False for Radicle ops
        if synapse.operation != "rad_node_status" and synapse.operation != "rad_self" and not self.radicle_initialized_successfully:
             return True, "Miner Radicle environment not ready."
        return False, "Allowed"

    async def priority(self, synapse: gittensor) -> float:
        return 1.0

    # __enter__ and __exit__ can remain as in the base or concise version.
    # No major changes needed there for this specific issue.
    def get_repo_path(self, rid_or_name: str) -> Path: # Already defined in concise version
        """Determines local path for a RID or local name."""
        safe_name = "".join(c if c.isalnum() or c in ['-', '_'] else '_' for c in rid_or_name)
        return self.repo_base_dir / safe_name
        
    def __enter__(self):
        super().__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback_obj):
        super().__exit__(exc_type, exc_value, traceback_obj)
        bt.logging.info("Miner shutting down.")