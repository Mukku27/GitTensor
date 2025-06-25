import os
import time
import uuid
import traceback
import asyncio
import shlex
import random
import re
import pexpect
import torch
import bittensor as bt
from dotenv import load_dotenv
import subprocess
import shutil
from typing import Optional, Tuple, List

# Import from captionize project structure
from gittensor.base.validator import BaseValidatorNeuron
from gittensor.protocol import RadicleSubnetSynapse # Use the new Radicle synapse
from gittensor.utils import  uids as uid_utils # Import new rad_utils and existing uids util

# Helper function to run shell commands
def run_command(command: str, suppress_error: bool = False, cwd: Optional[str] = None) -> Tuple[bool, str, str]:
    """Executes a shell command and returns success, stdout, and stderr."""
    try:
        bt.logging.debug(f"Running command: {command}")
        process = subprocess.Popen(shlex.split(command), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=cwd)
        stdout, stderr = process.communicate(timeout=60) # 60 second timeout
        success = process.returncode == 0
        if not success and not suppress_error:
            bt.logging.error(f"Command failed: {command}\nStderr: {stderr.strip()}\nStdout: {stdout.strip()}")
        return success, stdout.strip(), stderr.strip()
    except subprocess.TimeoutExpired:
        bt.logging.error(f"Command timed out: {command}")
        process.kill()
        return False, "", "Timeout expired"
    except Exception as e:
        bt.logging.error(f"Error running command {command}: {e}")
        return False, "", str(e)
    

class Validator(BaseValidatorNeuron):
    def __init__(self, config=None):
        super().__init__(config=config) # BaseValidatorNeuron handles wallet, subtensor, metagraph, dendrite, scores, logging
        
        bt.logging.info("Initializing Radicle GitTensor Validator...")

        
        self.setup_radicle_dependencies() # Check/install Radicle
        self.ensure_radicle_auth()
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
            return repo_rid, commit_hash, None , temp_dir
        except Exception as e:
            bt.logging.error(f"Error in create_and_push_radicle_repo: {e}\n{traceback.format_exc()}")
            return None, None, str(e)
        finally:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
       
        


    async def forward(self):
        """
        Validator forward pass. Consists of:
        1. Selecting miners to query.
        2. Validator creates a new Radicle repo and pushes it.
        3. Sending a `VALIDATE_PUSH` synapse to selected miners.
        4. Rewarding miners based on their success in seeding/validating.
        """
        bt.logging.info("Validator: Attempting to create and push a new Radicle repository for validation...")
        repo_to_validate_rid, commit_hash, push_error, local_validator_repo_path = self.create_and_push_radicle_repo()
                
        if push_error or not repo_to_validate_rid or not commit_hash:
            bt.logging.error(f"Validator: Failed to create/push Radicle repo for validation round: {push_error}. Skipping.")
            await asyncio.sleep(30) 
            return
                
        bt.logging.info(f"Validator: Successfully created and pushed test repo. RID: {repo_to_validate_rid}, Commit: {commit_hash}")

        # --- Step 2: Identify available miners ---
        available_uids = [uid for uid in self.metagraph.uids.tolist() if self.metagraph.axons[uid].is_serving]
        if not available_uids:
            bt.logging.warning("Validator: No active miners found to query.")
            self.metagraph.sync(subtensor=self.subtensor)
            await asyncio.sleep(60) # Wait longer if no miners
            return
                
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
                        current_round_scores[uid] += 1.0 # Add score for miner's explicit confirmation
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