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
                passphrase = "<YOUR_RADICAL_PASSPHRASE" # Replace with your actual passphrase
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

    async def run_sync_loop(self):
        """The main validation loop."""
        bt.logging.info("Starting validator sync loop.")

        while True:
            try:
                # --- Step 1: Validator creates a new Radicle repo and pushes it ---
                bt.logging.info("Attempting to create and push a new Radicle repository...")
                repo_rid, commit_hash, push_error = self.create_and_push_radicle_repo()
                
                if push_error or not repo_rid or not commit_hash:
                    bt.logging.error(f"Failed to create/push Radicle repo for validation: {push_error}. Skipping validation round.")
                    await asyncio.sleep(30)  
                    continue
                
                bt.logging.info(f"Successfully created and pushed test repo. RID: {repo_rid}, Commit: {commit_hash}")

                # --- Step 2: Identify available miners ---
                available_uids = [uid for uid in self.metagraph.uids.tolist() if self.metagraph.axons[uid].is_serving]
                if not available_uids:
                    bt.logging.warning("No active miners found to query.")
                    self.metagraph.sync(subtensor=self.subtensor) # Sync to find new miners
                    await asyncio.sleep(self.config.subtensor.target_block_time * 2 if hasattr(self.config.subtensor, 'target_block_time') else 60)
                    continue
                
                bt.logging.info(f"Found {len(available_uids)} active miners to query: {available_uids}")
                
                current_round_scores = torch.zeros_like(self.scores) # Scores for this specific round

                # --- Step 3: Query miners to VALIDATE THE PUSH (Miner seeds the repo) ---
                validate_push_synapse = RadicleSubnetSynapse(
                    operation_type="VALIDATE_PUSH",
                    repo_rid=repo_rid,
                    commit_hash=commit_hash
                )
                bt.logging.info(f"Querying {len(available_uids)} miners for VALIDATE_PUSH of RID {repo_rid}...")
                
                validate_push_responses: List[RadicleSubnetSynapse] = await self.dendrite.forward(
                    axons=[self.metagraph.axons[uid] for uid in available_uids],
                    synapse=validate_push_synapse,
                    timeout=self.query_timeout 
                )
                
                for i, uid in enumerate(available_uids):
                    resp = validate_push_responses[i]
                    if resp and resp.dendrite.status_code == 200 and resp.validation_passed:
                        current_round_scores[uid] += 0.5 # Award 0.5 points for successful push validation
                        bt.logging.info(f"UID {uid}: Successfully validated push (seeded). Partial score: {current_round_scores[uid]}")
                    else:
                        error_msg = resp.error_message if resp and resp.error_message else "No/failed response or validation_passed is false."
                        status_code = resp.dendrite.status_code if resp and resp.dendrite else "N/A"
                        bt.logging.warning(f"UID {uid}: Failed VALIDATE_PUSH. Status: {status_code}, Error: '{error_msg}'")

                # --- Step 4: Query miners to CLONE THE REPO ---
                clone_repo_synapse = RadicleSubnetSynapse(
                    operation_type="CLONE_REPO",
                    repo_rid=repo_rid  
                    # commit_hash is not needed for clone operation directly by miner
                )
                bt.logging.info(f"Querying {len(available_uids)} miners for CLONE_REPO of RID {repo_rid}...")

                clone_repo_responses: List[RadicleSubnetSynapse] = await self.dendrite.forward(
                    axons=[self.metagraph.axons[uid] for uid in available_uids],
                    synapse=clone_repo_synapse,
                    timeout=self.query_timeout # Potentially longer timeout for cloning
                )

                for i, uid in enumerate(available_uids):
                    resp = clone_repo_responses[i]
                    if resp and resp.dendrite.status_code == 200 and resp.clone_success:
                        current_round_scores[uid] += 0.5 # Award 0.5 points for successful clone
                        bt.logging.info(f"UID {uid}: Successfully cloned repo. Total score this round: {current_round_scores[uid]}")
                    else:
                        error_msg = resp.error_message if resp and resp.error_message else "No/failed response or clone_success is false."
                        status_code = resp.dendrite.status_code if resp and resp.dendrite else "N/A"
                        bt.logging.warning(f"UID {uid}: Failed CLONE_REPO. Status: {status_code}, Error: '{error_msg}'")
                
                # --- Step 5: Update moving average scores ---
                for uid_idx in range(self.metagraph.n.item()): # Iterate all possible UIDs
                    if uid_idx in available_uids: # Only update those queried
                        # Blend the current round's score (0 to 1.0) into the moving average
                        self.moving_avg_scores[uid_idx] = (
                            (1 - self.alpha) * self.moving_avg_scores[uid_idx] +
                            self.alpha * current_round_scores[uid_idx]
                        )
                    # Optionally, decay scores for UIDs not queried (e.g., self.moving_avg_scores[uid_idx] *= (1 - some_decay_factor))
                
                bt.logging.info(f"Moving Average Scores: {['{:.3f}'.format(s.item()) for s in self.moving_avg_scores]}")

                # --- Step 6: Set weights on Bittensor network ---
                current_block = self.subtensor.get_current_block()
                # Use self.metagraph.last_update[self.my_subnet_uid].item() for last set weights block for this validator
                # However, a simpler approach is to use self.subtensor.get_last_set_weights_block(netuid, uid)
                # For now, your existing logic for last_set_weights_block is:
                # last_set_weights_block = self.metagraph.last_update[self.my_subnet_uid].item() # This is last update time of neuron, not necessarily last set_weights from this validator
                # A more direct way:
                try:
                    last_set_weights_block = self.subtensor.get_last_set_weights_block(self.config.netuid, self.wallet.hotkey.ss58_address)
                except Exception as e:
                    bt.logging.warning(f"Could not get last_set_weights_block for validator UID {self.my_subnet_uid}, defaulting to 0. Error: {e}")
                    last_set_weights_block = 0 # Default if call fails (e.g. validator not yet set weights)


                tempo = self.subtensor.tempo(self.config.netuid)

                if (current_block - last_set_weights_block) > tempo :
                    if torch.sum(self.moving_avg_scores) > 0:
                        weights_to_set = self.moving_avg_scores / torch.sum(self.moving_avg_scores)
                        # Ensure no NaN values if sum is very small, though sum > 0 check helps
                        weights_to_set = torch.nan_to_num(weights_to_set, nan=0.0)
                    else:  
                        # If all scores are zero, set uniform weights for all *active* miners, or just zeros.
                        # Setting zeros is safer if no one is performing.
                        weights_to_set = torch.zeros_like(self.moving_avg_scores)
                    
                    # Ensure UIDs and weights tensors are correctly aligned and sized
                    uids_for_weights = self.metagraph.uids # This is a tensor of UIDs

                    bt.logging.info(f"Attempting to set weights: {['{:.3f}'.format(w.item()) for w in weights_to_set]} for UIDs: {uids_for_weights.tolist()}")
                    
                    success, message = self.subtensor.set_weights(
                        netuid=self.config.netuid,
                        wallet=self.wallet,
                        uids=uids_for_weights, # Make sure this is a 1D tensor of UIDs
                        weights=weights_to_set, # Make sure this is a 1D tensor of weights
                        wait_for_inclusion=True, # Recommended for critical ops
                        wait_for_finalization=False, # Faster, inclusion is usually enough
                        version_key = bt.__version_as_int__ # Use current bittensor version as protocol key
                    )
                    if success:
                        bt.logging.info(f"Successfully set weights: {message}")
                    else:
                        bt.logging.error(f"Failed to set weights: {message}")
                else:
                    bt.logging.info(f"Not time to set weights yet. Current block: {current_block}, Last set by me: {last_set_weights_block}, Tempo: {tempo}. Wait: {tempo - (current_block - last_set_weights_block)} blocks.")

                # --- Step 7: Sync metagraph and wait ---
                self.steps_passed += 1
                if self.steps_passed % 5 == 0: # Sync metagraph every 5 validation cycles
                    bt.logging.info("Syncing metagraph.")
                    self.metagraph.sync(subtensor=self.subtensor)
                    # Resize scores if metagraph size changed
                    if self.scores.size(0) != self.metagraph.n.item():
                        bt.logging.info("Metagraph size changed. Reinitializing scores and moving averages.")
                        self.scores = torch.zeros(self.metagraph.n.item(), dtype=torch.float32)
                        self.moving_avg_scores = torch.zeros(self.metagraph.n.item(), dtype=torch.float32)
                        # Also re-check available_uids as metagraph changed
                
                # Wait for a period before next validation cycle
                # Consider chain tempo or a fixed delay
                await asyncio.sleep(max(60, tempo // 2 if tempo > 0 else 60)) # e.g., wait at least 60s or half a tempo

            except RuntimeError as e:
                bt.logging.error(f"RuntimeError in validation loop: {e}")
                traceback.print_exc()
                await asyncio.sleep(60)  
            except KeyboardInterrupt:
                bt.logging.success("Keyboard interrupt detected. Exiting validator.")
                break
            except Exception as e:
                bt.logging.error(f"Unexpected error in validation loop: {e}")
                traceback.print_exc()
                await asyncio.sleep(60)

    def run(self):
        import asyncio
        asyncio.run(self.run_sync_loop())

if __name__ == "__main__":
    validator = Validator()
    validator.run()