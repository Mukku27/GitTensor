# neurons/validator.py
import os
import time
import uuid
import traceback
import asyncio
import torch
import bittensor as bt
from dotenv import load_dotenv
import subprocess
import shutil
from typing import Optional, Tuple, List

# Import from captionize project structure
from gittensor.base.validator import BaseValidatorNeuron
from gittensor.protocol import RadicleGittensorSynapse # Use the new Radicle synapse
from gittensor.utils import rad_utils, uids as uid_utils # Import new rad_utils and existing uids util

# Load environment variables from .env file
load_dotenv()

class Validator(BaseValidatorNeuron):
    def __init__(self, config=None):
        super().__init__(config=config) # BaseValidatorNeuron handles wallet, subtensor, metagraph, dendrite, scores, logging
        
        bt.logging.info("Initializing Radicle GitTensor Validator...")

        self.radicle_validator_alias = self.config.radicle.validator.alias # Get from config
        self.radicle_passphrase = os.getenv("RADICLE_PASSPHRASE")
        if not self.radicle_passphrase:
            bt.logging.warning("RADICLE_PASSPHRASE environment variable not set. Radicle operations requiring a passphrase may fail.")
            # self.radicle_passphrase = "<your_radicle_passphrase>" # Fallback - NOT RECOMMENDED

        self.setup_radicle_cli_dependencies_val()
        self.ensure_radicle_validator_auth()
        
        # Placeholder for where validator might store its local repo copies if needed for complex scenarios
        self.validator_local_repos_dir = "/tmp/gittensor_validator_repos" 
        os.makedirs(self.validator_local_repos_dir, exist_ok=True)

        # Scoring weights for the first functionality
        self.scoring_weights = {
            "VALIDATE_PUSH_success": 1.0,
        }
        
        bt.logging.info(f"Radicle GitTensor Validator Initialized. Alias: {self.radicle_validator_alias}")

    @classmethod
    def add_args(cls, parser):
        super().add_args(parser) # Add base neuron args
        # Add Radicle specific arguments for Validator
        parser.add_argument(
            "--radicle.validator.alias",
            type=str,
            default=f"gittensor-validator-{str(uuid.uuid4())[:8]}", # Unique default alias
            help="Alias for the Radicle identity for this validator.",
        )
        # Add other validator specific Radicle args if any in future.
        # --neuron.sample_size is already in BaseValidatorNeuron's add_args via captionize.utils.config

    def setup_radicle_cli_dependencies_val(self): # Renamed to avoid conflict if sharing utils
        bt.logging.info("Validator: Checking Radicle CLI installation...")
        success, stdout, _ = rad_utils.run_rad_command("--version", suppress_error=True)
        if success:
            bt.logging.info(f"Validator: Radicle CLI found: {stdout}")
        else:
            bt.logging.warning("Validator: Radicle CLI not found. Attempting to install via curl...")
            try:
                install_command = "curl -sSf https://radicle.xyz/install | sh"
                process = subprocess.Popen(install_command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                stdout_install, stderr_install = process.communicate(timeout=180)
                if process.returncode == 0:
                    bt.logging.info("Validator: Radicle CLI installed successfully via curl. Ensure it's in PATH or restart shell.")
                else:
                    bt.logging.error(f"Validator: Failed to install Radicle CLI via curl: {stderr_install}. Install manually.")
            except Exception as e:
                bt.logging.error(f"Validator: Exception during Radicle CLI install: {e}")

    def ensure_radicle_validator_auth(self):
        bt.logging.info(f"Validator: Ensuring Radicle identity '{self.radicle_validator_alias}'...")
        # Simplified auth check for validator, as it mainly consumes, but `rad init` needs it.
        # `rad auth <alias>` will select or create if it doesn't exist (may prompt).
        # Using pexpect for `rad auth` if it needs passphrase for creation.
        # Your old validator's ensure_radicle_auth was simpler.
        
        # Check if the alias key file exists
        rad_keys_path = os.path.expanduser(f"~/.radicle/keys/{self.radicle_validator_alias}")
        if not os.path.exists(rad_keys_path):
            bt.logging.info(f"Validator: Key for alias '{self.radicle_validator_alias}' not found. Attempting 'rad auth --alias {self.radicle_validator_alias}'.")
            # This command might prompt if creating a new key and identity is locked.
            # For simplicity, assume it either works non-interactively or user has set it up.
            # Robust solution would use pexpect here as well.
            auth_success, auth_output, auth_stderr = rad_utils.run_rad_command(
                f"auth --alias {self.radicle_validator_alias}"
            )
            if not auth_success:
                bt.logging.error(f"Validator: Failed to set up Radicle alias '{self.radicle_validator_alias}'. Output: {auth_output}, Err: {auth_stderr}. Please run manually.")
                # exit(1) # Decide if fatal
            else:
                bt.logging.info(f"Validator: Radicle alias '{self.radicle_validator_alias}' auth output: {auth_output}")
        else:
            bt.logging.info(f"Validator: Radicle key for alias '{self.radicle_validator_alias}' found. Selecting it.")
            rad_utils.run_rad_command(f"auth {self.radicle_validator_alias}", suppress_error=True)


    def _validator_create_and_push_repo(self) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Validator creates a temporary Git repo, initializes it with Radicle, and pushes it.
        Returns (repo_rid, commit_hash, error_message_or_None, local_repo_path_or_None)
        The local_repo_path is returned for potential later use by the validator THIS ROUND (e.g. modifications)
        and MUST be cleaned up by the caller.
        """
        repo_name = f"val-repo-{self.uid}-{str(uuid.uuid4())[:6]}" # Include validator UID for easier tracking
        # Create in a validator-specific temp area that we can manage.
        temp_dir_for_creation = os.path.join(self.validator_local_repos_dir, repo_name)
        
        try:
            if os.path.exists(temp_dir_for_creation):
                shutil.rmtree(temp_dir_for_creation)
            os.makedirs(temp_dir_for_creation, exist_ok=True)

            rad_utils.run_rad_command("init .", cwd=temp_dir_for_creation) # Should not be needed, git init first
            rad_utils.run_rad_command("config user.name Validator-{}".format(self.uid), cwd=temp_dir_for_creation, suppress_error=True)
            rad_utils.run_rad_command("config user.email validator-{}@gittensor.com".format(self.uid), cwd=temp_dir_for_creation, suppress_error=True)

            rad_utils.run_rad_command("checkout -b main", cwd=temp_dir_for_creation, suppress_error=True) # Ensure main branch

            with open(os.path.join(temp_dir_for_creation, "README.md"), "w") as f:
                f.write(f"# Test Repo by Validator UID {self.uid}\nTimestamp: {time.time()}\nRandom content: {uuid.uuid4()}")
            rad_utils.run_rad_command("add README.md", cwd=temp_dir_for_creation)
            
            commit_msg = f"Initial commit by validator UID {self.uid} - {time.time()}"
            # Use --allow-empty if there's a chance of no changes, though we added a file.
            commit_success, _, commit_stderr = rad_utils.run_rad_command(f"commit -m \"{commit_msg}\"", cwd=temp_dir_for_creation)
            if not commit_success:
                bt.logging.error(f"Validator: Git commit failed in {temp_dir_for_creation}. Stderr: {commit_stderr}")
                return None, None, f"Git commit failed: {commit_stderr}", temp_dir_for_creation # Return path for cleanup

            _, commit_hash, _ = rad_utils.run_rad_command("rev-parse HEAD", cwd=temp_dir_for_creation)
            if not commit_hash:
                return None, None, "Failed to get commit hash", temp_dir_for_creation

            # Initialize Radicle project (this makes it a Radicle project locally)
            # `rad init` might require passphrase if identity is locked.
            bt.logging.info(f"Validator: Initializing Radicle project in {temp_dir_for_creation}...")
            init_command = f"init --name {repo_name} --description 'Gittensor test repo by validator {self.uid}' --default-branch main --public --no-confirm"
            init_success, init_output = rad_utils.pexpect_run_rad_command_with_passphrase(
                init_command, self.radicle_passphrase, cwd=temp_dir_for_creation
            )
            if not init_success or "Error" in init_output:
                bt.logging.error(f"Validator: Radicle init failed in {temp_dir_for_creation}. Output: {init_output}")
                return None, commit_hash, f"Radicle init failed: {init_output}", temp_dir_for_creation
            
            # Get RID
            time.sleep(1) # Give rad a moment
            rid_success, rid_stdout, rid_stderr = rad_utils.run_rad_command("inspect --rid", cwd=temp_dir_for_creation)
            repo_rid = rid_stdout.strip()
            if not rid_success or not repo_rid.startswith("rad:"):
                bt.logging.error(f"Validator: Failed to get Radicle RID in {temp_dir_for_creation}. Stdout: '{rid_stdout}', Stderr: '{rid_stderr}'")
                return None, commit_hash, f"Failed to get Radicle RID: {rid_stderr or rid_stdout}", temp_dir_for_creation
            bt.logging.info(f"Validator: Radicle project initialized. RID: {repo_rid} in {temp_dir_for_creation}")

            # Push to network (makes it available for others, including miners, to seed/clone)
            # This also might need passphrase if identity is locked and it's the first push.
            # `rad push` is often simpler than `git push rad main` for initial push.
            bt.logging.info(f"Validator: Pushing repo {repo_rid} from {temp_dir_for_creation} to Radicle network...")
            push_success, push_output = rad_utils.pexpect_run_rad_command_with_passphrase(
                "push --all", self.radicle_passphrase, cwd=temp_dir_for_creation # --all ensures all local refs go up
            )
            if not push_success or "Error" in push_output: # Check for "Error" as well.
                 # Rad push can output "✓ Synced" even if some seeds fail. This is generally OK for network availability.
                if "✓ Synced" in push_output or "up to date" in push_output.lower() or "nothing to push" in push_output.lower():
                    bt.logging.info(f"Validator: Radicle push for {repo_rid} completed (possibly with partial seed failures but announced). Output: {push_output}")
                else:
                    bt.logging.error(f"Validator: Radicle push failed for {repo_rid}. Output: {push_output}")
                    return repo_rid, commit_hash, f"Radicle push failed: {push_output}", temp_dir_for_creation
            
            bt.logging.info(f"Validator: Successfully created and pushed repo. RID: {repo_rid}, Commit: {commit_hash}, Local Path: {temp_dir_for_creation}")
            return repo_rid, commit_hash, None, temp_dir_for_creation

        except Exception as e:
            bt.logging.error(f"Validator: Error in _validator_create_and_push_repo: {e}\n{traceback.format_exc()}")
            return None, None, str(e), temp_dir_for_creation if 'temp_dir_for_creation' in locals() else None


    async def forward(self):
        """
        Validator forward pass. Consists of:
        1. Selecting miners to query.
        2. Validator creates a new Radicle repo and pushes it.
        3. Sending a `VALIDATE_PUSH` synapse to selected miners.
        4. Rewarding miners based on their success in seeding/validating.
        """
        bt.logging.info(f"Validator {self.uid} starting forward pass for step {self.step}...")

        # 1. Select miners
        # Use get_random_uids from captionize.utils.uids
        # Ensure self.config.neuron.sample_size is defined in add_args or defaulted.
        sample_size = self.config.neuron.sample_size if hasattr(self.config.neuron, 'sample_size') else 1 # Default if not in config
        
        if self.metagraph.n.item() == 0:
            bt.logging.warning("Validator: No miners available in the metagraph. Skipping forward pass.")
            await asyncio.sleep(20) # Wait before retrying if no miners
            return

        # Ensure sample_size isn't larger than available, non-self UIDs
        available_miner_uids = [uid for uid in range(self.metagraph.n.item()) if uid != self.uid and self.metagraph.axons[uid].is_serving]
        
        if not available_miner_uids:
            bt.logging.warning("Validator: No other serving miners available. Skipping queries.")
            await asyncio.sleep(20)
            return

        k_miners = min(sample_size, len(available_miner_uids))
        if k_miners == 0: # Should be caught by above, but as safeguard
            bt.logging.warning("Validator: k_miners is 0. Cannot sample. Skipping queries.")
            await asyncio.sleep(20)
            return

        # get_random_uids takes `self` as first arg.
        # Need to ensure it's compatible or adapt how it's called.
        # The one in `captionize.utils.uids` is designed for a class `self`.
        # We can call it directly:
        miner_uids_tensor = uid_utils.get_random_uids(self, k=k_miners)
        miner_uids = miner_uids_tensor.tolist() # Convert to list of ints
        
        if not miner_uids:
            bt.logging.warning("Validator: No miner UIDs selected. Skipping forward pass.")
            return
        bt.logging.info(f"Validator {self.uid}: Selected {len(miner_uids)} miners for querying: {miner_uids}")

        # 2. Validator creates and pushes a new repo
        repo_rid, commit_hash, create_push_error, local_repo_path = self._validator_create_and_push_repo()
        
        if create_push_error or not repo_rid:
            bt.logging.error(f"Validator {self.uid}: Failed to create/push Radicle repo: {create_push_error}. Skipping queries for this round.")
            if local_repo_path and os.path.exists(local_repo_path): # Cleanup if path was returned
                try: shutil.rmtree(local_repo_path)
                except Exception as e_clean: bt.logging.error(f"Error cleaning up failed repo creation dir {local_repo_path}: {e_clean}")
            return # Skip to next validation cycle

        # 3. Send VALIDATE_PUSH synapse to miners
        synapse_to_send = RadicleGittensorSynapse(
            operation_type="VALIDATE_PUSH",
            repo_rid=repo_rid,
            commit_hash=commit_hash
        )

        bt.logging.info(f"Validator {self.uid}: Querying miners {miner_uids} with VALIDATE_PUSH for RID {repo_rid}")
        target_axons = [self.metagraph.axons[uid] for uid in miner_uids]
        
        responses: List[RadicleGittensorSynapse] = await self.dendrite.forward(
            axons=target_axons,
            synapse=synapse_to_send,
            deserialize=True, # Expecting RadicleGittensorSynapse objects back
            timeout=60 # Timeout for miner to respond (rad seed can take time)
        )

        # 4. Reward miners
        current_rewards = torch.zeros(len(miner_uids), dtype=torch.float32)
        for i, response_synapse in enumerate(responses):
            uid = miner_uids[i] # Get the UID for this response
            if response_synapse and response_synapse.dendrite.status_code == 200:
                if response_synapse.validation_passed:
                    current_rewards[i] = self.scoring_weights.get("VALIDATE_PUSH_success", 1.0)
                    bt.logging.info(f"Validator {self.uid}: Miner UID {uid} successfully validated/seeded {repo_rid}. Reward: {current_rewards[i]}")
                else:
                    bt.logging.warning(f"Validator {self.uid}: Miner UID {uid} failed to validate/seed {repo_rid}. Error: {response_synapse.error_message or response_synapse.status_message}")
                    current_rewards[i] = 0.0
            else:
                # Penalize for no response or error
                status_msg = response_synapse.dendrite.status_message if response_synapse and response_synapse.dendrite else "No response or transport error"
                bt.logging.warning(f"Validator {self.uid}: No successful response from Miner UID {uid} for {repo_rid}. Status: {status_msg}")
                current_rewards[i] = 0.0
        
        # Update scores using the base class method
        self.update_scores(current_rewards, miner_uids) # BaseValidatorNeuron.update_scores takes tensor and list

        # Cleanup the repo created by validator for this round
        if local_repo_path and os.path.exists(local_repo_path):
            try:
                shutil.rmtree(local_repo_path)
                bt.logging.info(f"Validator {self.uid}: Cleaned up local repo: {local_repo_path}")
            except Exception as e:
                bt.logging.error(f"Validator {self.uid}: Error cleaning up local repo {local_repo_path}: {e}")

        bt.logging.info(f"Validator {self.uid}: Forward pass for step {self.step} completed.")
        # BaseValidatorNeuron's run loop will handle sync, set_weights, etc.

    # run, run_in_background_thread, serve_axon, set_weights, update_scores, etc.
    # are inherited from BaseValidatorNeuron. We only override `forward`.
    # If custom save/load state is needed for Radicle specific things, override those.

# Entry point
if __name__ == "__main__":
    with Validator() as validator:
        # The BaseValidatorNeuron.run() method handles the main loop and calls self.forward()
        # So, we just need to keep the main thread alive if not using its run_in_background.
        # However, the base class's run() is typically called.
        # If running directly, it might be:
        # validator.run() 
        # For now, this keeps it similar to the miner's __main__
        bt.logging.info("Radicle GitTensor Validator running...")
        while True:
            time.sleep(60)