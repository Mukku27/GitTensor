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
from typing import Tuple, Optional, List, Dict # Added Dict
import asyncio
import pexpect
import re
import torch
from protocol import RadicleSubnetSynapse

# Helper function to run shell commands (remains unchanged)
def run_command(command: str, suppress_error: bool = False, cwd: Optional[str] = None) -> Tuple[bool, str, str]:
    """Executes a shell command and returns success, stdout, and stderr."""
    try:
        bt.logging.debug(f"Running command: {command} (cwd: {cwd})")
        process = subprocess.Popen(shlex.split(command), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=cwd)
        stdout, stderr = process.communicate(timeout=120)
        success = process.returncode == 0
        if not success and not suppress_error:
            bt.logging.error(f"Command failed: {command}\nStderr: {stderr.strip()}\nStdout: {stdout.strip()}")
        return success, stdout.strip(), stderr.strip()
    except subprocess.TimeoutExpired:
        bt.logging.error(f"Command timed out: {command}")
        if process.poll() is None: process.kill()
        return False, "", "Timeout expired"
    except Exception as e:
        bt.logging.error(f"Error running command {command}: {e}")
        return False, "", str(e)

class Validator:
    def __init__(self):
        self.config = self.get_config()
        self.setup_logging()
        self.setup_radicle_dependencies()
        self.ensure_radicle_auth()
        self.setup_bittensor_objects()
        self.scores = torch.zeros(self.metagraph.n.item(), dtype=torch.float32)
        self.moving_avg_scores = torch.zeros(self.metagraph.n.item(), dtype=torch.float32)
        self.alpha = self.config.validator.alpha
        self.query_timeout = 55 # Increased timeout for Radicle operations
        self.steps_passed = 0

    def get_config(self): # (remains unchanged from your provided code)
        parser = argparse.ArgumentParser()
        parser.add_argument(
            "--radicle.validator.alias",
            default=f"bittensor-validator-{str(uuid.uuid4())[:8]}", 
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

    def setup_logging(self): # (remains unchanged)
        bt.logging(config=self.config, logging_dir=self.config.full_path)
        bt.logging.info(f"Running validator for subnet: {self.config.netuid} on network: {self.config.subtensor.network} with config:")
        bt.logging.info(self.config)

    def setup_radicle_dependencies(self): # (remains unchanged)
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

    def ensure_radicle_auth(self): # (remains unchanged)
        bt.logging.info("Ensuring Radicle identity for validator...")
        radicle_home_keys = os.path.expanduser("~/.radicle/keys")
        if not os.path.exists(radicle_home_keys):
            bt.logging.info(f"Radicle keys not found. Attempting to authenticate as '{self.config.radicle.validator.alias}'.")
            success, stdout, stderr = run_command(f"rad auth --alias {self.config.radicle.validator.alias}")
            if not success:
                bt.logging.error(f"Failed to authenticate Radicle identity for validator: {stderr}. Please run 'rad auth --alias {self.config.radicle.validator.alias}' manually.")
                exit(1)
            bt.logging.info(f"Radicle identity created/selected for validator: {stdout}")
        else:
            bt.logging.info(f"Radicle keys directory found. Assuming identity '{self.config.radicle.validator.alias}' is available or will be created/used by rad commands.")
            run_command(f"rad auth {self.config.radicle.validator.alias}", suppress_error=True)

    def setup_bittensor_objects(self): # (remains unchanged)
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

    def create_and_push_radicle_repo(self) -> Tuple[Optional[str], Optional[str], Optional[str]]: # (remains unchanged from your provided code)
        """Creates a temporary Git repo, initializes it with Radicle, and pushes it."""
        repo_name = f"test-repo-{str(uuid.uuid4())[:8]}"
        temp_dir = os.path.join("/tmp", repo_name)
        try:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
            os.makedirs(temp_dir)
            run_command("git init", cwd=temp_dir)
            run_command("git checkout -b main", cwd=temp_dir)
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
            _, commit_hash, _ = run_command("git rev-parse HEAD", cwd=temp_dir)
            if not commit_hash:
                bt.logging.error("Failed to get commit hash.")
                return None, None, "Failed to get commit hash"
            try:
                bt.logging.debug("Running rad init with passphrase via pexpect.")
                command = f"rad init --name {repo_name} --description 'Test repo for Bittensor validation' --default-branch main --public"
                child = pexpect.spawn(command, cwd=temp_dir, encoding="utf-8", timeout=70)
                passphrase = "<YOUR_RADICAL_PASSPHRASE" # Replace with your actual passphrase or load securely
                index = child.expect([
                    re.compile(r'(?i)passphrase.*:', re.IGNORECASE),
                    pexpect.EOF,
                    pexpect.TIMEOUT
                ])
                if index == 0:
                    child.sendline(passphrase)
                    child.expect(pexpect.EOF)
                    # output = child.before # No need to assign to output if not used later
                    # bt.logging.debug(f"Radicle init output (with passphrase): {output}")
                elif index == 1:
                    # output = child.before
                    # bt.logging.debug(f"Radicle init output (no passphrase): {output}")
                    bt.logging.warning("Passphrase prompt not shown — identity might be already unlocked.")
                else: # index == 2 (TIMEOUT)
                    raise Exception("Timeout while waiting for rad init to prompt passphrase or complete.")
            except pexpect.exceptions.ExceptionPexpect as e:
                bt.logging.error(f"Radicle init via pexpect failed: {str(e)}")
                return None, None, f"Radicle init failed: {str(e)}"
            time.sleep(1)
            _, rid_stdout, _ = run_command("rad inspect --rid", cwd=temp_dir) # Use rad inspect --rid
            repo_rid = rid_stdout.strip()
            if not repo_rid.startswith("rad:"):
                bt.logging.error(f"Failed to get Radicle RID. stdout: '{rid_stdout}'")
                return None, None, f"Failed to get Radicle RID"
            
            # Perform rad push after successful rad init
            push_success, push_stdout, push_stderr = run_command("rad push --all", cwd=temp_dir)
            if not push_success:
                bt.logging.error(f"Radicle push failed for {repo_rid}: {push_stderr} {push_stdout}")
                return repo_rid, commit_hash, f"Radicle push failed: {push_stderr}"

            bt.logging.info(f"Radicle project initialized and pushed. RID: {repo_rid}")
            return repo_rid, commit_hash, None
        except Exception as e:
            bt.logging.error(f"Error in create_and_push_radicle_repo: {e}\n{traceback.format_exc()}")
            return None, None, str(e)
        finally:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)

    def clone_repository_locally(self, repo_rid: str, miner_node_id: str) -> bool:
        """
        Attempts to clone the given Radicle repository using a specific miner's node ID.
        Returns True if successful, False otherwise.
        """
        if not repo_rid:
            bt.logging.error("Validator: No repo_rid provided for local clone.")
            return False
        if not miner_node_id: # Ensure miner_node_id is provided
            bt.logging.error(f"Validator: No miner_node_id provided for cloning RID {repo_rid}.")
            return False

        base_clone_dir = "/tmp/validator_clones"
        os.makedirs(base_clone_dir, exist_ok=True)
        
        sanitized_rid_for_path = repo_rid.replace(":", "_").replace("/", "_")
        # Include part of miner_node_id in temp dir name for clarity if debugging multiple clones
        sanitized_miner_id_for_path = miner_node_id.replace(":", "_").replace("/", "_")[-12:]
        clone_target_dir = os.path.join(base_clone_dir, f"clone_{sanitized_rid_for_path}_from_{sanitized_miner_id_for_path}_{str(uuid.uuid4())[:8]}")

        bt.logging.info(f"Validator: Attempting to clone RID {repo_rid} from miner {miner_node_id} into: {clone_target_dir}")
        try:
            # Use --seed <NODE_ID> to specify the peer to fetch from.
            # --no-follow is used because we don't want the validator to start following the project by default from this action.
            # --no-confirm to avoid interactive prompts.
            clone_command = f"rad clone {repo_rid} {clone_target_dir} --seed {miner_node_id} --no-follow --no-confirm"
            bt.logging.debug(f"Validator: Running clone command: {clone_command}")
            clone_success_flag, stdout, stderr = run_command(clone_command)

            if clone_success_flag and os.path.exists(os.path.join(clone_target_dir, ".git")):
                bt.logging.info(f"Validator: Successfully cloned RID {repo_rid} from miner {miner_node_id} to {clone_target_dir}.")
                return True
            else:
                bt.logging.warning(f"Validator: Failed to clone RID {repo_rid} from miner {miner_node_id}. Success_flag: {clone_success_flag}, Stdout: '{stdout}', Stderr: '{stderr}'")
                return False
        except Exception as e:
            bt.logging.error(f"Validator: Exception during local clone from miner {miner_node_id} for {repo_rid}: {e}")
            return False
        finally:
            if os.path.exists(clone_target_dir):
                try:
                    shutil.rmtree(clone_target_dir)
                except Exception as e:
                    bt.logging.error(f"Validator: Error removing temp clone dir {clone_target_dir}: {e}")

    async def run_sync_loop(self):
        bt.logging.info("Starting validator sync loop.")

        while True:
            try:
                # === Stage 1: Create and Push Repository by Validator ===
                bt.logging.info("Validator: Stage 1 - Creating and pushing a new Radicle repository...")
                repo_to_validate_rid, commit_hash, push_error = self.create_and_push_radicle_repo()
                
                if push_error or not repo_to_validate_rid or not commit_hash:
                    bt.logging.error(f"Validator: Failed Stage 1 (create/push repo): {push_error}. Skipping round.")
                    await asyncio.sleep(30) 
                    continue
                bt.logging.info(f"Validator: Stage 1 complete. Pushed RID: {repo_to_validate_rid}, Commit: {commit_hash}")

                # === Stage 2: Identify Available Miners ===
                available_uids = [uid for uid in self.metagraph.uids.tolist() if self.metagraph.axons[uid].is_serving]
                if not available_uids:
                    bt.logging.warning("Validator: No active miners found. Syncing metagraph and retrying.")
                    self.metagraph.sync(subtensor=self.subtensor)
                    await asyncio.sleep(60)
                    continue
                bt.logging.info(f"Validator: Stage 2 complete. Found {len(available_uids)} active miners: {available_uids}")
                
                current_round_scores = torch.zeros_like(self.scores)
                miner_data_for_round: Dict[int, Dict[str, any]] = {uid: {} for uid in available_uids} # Store data per miner UID

                # === Stage 3: Get Miner Status (and Node IDs) ===
                bt.logging.info("Validator: Stage 3 - Querying miners for GET_MINER_STATUS...")
                get_status_synapse = RadicleSubnetSynapse(operation_type="GET_MINER_STATUS")
                target_axons_status = [self.metagraph.axons[uid] for uid in available_uids]
                
                get_status_responses: List[RadicleSubnetSynapse] = await self.dendrite.forward(
                    axons=target_axons_status,
                    synapse=get_status_synapse,
                    timeout=self.query_timeout
                )

                for i, uid in enumerate(available_uids):
                    resp = get_status_responses[i]
                    if resp and resp.dendrite.status_code == 200 and resp.is_miner_radicle_node_running and resp.miner_radicle_node_id:
                        miner_data_for_round[uid]['node_id'] = resp.miner_radicle_node_id
                        miner_data_for_round[uid]['alias'] = resp.miner_radicle_node_alias
                        miner_data_for_round[uid]['seeded_count'] = resp.seeded_rids_count
                        current_round_scores[uid] += 0.0 # Score for being responsive and providing Node ID
                        bt.logging.info(f"UID {uid}: GET_MINER_STATUS success. Node ID: {resp.miner_radicle_node_id}. Score +0.2")
                    elif resp:
                        bt.logging.warning(f"UID {uid}: GET_MINER_STATUS failed or incomplete. Status: {resp.dendrite.status_code}, Error: {resp.error_message or 'Node not running/no ID'}")
                    else:
                        bt.logging.warning(f"UID {uid}: No response for GET_MINER_STATUS.")
                
                # Filter UIDs that provided a node_id for subsequent steps
                uids_with_node_id = [uid for uid, data in miner_data_for_round.items() if 'node_id' in data]
                if not uids_with_node_id:
                    bt.logging.warning("Validator: No miners provided valid Node IDs. Skipping VALIDATE_PUSH and CLONE for this round.")
                    # Proceed to update scores (which will be low) and set weights
                else:
                    bt.logging.info(f"Validator: {len(uids_with_node_id)} miners provided Node IDs: {uids_with_node_id}")

                    # === Stage 4: Validate Push (Miner Seeds Repo) ===
                    bt.logging.info(f"Validator: Stage 4 - Querying {len(uids_with_node_id)} miners for VALIDATE_PUSH of RID {repo_to_validate_rid}...")
                    validate_push_synapse = RadicleSubnetSynapse(
                        operation_type="VALIDATE_PUSH",
                        repo_rid=repo_to_validate_rid,
                        commit_hash=commit_hash
                    )
                    target_axons_validate = [self.metagraph.axons[uid] for uid in uids_with_node_id]
                    
                    validate_push_responses: List[RadicleSubnetSynapse] = await self.dendrite.forward(
                        axons=target_axons_validate,
                        synapse=validate_push_synapse,
                        timeout=self.query_timeout
                    )

                    for i, uid in enumerate(uids_with_node_id): # Iterate based on the order of uids_with_node_id
                        resp = validate_push_responses[i]
                        if resp and resp.dendrite.status_code == 200 and resp.validation_passed:
                            miner_data_for_round[uid]['validated_push'] = True
                            current_round_scores[uid] += 0.4 # Score for successful seed confirmation
                            bt.logging.info(f"UID {uid}: VALIDATE_PUSH success. Score +0.5")
                        elif resp:
                            miner_data_for_round[uid]['validated_push'] = False
                            bt.logging.warning(f"UID {uid}: VALIDATE_PUSH failed. Status: {resp.dendrite.status_code}, Error: {resp.error_message or 'validation_passed is False'}")
                        else:
                            miner_data_for_round[uid]['validated_push'] = False
                            bt.logging.warning(f"UID {uid}: No response for VALIDATE_PUSH.")
                    
                    # === Stage 5: Validator Clones from Miners that Validated Push ===
                    bt.logging.info("Validator: Stage 5 - Attempting to clone repo from miners that successfully validated push...")
                    for uid in uids_with_node_id:
                        if miner_data_for_round[uid].get('validated_push', False):
                            miner_node_id_to_clone_from = miner_data_for_round[uid]['node_id']
                            bt.logging.info(f"Validator: Attempting clone for UID {uid} from its Node ID {miner_node_id_to_clone_from} for RID {repo_to_validate_rid}")
                            
                            clone_success = self.clone_repository_locally(repo_to_validate_rid, miner_node_id_to_clone_from)
                            if clone_success:
                                current_round_scores[uid] += 0.5 # Score for successful clone from this miner
                                bt.logging.info(f"UID {uid}: CLONE success from its node. Score +0.4")
                            else:
                                bt.logging.warning(f"UID {uid}: CLONE failed from its node {miner_node_id_to_clone_from}.")
                        else:
                             bt.logging.debug(f"UID {uid}: Skipping clone attempt as VALIDATE_PUSH was not successful for this miner.")


                # === Stage 6: Update Moving Average Scores ===
                for uid_idx in range(self.metagraph.n.item()): # Iterate all possible UIDs
                    if uid_idx in available_uids: # Check if this UID was part of the queried set
                        # Ensure current_round_scores[uid_idx] is used, not score_value if it's from a different loop
                        self.moving_avg_scores[uid_idx] = (
                            (1 - self.alpha) * self.moving_avg_scores[uid_idx] +
                            self.alpha * current_round_scores[uid_idx] 
                        )
                bt.logging.info(f"Validator: Moving Average Scores: {['{:.3f}'.format(s.item()) for s in self.moving_avg_scores]}")

                # === Stage 7: Set Weights ===
                current_block = self.subtensor.get_current_block()
                # Using the line as requested, though it reflects when the neuron (validator or miner) was last updated on-chain,
                # not necessarily when this specific validator last set weights.
                last_set_weights_block = self.metagraph.last_update[self.my_subnet_uid].item() 
                
                tempo = self.subtensor.tempo(self.config.netuid)

                if (current_block - last_set_weights_block) > tempo :
                    if torch.sum(self.moving_avg_scores) > 1e-6:
                        weights_to_set = self.moving_avg_scores / torch.sum(self.moving_avg_scores)
                        weights_to_set = torch.nan_to_num(weights_to_set, nan=0.0)
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
                        wait_for_finalization=False, # Usually False is fine
                        version_key = bt.__version_as_int__
                    )
                    if success:
                        bt.logging.info(f"Validator: Successfully set weights: {message}")
                    else:
                        bt.logging.error(f"Validator: Failed to set weights: {message}")
                else:
                    wait_blocks = tempo - (current_block - last_set_weights_block)
                    bt.logging.info(f"Validator: Not time to set weights. Current: {current_block}, LastUpdate: {last_set_weights_block}, Tempo: {tempo}. Wait: {max(0, wait_blocks)} blocks.")

                # === Stage 8: Sync Metagraph and Wait ===
                self.steps_passed += 1
                if self.steps_passed % 5 == 0: 
                    bt.logging.info("Validator: Syncing metagraph.")
                    self.metagraph.sync(subtensor=self.subtensor)
                    if self.scores.size(0) != self.metagraph.n.item():
                        bt.logging.info("Validator: Metagraph size changed. Reinitializing scores.")
                        self.scores = torch.zeros(self.metagraph.n.item(), dtype=torch.float32)
                        self.moving_avg_scores = torch.zeros(self.metagraph.n.item(), dtype=torch.float32)
                
                await asyncio.sleep(max(60, tempo // 2 if tempo > 0 else 60))

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
        asyncio.run(self.run_sync_loop())

if __name__ == "__main__":
    validator = Validator()
    validator.run()