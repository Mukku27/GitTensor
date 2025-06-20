import os
import time
import random
import argparse
import traceback
import bittensor as bt
import subprocess
import shutil
import uuid
import shlex
from typing import Tuple, Optional, List
import asyncio
import pexpect
import re
import torch
from protocol import RadicleSubnetSynapse

# Helper function to run shell commands
def run_command(command: str, suppress_error: bool = False, cwd: Optional[str] = None) -> Tuple[bool, str, str]:
    """Executes a shell command and returns success, stdout, and stderr."""
    try:
        bt.logging.debug(f"Running command: {command} (cwd: {cwd})")
        process = subprocess.Popen(shlex.split(command), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=cwd)
        stdout, stderr = process.communicate(timeout=120) # 120 second timeout for potentially long git/rad ops
        success = process.returncode == 0
        if not success and not suppress_error:
            bt.logging.error(f"Command failed: {command}\nStderr: {stderr.strip()}\nStdout: {stdout.strip()}")
        return success, stdout.strip(), stderr.strip()
    except subprocess.TimeoutExpired:
        bt.logging.error(f"Command timed out: {command}")
        process.kill() # Ensure the process is killed on timeout
        return False, "", "Timeout expired"
    except Exception as e:
        bt.logging.error(f"Error running command {command}: {e}")
        return False, "", str(e)

class Validator:
    def __init__(self):
        self.config = self.get_config()
        self.setup_logging()
        self.setup_radicle_dependencies() # Check/install Radicle
        self.ensure_radicle_auth()       # Ensure validator identity
        self.setup_bittensor_objects()
        # Initialize scores and moving averages
        self.scores = torch.zeros(self.metagraph.n.item(), dtype=torch.float32)
        self.moving_avg_scores = torch.zeros(self.metagraph.n.item(), dtype=torch.float32)
        self.alpha = self.config.validator.alpha # Weight for moving average
        self.query_timeout = 55 # seconds for dendrite queries
        self.steps_passed = 0

    def get_config(self):
        parser = argparse.ArgumentParser()
        parser.add_argument(
            "--radicle.validator.alias",
            default=f"bittensor-validator-{str(uuid.uuid4())[:8]}", # Unique default alias
            help="Alias for the Radicle identity for this validator.",
        )
        parser.add_argument(
            "--validator.alpha",
            type=float,
            default=0.05,
            help="Alpha for exponential moving average of scores.",
        )
        parser.add_argument(
            "--netuid", type=int, default=1, help="The chain subnet uid."
        )
        bt.subtensor.add_args(parser)
        bt.logging.add_args(parser)
        bt.wallet.add_args(parser)
        # No axon for validator, so bt.axon.add_args(parser) is not needed
        config = bt.config(parser)
        config.full_path = os.path.expanduser(
            "{}/{}/{}/netuid{}/{}".format(
                config.logging.logging_dir,
                config.wallet.name,
                config.wallet.hotkey_str,
                config.netuid,
                "validator",
            )
        )
        os.makedirs(config.full_path, exist_ok=True)
        return config

    def setup_logging(self):
        bt.logging(config=self.config, logging_dir=self.config.full_path)
        bt.logging.info(f"Running validator for subnet: {self.config.netuid} on network: {self.config.subtensor.network} with config:")
        bt.logging.info(self.config)

    def setup_radicle_dependencies(self):
        bt.logging.info("Checking Radicle CLI installation...")
        success, stdout, _ = run_command("rad --version", suppress_error=True)
        if success:
            bt.logging.info(f"Radicle CLI found: {stdout}")
        else:
            bt.logging.warning("Radicle CLI not found. Attempting to install...")
            install_success, _, stderr = run_command("curl -sSf https://radicle.xyz/install | sh")
            if install_success:
                bt.logging.info("Radicle CLI installed successfully. Please ensure it's in your PATH or restart the shell/validator.")
            else:
                bt.logging.error(f"Failed to install Radicle CLI: {stderr}. Please install it manually.")
                exit(1)

    def ensure_radicle_auth(self):
        bt.logging.info("Ensuring Radicle identity for validator...")
        radicle_home_keys = os.path.expanduser("~/.radicle/keys")
        # Check if default identity (usually 'radicle') exists or if our specific alias exists
        # This is a simplification; a robust check would involve `rad self` or similar
        # For now, if keys dir exists, assume some identity is set up.
        # If not, try to create one with the specified alias.
        if not os.path.exists(radicle_home_keys):
            bt.logging.info(f"Radicle keys not found. Attempting to authenticate as '{self.config.radicle.validator.alias}'.")
            success, stdout, stderr = run_command(f"rad auth --alias {self.config.radicle.validator.alias}")
            if not success:
                bt.logging.error(f"Failed to authenticate Radicle identity for validator: {stderr}. Please run 'rad auth --alias {self.config.radicle.validator.alias}' manually.")
                # It might be okay to continue if `rad` commands can run anonymously for some operations,
                # but `rad init` and `rad push` typically require an identity.
                # For this subnet, an identity is crucial.
                exit(1)
            bt.logging.info(f"Radicle identity created/selected for validator: {stdout}")
        else:
            bt.logging.info(f"Radicle keys directory found. Assuming identity '{self.config.radicle.validator.alias}' is available or will be created/used by rad commands.")
            # Verify if the specific alias is active, or just use default.
            # `rad auth {self.config.radicle.validator.alias}` can also select an existing one.
            run_command(f"rad auth {self.config.radicle.validator.alias}", suppress_error=True)

    def setup_bittensor_objects(self):
        bt.logging.info("Setting up Bittensor objects.")
        self.wallet = bt.wallet(config=self.config)
        bt.logging.info(f"Wallet: {self.wallet}")
        self.subtensor = bt.subtensor(config=self.config)
        bt.logging.info(f"Subtensor: {self.subtensor}")
        self.dendrite = bt.dendrite(wallet=self.wallet)
        bt.logging.info(f"Dendrite: {self.dendrite}")
        self.metagraph = self.subtensor.metagraph(self.config.netuid)
        bt.logging.info(f"Metagraph: {self.metagraph}")

        if self.wallet.hotkey.ss58_address not in self.metagraph.hotkeys:
            bt.logging.error(f"Your validator: {self.wallet} is not registered to chain connection: {self.subtensor}. Run 'btcli s register --netuid {self.config.netuid}' and try again.")
            exit()
        self.my_subnet_uid = self.metagraph.hotkeys.index(self.wallet.hotkey.ss58_address)
        bt.logging.info(f"Running validator on uid: {self.my_subnet_uid}")

    def create_and_push_radicle_repo(self) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Creates a temporary Git repo, initializes it with Radicle, and pushes it."""
        repo_name = f"test-repo-{str(uuid.uuid4())[:8]}"
        temp_dir = os.path.join("/tmp", repo_name)  # Use /tmp to isolate each repo
        try:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
            os.makedirs(temp_dir)

            # 1. Init Git
            run_command("git init", cwd=temp_dir)
            run_command("git checkout -b main", cwd=temp_dir)

            # 2. Add random files
            with open(os.path.join(temp_dir, "file1.py"), "w") as f:
                f.write(f"# Python test file {random.randint(1, 1000)}\nprint('Hello Radicle!')")
            with open(os.path.join(temp_dir, "README.md"), "w") as f:
                f.write(f"# Test Repo\nRandom content: {uuid.uuid4()}")
            run_command("git add .", cwd=temp_dir)
            commit_msg = f"Initial commit {time.time()}"
            git_commit_success, _, _ = run_command(f"git commit -m '{commit_msg}'", cwd=temp_dir)
            if not git_commit_success:
                bt.logging.error("Git commit failed.")
                return None, None, "Git commit failed"

            # Get commit hash
            _, commit_hash, _ = run_command("git rev-parse HEAD", cwd=temp_dir)
            if not commit_hash:
                bt.logging.error("Failed to get commit hash.")
                return None, None, "Failed to get commit hash"

            # 3. Init Radicle repo with passphrase via pexpect
            try:
                bt.logging.debug("Running rad init with passphrase via pexpect.")
                command = f"rad init --name {repo_name} --description 'Test repo for Bittensor validation' --default-branch main --public"
                child = pexpect.spawn(command, cwd=temp_dir, encoding="utf-8", timeout=70)
                # Optional logging to stdout
                # child.logfile = sys.stdout
                passphrase = "<YOUR_RADICAL_PASSPHRASE>" # Replace with your actual passphrase
                index = child.expect([
                    re.compile(r'(?i)passphrase.*:', re.IGNORECASE),
                    pexpect.EOF,
                    pexpect.TIMEOUT
                ])
                if index == 0:
                    child.sendline(passphrase)
                    child.expect(pexpect.EOF)
                    output = child.before
                    bt.logging.debug(f"Radicle init output (with passphrase): {output}")
                elif index == 1:
                    # No passphrase prompt; likely already unlocked
                    bt.logging.warning("Passphrase prompt not shown — identity might be already unlocked.")
                    output = child.before
                    bt.logging.debug(f"Radicle init output (no passphrase): {output}")
                else:
                    raise Exception("Timeout while waiting for rad init to prompt passphrase or complete.")
            except pexpect.exceptions.ExceptionPexpect as e:
                bt.logging.error(f"Radicle init via pexpect failed: {str(e)}")
                return None, None, f"Radicle init failed: {str(e)}"

            # 4. Get RID
            time.sleep(1)
            _, rid_stdout, _ = run_command("rad inspect --rid", cwd=temp_dir)
            bt.logging.debug(f"Radicle inspect output: {rid_stdout}")
            repo_rid = rid_stdout.strip()
            if not repo_rid.startswith("rad:"):
                bt.logging.error(f"Failed to get Radicle RID. stdout: '{rid_stdout}'")
                return None, None, f"Failed to get Radicle RID"
            bt.logging.info(f"Radicle project initialized. RID: {repo_rid}")
            bt.logging.info(f"Radicle project pushed successfully: {repo_rid}")
            return repo_rid, commit_hash, None
        except Exception as e:
            bt.logging.error(f"Error in create_and_push_radicle_repo: {e}\n{traceback.format_exc()}")
            return None, None, str(e)
        finally:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)

    def clone_repository_locally(self, repo_rid: str, miner_node_id: str) -> bool:
        """
        Attempts to clone the given Radicle repository into a temporary local directory.
        Returns True if successful, False otherwise.
        """
        if not repo_rid:
            bt.logging.error("No repo_rid provided for local clone.")
            return False

        # Create a unique temporary directory for this clone attempt
        base_clone_dir = "/tmp/validator_clones" # Validator's own temp clone space
        os.makedirs(base_clone_dir, exist_ok=True)
        
        # Sanitize RID for use in path if needed, or use a UUID for the dir name
        sanitized_rid_for_path = repo_rid.replace(":", "_").replace("/", "_")
        clone_target_dir = os.path.join(base_clone_dir, f"clone_{sanitized_rid_for_path}_{str(uuid.uuid4())[:8]}")

        bt.logging.info(f"Validator attempting to clone RID {repo_rid} into its local directory: {clone_target_dir}")
        try:
            # The command `rad clone <RID> <target_directory> --seed <NODEID>`
            # We add `--no-confirm` to avoid potential prompts if the identity is already seeding it.
            # And `--no-seed` because the validator isn't intending to become a long-term seeder from this action, just verify cloneability.
            clone_success_flag, stdout, stderr = run_command(f"rad clone {repo_rid} {clone_target_dir} --no-confirm --seed {miner_node_id} ")

            if clone_success_flag and os.path.exists(os.path.join(clone_target_dir, ".git")):
                bt.logging.info(f"Validator successfully cloned RID {repo_rid} to {clone_target_dir}.")
                return True
            else:
                bt.logging.warning(f"Validator failed to clone RID {repo_rid}. Success_flag: {clone_success_flag}, Stdout: '{stdout}', Stderr: '{stderr}'")
                return False
        except Exception as e:
            bt.logging.error(f"Exception during validator's local clone operation for {repo_rid}: {e}")
            return False
        finally:
            # Clean up the temporary clone directory
            if os.path.exists(clone_target_dir):
                try:
                    shutil.rmtree(clone_target_dir)
                    bt.logging.debug(f"Validator successfully removed its temporary clone directory: {clone_target_dir}")
                except Exception as e:
                    bt.logging.error(f"Validator error removing its temporary clone directory {clone_target_dir}: {e}")

    async def test_repository_unseeding(self, repo_rid: str, target_miner_uid: int, target_miner_node_id: str) -> bool:
        """
        Tests if a miner successfully unseeds a repository.
        1. Sends UNSEED_REPO request to the miner.
        2. If miner confirms command success, validator attempts to re-clone from that miner.
        3. Returns True if re-clone FAILS (unseeding verified), False otherwise.
        """
        bt.logging.info(f"Validator [test_unseeding]: Testing unseeding for RID {repo_rid} by UID {target_miner_uid} (Node ID: {target_miner_node_id})")

        # Step 1: Send UNSEED_REPO request
        unseed_synapse = RadicleSubnetSynapse(
            operation_type="UNSEED_REPO",
            repo_rid=repo_rid
        )
        
        target_axon = self.metagraph.axons[target_miner_uid]
        unseed_response: List[RadicleSubnetSynapse] = await self.dendrite.forward(
            axons=[target_axon], # Query single axon
            synapse=unseed_synapse,
            timeout=self.query_timeout # Adjust if unseed takes longer
        )

        if not unseed_response or len(unseed_response) == 0 or not unseed_response[0].dendrite or unseed_response[0].dendrite.status_code != 200:
            bt.logging.warning(f"Validator [test_unseeding]: No valid response from UID {target_miner_uid} for UNSEED_REPO. Dendrite status: {unseed_response[0].dendrite.status_code if unseed_response and len(unseed_response) > 0 and unseed_response[0].dendrite else 'N/A'}")
            return False # Cannot verify, consider test failed

        response = unseed_response[0]
        if not response.unseed_command_successful:
            bt.logging.warning(f"Validator [test_unseeding]: Miner UID {target_miner_uid} reported 'rad unseed' command FAILED. Error: {response.error_message}")
            return False # Miner failed to execute command, test failed.

        bt.logging.info(f"Validator [test_unseeding]: Miner UID {target_miner_uid} reported 'rad unseed' command SUCCESSFUL. Attempting verification clone...")

        # Step 2: Attempt to re-clone from the miner to verify unseeding
        # This reuses the logic pattern of your existing clone_repository_locally.
        base_reclone_dir = "/tmp/validator_post_unseed_clones"
        os.makedirs(base_reclone_dir, exist_ok=True)
        sanitized_rid_for_path = repo_rid.replace(":", "_").replace("/", "_")
        reclone_target_dir = os.path.join(base_reclone_dir, f"post_unseed_{sanitized_rid_for_path}_{str(uuid.uuid4())[:8]}")

        reclone_actually_failed = False
        try:
            # Important: clone from the specific miner's node_id
            clone_command = f"rad clone {repo_rid} {reclone_target_dir} --seed {target_miner_node_id} --no-follow --no-confirm"
            bt.logging.debug(f"Validator [test_unseeding]: Running re-clone command: {clone_command}")
            reclone_cmd_success, stdout, stderr = run_command(clone_command)

            if reclone_cmd_success and os.path.exists(os.path.join(reclone_target_dir, ".git")):
                bt.logging.warning(f"Validator [test_unseeding]: Re-clone of {repo_rid} from UID {target_miner_uid} (Node: {target_miner_node_id}) SUCCEEDED after unseed. Unseeding test FAILED for this miner.")
                reclone_actually_failed = False
            else:
                bt.logging.info(f"Validator [test_unseeding]: Re-clone of {repo_rid} from UID {target_miner_uid} (Node: {target_miner_node_id}) FAILED as expected after unseed. Unseeding test SUCCEEDED for this miner. Stdout: {stdout}, Stderr: {stderr}")
                reclone_actually_failed = True
        except Exception as e:
            bt.logging.error(f"Validator [test_unseeding]: Exception during re-clone attempt for {repo_rid} from UID {target_miner_uid}: {e}")
            reclone_actually_failed = True # If clone itself errors, treat as data not easily available
        finally:
            if os.path.exists(reclone_target_dir):
                try:
                    shutil.rmtree(reclone_target_dir)
                except Exception as e:
                    bt.logging.error(f"Validator [test_unseeding]: Error removing re-clone temp dir {reclone_target_dir}: {e}")
        
        return reclone_actually_failed # True if re-clone failed (unseed successful)

    async def run_sync_loop(self):
        """The main validation loop."""
        bt.logging.info("Starting validator sync loop.")

        while True:
            try:
                # --- Step 1: Validator creates a new Radicle repo and pushes it ---
                # This repo (repo_to_validate_rid) is what miners are expected to seed.
                bt.logging.info("Validator: Attempting to create and push a new Radicle repository for validation...")
                repo_to_validate_rid, commit_hash, push_error = self.create_and_push_radicle_repo()
                
                if push_error or not repo_to_validate_rid or not commit_hash:
                    bt.logging.error(f"Validator: Failed to create/push Radicle repo for validation round: {push_error}. Skipping.")
                    await asyncio.sleep(30) 
                    continue
                
                bt.logging.info(f"Validator: Successfully created and pushed test repo. RID: {repo_to_validate_rid}, Commit: {commit_hash}")

                # --- Step 2: Identify available miners ---
                available_uids = [uid for uid in self.metagraph.uids.tolist() if self.metagraph.axons[uid].is_serving]
                if not available_uids:
                    bt.logging.warning("Validator: No active miners found to query.")
                    self.metagraph.sync(subtensor=self.subtensor)
                    await asyncio.sleep(60) # Wait longer if no miners
                    continue
                
                bt.logging.info(f"Validator: Found {len(available_uids)} active miners to query: {available_uids}")
                
                current_round_scores = torch.zeros_like(self.scores) # Scores for this specific round
                miner_round_data = {} # Track detailed info for each miner

                # === Stage 3: Get Miner Status ===
                miner_status_synapse = RadicleSubnetSynapse(
                    operation_type="GET_MINER_STATUS"
                )
                bt.logging.info(f"Validator: Stage 3 - Querying {len(available_uids)} miners for MINER_STATUS...")

                target_axons_status = [self.metagraph.axons[uid] for uid in available_uids]
                miner_status_responses: List[RadicleSubnetSynapse] = await self.dendrite.forward(
                    axons=target_axons_status,
                    synapse=miner_status_synapse,
                    timeout=self.query_timeout 
                )

                uids_for_targeted_tests = []
                bt.logging.info(f"Validator: Received responses for MINER_STATUS from {len(miner_status_responses)} miners.")
                for i, uid in enumerate(available_uids):
                    resp = miner_status_responses[i]
                    miner_round_data[uid] = {}
                    if resp and resp.dendrite.status_code == 200 and resp.is_miner_radicle_node_running:
                        current_round_scores[uid] += 0.1 # Status score component
                        miner_round_data[uid]['node_id'] = resp.miner_radicle_node_id
                        miner_round_data[uid]['status_success'] = True
                        uids_for_targeted_tests.append(uid)
                        bt.logging.info(f"UID {uid}: Successfully confirmed MINER_STATUS. Miner Node ID: {resp.miner_radicle_node_id}, Seeded RIDs: {resp.seeded_rids_count}. Score +0.2")
                    elif resp:
                        error_msg = resp.error_message or "Miner did not confirm seeding or node is not running."
                        status_code = resp.dendrite.status_code
                        miner_round_data[uid]['status_success'] = False
                        bt.logging.warning(f"UID {uid}: Failed to confirm MINER_STATUS. Status: {status_code}, Miner Error: '{error_msg}'.")
                    else:
                        miner_round_data[uid]['status_success'] = False
                        bt.logging.warning(f"UID {uid}: No response or transport error for MINER_STATUS.")

                # === Stage 4: Query miners to explicitly VALIDATE THE PUSH (Miner confirms seeding) ===
                validate_push_synapse = RadicleSubnetSynapse(
                    operation_type="VALIDATE_PUSH",
                    repo_rid=repo_to_validate_rid,
                    commit_hash=commit_hash
                )
                bt.logging.info(f"Validator: Stage 4 - Querying {len(available_uids)} miners for VALIDATE_PUSH of RID {repo_to_validate_rid}...")

                target_axons_validate = [self.metagraph.axons[uid] for uid in available_uids]
                validate_push_responses: List[RadicleSubnetSynapse] = await self.dendrite.forward(
                    axons=target_axons_validate,
                    synapse=validate_push_synapse,
                    timeout=self.query_timeout 
                )
                
                for i, uid in enumerate(available_uids):
                    resp = validate_push_responses[i]
                    if resp and resp.dendrite.status_code == 200 and resp.validation_passed:
                        current_round_scores[uid] += 0.3 # Add score for miner's explicit confirmation
                        miner_round_data[uid]['validate_push_success'] = True
                        bt.logging.info(f"UID {uid}: Successfully confirmed VALIDATE_PUSH (seeded). Score +0.3")
                    elif resp:
                        error_msg = resp.error_message or "Validation_passed is false or missing."
                        status_code = resp.dendrite.status_code
                        miner_round_data[uid]['validate_push_success'] = False
                        bt.logging.warning(f"UID {uid}: Failed VALIDATE_PUSH. Status: {status_code}, Miner Error: '{error_msg}'.")
                    else:
                        miner_round_data[uid]['validate_push_success'] = False
                        bt.logging.warning(f"UID {uid}: No response or transport error for VALIDATE_PUSH.")

                # === Stage 5: Initial Clone Test by Validator ===
                bt.logging.info(f"Validator: Stage 5 - Testing initial clone of {repo_to_validate_rid} from miners with node_id...")
                for uid in uids_for_targeted_tests:
                    if miner_round_data[uid].get('status_success', False):
                        miner_node_id = miner_round_data[uid]['node_id']
                        bt.logging.info(f"Validator: UID {uid} has node_id {miner_node_id}. Attempting initial clone.")
                        
                        initial_clone_successful = self.clone_repository_locally(repo_to_validate_rid, miner_node_id)
                        miner_round_data[uid]['initial_clone_success'] = initial_clone_successful
                        if initial_clone_successful:
                            current_round_scores[uid] += 0.3 # Clone score component
                            bt.logging.info(f"UID {uid}: Initial clone test for {repo_to_validate_rid} SUCCEEDED. Score +0.3")
                        else:
                            bt.logging.warning(f"UID {uid}: Initial clone test for {repo_to_validate_rid} FAILED.")
                    else:
                        bt.logging.debug(f"UID {uid}: Skipping initial clone test as miner status was not successful.")

                # === Stage 6: Test Repository Unseeding by Miners ===
                bt.logging.info(f"Validator: Stage 6 - Testing UNSEED_REPO for {repo_to_validate_rid} with miners that allowed initial clone...")
                for uid in uids_for_targeted_tests: # Iterate through miners who had a node_id
                    if miner_round_data[uid].get('initial_clone_success', False): # Only test unseeding if initial clone from them was possible
                        miner_node_id_for_unseed_test = miner_round_data[uid]['node_id']
                        bt.logging.info(f"Validator: UID {uid} allowed initial clone. Proceeding to test UNSEED_REPO.")

                        unseeding_test_passed = await self.test_repository_unseeding(
                            repo_rid=repo_to_validate_rid,
                            target_miner_uid=uid,
                            target_miner_node_id=miner_node_id_for_unseed_test
                        )
                        miner_round_data[uid]['unseeding_test_passed'] = unseeding_test_passed
                        if unseeding_test_passed:
                            # Unseeding test passed means re-clone FAILED, which is good.
                            current_round_scores[uid] += 0.3 # Adjust score as needed
                            bt.logging.info(f"UID {uid}: UNSEED_REPO test for {repo_to_validate_rid} SUCCEEDED (re-clone failed). Score +0.15")
                        else:
                            bt.logging.warning(f"UID {uid}: UNSEED_REPO test for {repo_to_validate_rid} FAILED (re-clone succeeded or miner failed unseed cmd).")
                    else:
                        bt.logging.debug(f"UID {uid}: Skipping UNSEED_REPO test as initial clone was not successful or no node_id.")

                # --- Stage 7: Update moving average scores ---
                for uid_idx in range(self.metagraph.n.item()):
                    if uid_idx in available_uids: 
                        self.moving_avg_scores[uid_idx] = (
                            (1 - self.alpha) * self.moving_avg_scores[uid_idx] +
                            self.alpha * current_round_scores[uid_idx] # current_round_scores can be between 0 and 0.95 (0.2+0.3+0.3+0.15)
                        )
                
                bt.logging.info(f"Validator: Moving Average Scores: {['{:.3f}'.format(s.item()) for s in self.moving_avg_scores]}" )

                # --- Step 8: Set weights on Bittensor network ---
            
                current_block = self.subtensor.get_current_block()
                try:
                    last_set_weights_block = self.metagraph.last_update[self.my_subnet_uid].item()
                except Exception as e:
                    bt.logging.warning(f"Could not get last_set_weights_block for validator UID {self.my_subnet_uid}, defaulting to 0. Error: {e}")
                    last_set_weights_block = 0
                
                tempo = self.subtensor.tempo(self.config.netuid)

                if (current_block - last_set_weights_block) > tempo :
                    if torch.sum(self.moving_avg_scores) > 1e-6: # Check for sum being practically non-zero
                        weights_to_set = self.moving_avg_scores / torch.sum(self.moving_avg_scores)
                        weights_to_set = torch.nan_to_num(weights_to_set, nan=0.0) # Handle potential NaNs
                    else: 
                        weights_to_set = torch.zeros_like(self.moving_avg_scores)
                    
                    uids_for_weights = self.metagraph.uids 

                    bt.logging.info(f"Validator: Attempting to set weights: {['{:.3f}'.format(w.item()) for w in weights_to_set]} for UIDs: {uids_for_weights.tolist()}")
                    
                    success, message = self.subtensor.set_weights(
                        netuid=self.config.netuid,
                        wallet=self.wallet,
                        uids=uids_for_weights, 
                        weights=weights_to_set, 
                        wait_for_inclusion=True, 
                        wait_for_finalization=False
                        )
                    if success:
                        bt.logging.info(f"Validator: Successfully set weights: {message}")
                    else:
                        bt.logging.error(f"Validator: Failed to set weights: {message}")
                else:
                    bt.logging.info(f"Validator: Not time to set weights yet. Current block: {current_block}, Last set by me: {last_set_weights_block}, Tempo: {tempo}. Wait: {tempo - (current_block - last_set_weights_block)} blocks.")

                # --- Step 7: Sync metagraph and wait ---
                self.steps_passed += 1
                if self.steps_passed % 5 == 0: # Sync metagraph every 5 validation cycles
                    bt.logging.info("Validator: Syncing metagraph.")
                    self.metagraph.sync(subtensor=self.subtensor)
                    # Resize scores if metagraph size changed
                    if self.scores.size(0) != self.metagraph.n.item():
                        bt.logging.info("Validator: Metagraph size changed. Reinitializing scores and moving averages.")
                        self.scores = torch.zeros(self.metagraph.n.item(), dtype=torch.float32)
                        self.moving_avg_scores = torch.zeros(self.metagraph.n.item(), dtype=torch.float32)
                        # Also re-check available_uids as metagraph changed
                
                # Wait for a period before next validation cycle
                # Consider chain tempo or a fixed delay
                await asyncio.sleep(max(60, tempo // 2 if tempo > 0 else 60)) # e.g., wait at least 60s or half a tempo

            except RuntimeError as e:
                bt.logging.error(f"Validator: RuntimeError in validation loop: {e}")
                traceback.print_exc()
                await asyncio.sleep(60)  
            except KeyboardInterrupt:
                bt.logging.success("Validator: Keyboard interrupt detected. Exiting validator.")
                break
            except Exception as e:
                bt.logging.error(f"Validator: Unexpected error in validation loop: {e}")
                traceback.print_exc()
                await asyncio.sleep(60)

    def run(self):
        import asyncio
        asyncio.run(self.run_sync_loop())

if __name__ == "__main__":
    validator = Validator()
    validator.run()
