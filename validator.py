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
        self.scores = bt.utils.weight_utils.construct_weights_tensor(self.metagraph.n.item())
        self.moving_avg_scores = bt.utils.weight_utils.construct_weights_tensor(self.metagraph.n.item())

        self.alpha = self.config.validator.alpha # Weight for moving average
        self.query_timeout = 15 # seconds for dendrite queries
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
        temp_dir = os.path.join("/tmp", repo_name) # Ensure /tmp is writable
        
        try:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
            os.makedirs(temp_dir)

            # 1. Init git project
            run_command("git init", cwd=temp_dir)
            
            # 2. Add random files
            with open(os.path.join(temp_dir, "file1.py"), "w") as f:
                f.write(f"# Python test file {random.randint(1,1000)}\nprint('Hello Radicle!')")
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


            # 3. Init Radicle project
            # `rad init` asks for name, description, default branch. Provide them non-interactively.
            # Using a simpler `rad init` that might use defaults or prompt if not fully automated.
            # For non-interactive: rad init --name <name> --description <desc> --default-branch <branch>
            # For now, let's try a simple rad init and see if it works without prompts
            # or if the environment is set up for non-interactive use.
            # The project name usually defaults to the directory name.
            rad_init_success, rad_init_stdout, rad_init_stderr = run_command(
                f"rad init --name {repo_name} --description 'Test repo for Bittensor validation' --default-branch main --no-confirm",
                cwd=temp_dir
            )
            if not rad_init_success:
                bt.logging.error(f"Radicle init failed: {rad_init_stderr} {rad_init_stdout}")
                return None, None, f"Radicle init failed: {rad_init_stderr} {rad_init_stdout}"
            
            # Extract RID from `rad init` stdout (it usually prints the RID)
            # Or get it from `rad .` (rad self for project)
            time.sleep(1) # give a moment for .rad/id to be written potentially
            _, rid_stdout, rid_stderr = run_command("rad . id", cwd=temp_dir) # Gets project ID (RID)
            repo_rid = rid_stdout.strip()
            if not repo_rid.startswith("rad:"):
                 bt.logging.error(f"Failed to get Radicle RID. stdout: '{rid_stdout}', stderr: '{rid_stderr}'")
                 return None, None, f"Failed to get Radicle RID: {rid_stderr}"
            bt.logging.info(f"Radicle project initialized. RID: {repo_rid}")

            # 4. Push to network
            # Assumes miner's seed node is discoverable and configured
            rad_push_success, rad_push_stdout, rad_push_stderr = run_command("rad push", cwd=temp_dir)
            if not rad_push_success:
                bt.logging.error(f"Radicle push failed: {rad_push_stderr} {rad_push_stdout}")
                return repo_rid, commit_hash, f"Radicle push failed: {rad_push_stderr} {rad_push_stdout}"

            bt.logging.info(f"Radicle project pushed successfully: {repo_rid}")
            return repo_rid, commit_hash, None # RID, commit_hash, error_message

        except Exception as e:
            bt.logging.error(f"Error in create_and_push_radicle_repo: {e}\n{traceback.format_exc()}")
            return None, None, str(e)
        finally:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir) # Clean up

    async def run_sync_loop(self):
        """The main validation loop."""
        bt.logging.info("Starting validator sync loop.")

        while True:
            try:
                # --- Step 1: Create a new Radicle repo and push it ---
                bt.logging.info("Attempting to create and push a new Radicle repository...")
                repo_rid, commit_hash, push_error = self.create_and_push_radicle_repo()

                if push_error or not repo_rid or not commit_hash:
                    bt.logging.error(f"Failed to create/push Radicle repo: {push_error}. Skipping validation round for this attempt.")
                    # Score miners based on availability / GET_MINER_STATUS if desired, or just wait.
                    await asyncio.sleep(30) # Wait before retrying local push
                    continue
                
                bt.logging.info(f"Successfully pushed test repo. RID: {repo_rid}, Commit: {commit_hash}")

                # --- Step 2: Query miners to validate the push and get their status ---
                # Get a random sample of active miners to query.
                # Query all active miners in this example. For larger subnets, sampling is better.
                available_uids = [uid for uid in self.metagraph.uids.tolist() if self.metagraph.axons[uid].is_serving]
                if not available_uids:
                    bt.logging.warning("No active miners found to query.")
                    await asyncio.sleep(self.config.subtensor.target_block_time * 2) # Wait for metagraph update
                    continue
                
                # Create Synapse for VALIDATE_PUSH
                validate_synapse = RadicleSubnetSynapse(
                    operation_type="VALIDATE_PUSH",
                    repo_rid=repo_rid,
                    commit_hash=commit_hash
                )
                
                bt.logging.info(f"Querying {len(available_uids)} available miners to validate push of RID {repo_rid}...")
                
                # Query axons for VALIDATE_PUSH
                # Note: dendrite.query returns List[Synapse], not List[Response]. Access attrs directly.
                validate_responses: List[RadicleSubnetSynapse] = await self.dendrite.forward(
                    axons=[self.metagraph.axons[uid] for uid in available_uids],
                    synapse=validate_synapse,
                    timeout=self.query_timeout 
                )
                
                current_scores = self.scores.clone() # Work with a copy for this round's scoring

                for i, uid in enumerate(available_uids):
                    resp = validate_responses[i]
                    score = 0.0
                    if resp and resp.validation_passed and resp.status_message == "SUCCESS":
                        score = 1.0 # Full score if miner successfully validated/seeded
                        bt.logging.info(f"UID {uid}: Successfully validated push.")
                    elif resp and resp.error_message:
                        bt.logging.warning(f"UID {uid}: Failed push validation. Error: {resp.error_message}")
                    else:
                        bt.logging.warning(f"UID {uid}: No response or invalid response for push validation.")
                    
                    # Update score for this specific UID
                    current_scores[uid] = score


                # --- (Optional) Step 3: Query miners for their general status ---
                # This could be done less frequently or combined with validation score.
                # For simplicity, we'll focus on the push validation score for now.
                # If you want to include GET_MINER_STATUS:
                # status_synapse = RadicleSubnetSynapse(operation_type="GET_MINER_STATUS")
                # status_responses = await self.dendrite.forward(...)
                # Factor status_responses (e.g., is_miner_radicle_node_running, seeded_rids_count) into current_scores[uid]

                # --- Step 4: Update moving average scores ---
                for uid_idx, score_value in enumerate(current_scores):
                    # Only update for UIDs that were part of the current query (available_uids)
                    # For others, their moving average remains, or decays slowly (not implemented here)
                    if uid_idx in available_uids:
                        self.moving_avg_scores[uid_idx] = (
                            (1 - self.alpha) * self.moving_avg_scores[uid_idx] + self.alpha * score_value
                        )
                
                bt.logging.info(f"Moving Average Scores: {['{:.3f}'.format(s.item()) for s in self.moving_avg_scores]}")

                # --- Step 5: Set weights on Bittensor network ---
                # Check if it's time to set weights based on chain tempo
                current_block = self.subtensor.get_current_block()
                last_set_weights_block = self.subtensor.get_last_set_weights_block(self.config.netuid, self.my_subnet_uid)
                tempo = self.subtensor.tempo(self.config.netuid)

                if current_block - last_set_weights_block > tempo :
                    if sum(self.moving_avg_scores) > 0:
                        weights_to_set = self.moving_avg_scores / sum(self.moving_avg_scores)
                    else: # Avoid division by zero if all scores are 0
                        weights_to_set = bt.utils.weight_utils.construct_weights_tensor(self.metagraph.n.item()) # Set to zeros or uniform

                    bt.logging.info(f"Setting weights: {['{:.3f}'.format(w.item()) for w in weights_to_set]}")
                    
                    success, message = self.subtensor.set_weights(
                        netuid=self.config.netuid,
                        wallet=self.wallet,
                        uids=self.metagraph.uids,
                        weights=weights_to_set,
                        wait_for_inclusion=False, # Faster, but check inclusion separately if needed
                        version_key=bt.__version_as_int__ # Protocol version
                    )
                    if success:
                        bt.logging.info(f"Successfully set weights: {message}")
                    else:
                        bt.logging.error(f"Failed to set weights: {message}")
                else:
                    bt.logging.info(f"Not time to set weights yet. Current block: {current_block}, Last set: {last_set_weights_block}, Tempo: {tempo}")


                # --- Step 6: Sync metagraph and wait ---
                self.steps_passed += 1
                if self.steps_passed % 5 == 0: # Sync metagraph every 5 cycles
                    bt.logging.info("Syncing metagraph.")
                    self.metagraph.sync(subtensor=self.subtensor)
                    # Resize scores if metagraph size changed
                    if self.scores.size(0) != self.metagraph.n.item():
                        bt.logging.info("Metagraph size changed. Reinitializing scores.")
                        self.scores = bt.utils.weight_utils.construct_weights_tensor(self.metagraph.n.item())
                        self.moving_avg_scores = bt.utils.weight_utils.construct_weights_tensor(self.metagraph.n.item())


                # Wait for a period before next validation cycle
                # Consider chain tempo or a fixed delay
                await asyncio.sleep(max(30, tempo // 2)) # e.g., wait at least 30s or half a tempo

            except RuntimeError as e:
                bt.logging.error(f"RuntimeError in validation loop: {e}")
                traceback.print_exc()
                await asyncio.sleep(60) # Wait longer after runtime errors
            except KeyboardInterrupt:
                bt.logging.success("Keyboard interrupt detected. Exiting validator.")
                break
            except Exception as e:
                bt.logging.error(f"Unexpected error in validation loop: {e}")
                traceback.print_exc()
                await asyncio.sleep(60) # Wait after unexpected errors
    
    def run(self):
        import asyncio
        asyncio.run(self.run_sync_loop())


if __name__ == "__main__":
    validator = Validator()
    validator.run()