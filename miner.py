import os
import time
import argparse
import traceback
import bittensor as bt
import subprocess
import json
import shlex
from typing import Tuple, Optional, Dict, List
import pexpect

from protocol import RadicleSubnetSynapse

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

class Miner:
    def __init__(self):
        self.config = self.get_config()
        self.setup_logging()
        self.radicle_node_process = None
        self.setup_radicle_dependencies() # Check/install Radicle
        self.ensure_radicle_auth_and_config() # Ensure miner identity and config
        self.setup_bittensor_objects()
        self.start_radicle_node() # Start Radicle seed node

    def get_config(self):
        parser = argparse.ArgumentParser()
        parser.add_argument(
            "--radicle.node.alias",
            default="bittensor-miner-seed",
            help="Alias for the Radicle node identity for this miner.",
        )
        parser.add_argument(
            "--radicle.node.external_address",
            default=None, # e.g., "your.domain.com:8776" or "public_ip:8776"
            help="Publicly reachable address for the Radicle node (domain:port or ip:port). If None, tries to use local.",
        )
        parser.add_argument(
            "--netuid", type=int, default=1, help="The chain subnet uid."
        )
        bt.subtensor.add_args(parser)
        bt.logging.add_args(parser)
        bt.wallet.add_args(parser)
        bt.axon.add_args(parser)
        config = bt.config(parser)
        config.full_path = os.path.expanduser(
            "{}/{}/{}/netuid{}/{}".format(
                config.logging.logging_dir,
                config.wallet.name,
                config.wallet.hotkey_str,
                config.netuid,
                "miner",
            )
        )
        os.makedirs(config.full_path, exist_ok=True)
        return config

    def setup_logging(self):
        bt.logging(config=self.config, logging_dir=self.config.full_path)
        bt.logging.info(f"Running miner for subnet: {self.config.netuid} on network: {self.config.subtensor.network} with config:")
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
                bt.logging.info("Radicle CLI installed successfully. Please ensure it's in your PATH or restart the shell/miner.")
                # It's hard to make it available in the current process PATH immediately after install.
                # A restart of the miner or manual PATH adjustment might be needed.
            else:
                bt.logging.error(f"Failed to install Radicle CLI: {stderr}. Please install it manually.")
                exit(1)

    def ensure_radicle_auth_and_config(self):
        bt.logging.info("Ensuring Radicle identity and configuration...")
        radicle_home = os.path.expanduser("~/.radicle")
        keys_path = os.path.join(radicle_home, "keys")
        config_path = os.path.join(radicle_home, "config.json")

        if not os.path.exists(keys_path):
            bt.logging.info(f"Radicle keys not found at {keys_path}. Attempting to authenticate as '{self.config.radicle.node.alias}'.")
            success, stdout, stderr = run_command(f"rad auth --alias {self.config.radicle.node.alias}")
            if not success:
                bt.logging.error(f"Failed to authenticate Radicle identity: {stderr}. Please run 'rad auth --alias {self.config.radicle.node.alias}' manually.")
                exit(1)
            bt.logging.info(f"Radicle identity created: {stdout}")
        else:
             bt.logging.info(f"Radicle keys found at {keys_path}.")


        if not os.path.exists(config_path):
            bt.logging.info(f"Radicle config not found at {config_path}. Creating default seeding configuration.")
            node_config = {
                "node": {
                    "alias": self.config.radicle.node.alias,
                    "externalAddresses": [self.config.radicle.node.external_address] if self.config.radicle.node.external_address else [],
                    "listen": ["0.0.0.0:8776"], # Listen on all interfaces
                    "seedingPolicy": {"default": "allow", "scope": "all"},
                    "scope": "all",
                    "policy": "allow"
                }
            }
            try:
                os.makedirs(radicle_home, exist_ok=True)
                with open(config_path, 'w') as f:
                    json.dump(node_config, f, indent=2)
                bt.logging.info(f"Created Radicle config at {config_path} with open seeding policy.")
                if not self.config.radicle.node.external_address:
                    bt.logging.warning("radicle.node.external_address is not set. The Radicle node might only be accessible locally.")
            except Exception as e:
                bt.logging.error(f"Failed to create Radicle config: {e}. Please create it manually at {config_path}.")
                exit(1)
        else:
            bt.logging.info(f"Radicle config found at {config_path}.")
            # Optionally, verify and update existing config here if needed

    def start_radicle_node(self):
        bt.logging.info("Attempting to start Radicle seed node...")
        # Check if already running (simple check, could be more robust)
        _, stdout, _ = run_command("rad node status", suppress_error=True)
        if "running" in stdout.lower() and "offline" not in stdout.lower() :
             bt.logging.info("Radicle node appears to be already running.")
             # Consider how to manage this if it was started outside this script
             return

        try:
            # Using Popen for non-blocking start. For production, systemd is better.
            command = "rad node start"
            child = pexpect.spawn(command, encoding="utf-8")
            child.expect("Passphrase:")
            child.sendline("<your_radicle_passphrase>")  # Replace with your actual passphrase or handle securely
            bt.logging.info("Radicle node started with provided passphrase.")
            self.radicle_node_process = child
            bt.logging.info(f"Radicle node process started. Monitoring output... {child.pid}")
            time.sleep(5) # Give it a few seconds to start up
        except pexpect.exceptions.TIMEOUT as e:
            bt.logging.error(f"Timeout while starting Radicle node: {e}")
            self.radicle_node_process = None
        except pexpect.exceptions.EOF as e:
            bt.logging.error(f"Radicle node exited unexpectedly: {e}")
            self.radicle_node_process = None
        except Exception as e:
            bt.logging.error(f"Failed to start Radicle node: {e}")
            self.radicle_node_process = None

    def _log_radicle_node_output(self):
        if self.radicle_node_process and self.radicle_node_process.stdout:
            try:
                for line in iter(self.radicle_node_process.stdout.readline, ''):
                    if not line: break # End of stream
                    bt.logging.debug(f"[RadicleNode STDOUT] {line.strip()}")
                for line in iter(self.radicle_node_process.stderr.readline, ''):
                    if not line: break
                    bt.logging.warning(f"[RadicleNode STDERR] {line.strip()}")
            except Exception: # Handle if stream is closed or other errors
                pass


    def setup_bittensor_objects(self):
        bt.logging.info("Setting up Bittensor objects.")
        self.wallet = bt.wallet(config=self.config)
        bt.logging.info(f"Wallet: {self.wallet}")
        self.subtensor = bt.subtensor(config=self.config)
        bt.logging.info(f"Subtensor: {self.subtensor}")
        self.metagraph = self.subtensor.metagraph(self.config.netuid)
        bt.logging.info(f"Metagraph: {self.metagraph}")

        if self.wallet.hotkey.ss58_address not in self.metagraph.hotkeys:
            bt.logging.error(f"Your miner: {self.wallet} is not registered to chain connection: {self.subtensor}. Run 'btcli s register --netuid {self.config.netuid}' and try again.")
            exit()
        self.my_subnet_uid = self.metagraph.hotkeys.index(self.wallet.hotkey.ss58_address)
        bt.logging.info(f"Running miner on uid: {self.my_subnet_uid}")

    def blacklist_fn(self, synapse: RadicleSubnetSynapse) -> Tuple[bool, str]:
        if synapse.dendrite.hotkey not in self.metagraph.hotkeys:
            bt.logging.trace(f"Blacklisting unrecognized hotkey {synapse.dendrite.hotkey}")
            return True, "Unrecognized hotkey"
        
        # Additional blacklist logic can be added here (e.g., based on stake, trust, etc.)
        requester_uid = self.metagraph.hotkeys.index(synapse.dendrite.hotkey)
        if self.metagraph.S[requester_uid] < 1 : # Example: min stake of 1000 TAO for validators, for testing purposes we use 1
             bt.logging.trace(f"Blacklisting hotkey {synapse.dendrite.hotkey} due to low stake: {self.metagraph.S[requester_uid]}")
             return True, "Low stake"

        bt.logging.trace(f"Not blacklisting recognized hotkey {synapse.dendrite.hotkey}")
        return False, "Allowed"

    def priority_fn(self, synapse: RadicleSubnetSynapse) -> float:
        # Prioritize validators with higher stake.
        caller_uid = self.metagraph.hotkeys.index(synapse.dendrite.hotkey)
        priority = float(self.metagraph.S[caller_uid])
        bt.logging.trace(f"Priority for {synapse.dendrite.hotkey}: {priority}")
        return priority

    def forward_radicle_operation(self, synapse: RadicleSubnetSynapse) -> RadicleSubnetSynapse:
        bt.logging.info(f"Received operation: {synapse.operation_type} from {synapse.dendrite.hotkey}")

        if synapse.operation_type == "VALIDATE_PUSH":
            if not synapse.repo_rid:
                synapse.status_message = "FAILURE"
                synapse.error_message = "repo_rid not provided for VALIDATE_PUSH"
                synapse.validation_passed = False
                return synapse

            # Attempt to track/seed the RID to confirm it's on the network and accessible
            # `rad seed <rid>` ensures it's seeded. `rad track <rid>` just follows.
            # For validation, ensuring it's seeded by this miner is a good check.
            bt.logging.info(f"Validator requests push validation for RID: {synapse.repo_rid}")
            success, stdout, stderr = run_command(f"rad seed {synapse.repo_rid}")
            # `rad seed` might not give immediate feedback if already seeded.
            # A better check might be `rad inspect <rid>` or checking `rad seed list`
            
            # Let's check if it's in the seed list
            time.sleep(2) # Give some time for seeding to potentially propagate
            list_success, list_stdout, list_stderr = run_command("rad ls --seeded")
            if list_success and synapse.repo_rid in list_stdout:
                bt.logging.info(f"Successfully verified and seeding RID: {synapse.repo_rid}")
                synapse.status_message = "SUCCESS"
                synapse.validation_passed = True
            else:
                bt.logging.warning(f"Failed to confirm seeding for RID: {synapse.repo_rid}. Seed cmd success: {success}, stdout: {stdout}, stderr: {stderr}. List cmd success: {list_success}, list_stdout: {list_stdout}, list_stderr: {list_stderr}")
                synapse.status_message = "FAILURE"
                synapse.validation_passed = False
                synapse.error_message = f"Could not confirm seeding of RID {synapse.repo_rid}. Radicle seed output: {stderr}. Radicle list output: {list_stderr}"

        elif synapse.operation_type == "GET_MINER_STATUS":
            bt.logging.info("Validator requests miner status.")
            status_success, status_stdout, status_stderr = run_command("rad node status")
            alias_success, alias_stdout, _ = run_command("rad self --alias", suppress_error=True)
            id_success, id_stdout, _ = run_command("rad self --nid", suppress_error=True)
            
            synapse.is_miner_radicle_node_running = status_success and "running" in status_stdout.lower() and "offline" not in status_stdout.lower()
            synapse.miner_radicle_node_alias = alias_stdout if alias_success else "N/A"
            synapse.miner_radicle_node_id = id_stdout if id_success else "N/A"

            if synapse.is_miner_radicle_node_running:
                list_success, list_stdout, _ = run_command("rad ls --seeded")
                if list_success:
                    # Count non-empty lines, as each line is an RID
                    seeded_rids = [line for line in list_stdout.splitlines() if line.strip().startswith("rad:") and len(line.strip()) > 10]
                    synapse.seeded_rids_count = len(seeded_rids)
                else:
                    synapse.seeded_rids_count = 0
                synapse.status_message = "SUCCESS"
            else:
                synapse.seeded_rids_count = 0
                synapse.status_message = "FAILURE"
                synapse.error_message = f"Radicle node not running or status check failed. Output: {status_stdout} {status_stderr}"
                
        elif synapse.operation_type == "VALIDATE_CHANGES_SYNC":
            bt.logging.info(f"Validator requests changes sync validation for RID: {synapse.repo_sync_rid}")
            if not synapse.repo_sync_rid:
                synapse.status_message = "FAILURE"
                synapse.error_message = "repo_rid not provided for VALIDATE_CHANGES_SYNC"
                synapse.changes_synced_successfully = False
                return synapse

            bt.logging.info(f"Miner: VALIDATE_CHANGES_SYNC request for RID: {synapse.repo_sync_rid}")
            rad_path = run_command("rad path")[1].strip()
            # Miner attempts to sync the repository
            # `rad sync <RID>` should fetch the latest changes pushed by the validator
            bt.logging.info(f"Miner: Attempting to sync changes for RID {synapse.repo_sync_rid} in directory {rad_path}/storage/{synapse.repo_sync_rid.split(':')[1]}/")
            # Ensure the directory exists before syncing
            sync_success, stdout_sync, stderr_sync = run_command(f"rad sync {synapse.repo_sync_rid} --fetch")
            bt.logging.info(f"suucess {sync_success}, output {stdout_sync} errrr {stderr_sync}")

            
            if sync_success:
                # Check if "✓ Synced" or similar success message is in stdout
                # Radicle's `rad sync` output can vary, be specific if possible.
                # A simple check for "✓ Synced" or "up to date" can work.
                if "✓ Synced" in stdout_sync or "up to date" in stdout_sync.lower() or "nothing to sync" in stdout_sync.lower():
                    bt.logging.info(f"Miner: Successfully synced changes for RID {synapse.repo_sync_rid}. Output: {stdout_sync}")
                    synapse.changes_synced_successfully = True
                    synapse.status_message = "SUCCESS"
                else:
                    # Sync command ran, but output doesn't confirm sync.
                    bt.logging.warning(f"Miner: 'rad sync {synapse.repo_sync_rid}' ran, but success message not found in output. Stdout: {stdout_sync}, Stderr: {stderr_sync}")
                    synapse.changes_synced_successfully = False
                    synapse.status_message = "FAILURE"
                    synapse.error_message = f"Sync command output did not confirm sync success. Output: {stdout_sync}"
            else:
                bt.logging.warning(f"Miner: Failed to execute 'rad sync {synapse.repo_sync_rid}'. Stderr: {stderr_sync}, Stdout: {stdout_sync}")
                synapse.changes_synced_successfully = False
                synapse.status_message = "FAILURE"
                synapse.error_message = f"rad sync command failed: {stderr_sync or stdout_sync}"

        elif synapse.operation_type == "VALIDATE_BRANCH_SYNC":
            
            rid_to_sync_branch = synapse.branch_sync_repo_id 
            bt.logging.info(f"Miner: VALIDATE_BRANCH_SYNC request for RID: {rid_to_sync_branch}")

            if not rid_to_sync_branch:
                synapse.status_message = "FAILURE"
                synapse.error_message = "branch_sync_repo_id not provided for VALIDATE_BRANCH_SYNC"
                synapse.branch_changes_synced_successfully = False
                return synapse

            sync_success, stdout_sync, stderr_sync = run_command(f"rad sync {rid_to_sync_branch} --fetch")
            
            if sync_success and ("✓ Synced" in stdout_sync or "up to date" in stdout_sync.lower() or "nothing to sync" in stdout_sync.lower()):
                bt.logging.info(f"Miner: Successfully synced (including branches) for RID {rid_to_sync_branch}. Output: {stdout_sync}")
                synapse.branch_changes_synced_successfully = True
                synapse.status_message = "SUCCESS"
            else:
                bt.logging.warning(f"Miner: 'rad sync {rid_to_sync_branch}' (for branch) failed or success message not found. Stdout: {stdout_sync}, Stderr: {stderr_sync}")
                synapse.branch_changes_synced_successfully = False
                synapse.status_message = "FAILURE"
                synapse.error_message = f"Branch sync command output did not confirm success. Output: {stdout_sync}"
            return synapse
        elif synapse.operation_type == "UNSEED_REPO":
            if not synapse.repo_rid:
                synapse.status_message = "FAILURE"
                synapse.error_message = "repo_rid not provided for UNSEED_REPO"
                synapse.unseed_command_successful = False
                return synapse

            bt.logging.info(f"Miner: Received UNSEED_REPO request for RID: {synapse.repo_rid} from {synapse.dendrite.hotkey}")
            
            unseed_success, stdout, stderr = run_command(f"rad unseed {synapse.repo_rid}")
            
            if unseed_success:
                bt.logging.info(f"Miner: Successfully executed 'rad unseed {synapse.repo_rid}'. Output: {stdout}")
                rad_path = run_command("rad path")[1].strip()
                dlt_success, dlt_stdout, dlt_stderr = run_command(f"rm -rf {rad_path}/storage/{synapse.repo_rid.split(':')[1]}")
                bt.logging.info(f"Miner: Successfully deleted local Radicle directory for {synapse.repo_rid}. Output: {dlt_stdout}, Error: {dlt_stderr}, Success: {dlt_success}")
                synapse.unseed_command_successful = True
                synapse.status_message = "SUCCESS"
            else:
                bt.logging.warning(f"Miner: Failed to execute 'rad unseed {synapse.repo_rid}'. Stderr: {stderr}, Stdout: {stdout}")
                synapse.unseed_command_successful = False
                synapse.status_message = "FAILURE"
                synapse.error_message = f"rad unseed command failed: {stderr or stdout}"
            return synapse

        else:
            synapse.status_message = "FAILURE"
            synapse.error_message = f"Unknown operation_type: {synapse.operation_type}"

        bt.logging.info(f"Responding to {synapse.dendrite.hotkey}: {synapse.status_message}, Validation: {synapse.validation_passed}, Error: {synapse.error_message}")
        return synapse

    def setup_axon(self):
        self.axon = bt.axon(wallet=self.wallet, config=self.config)
        bt.logging.info("Attaching forward function to axon.")
        self.axon.attach(
            forward_fn=self.forward_radicle_operation,
            blacklist_fn=self.blacklist_fn,
            priority_fn=self.priority_fn
        )
        bt.logging.info(f"Serving axon on network: {self.config.subtensor.network} with netuid: {self.config.netuid}")
        self.axon.serve(netuid=self.config.netuid, subtensor=self.subtensor)
        bt.logging.info(f"Axon: {self.axon}")
        bt.logging.info(f"Starting axon server on port: {self.config.axon.port}")
        self.axon.start()

    def run(self):
        self.setup_axon()
        bt.logging.info(f"Miner started. UID: {self.my_subnet_uid}. Radicle Node Alias: {self.config.radicle.node.alias}")
        step = 0
        try:
            while True:
                # Log Radicle node output periodically if it was started by this script
                if step % 30 == 0 and self.radicle_node_process:
                     self._log_radicle_node_output()
                     # Check if radicle_node_process is still alive
                     if self.radicle_node_process.pid is None:
                         bt.logging.error(f"Radicle node process terminated unexpectedly with code {self.radicle_node_process.codec_errors}. Restarting...")
                         self.start_radicle_node() # Attempt to restart

                if step % 60 == 0:
                    self.metagraph.sync(subtensor=self.subtensor) # Sync metagraph
                    log_str = (
                        f"Step:{step} | Block:{self.metagraph.block.item()} | "
                        f"Stake:{self.metagraph.S[self.my_subnet_uid]} | "
                        f"Trust:{self.metagraph.T[self.my_subnet_uid]} | "
                        f"Incentive:{self.metagraph.I[self.my_subnet_uid]} | "
                        f"Emission:{self.metagraph.E[self.my_subnet_uid]}"
                    )
                    bt.logging.info(log_str)
                step += 1
                time.sleep(1)

        except KeyboardInterrupt:
            self.axon.stop()
            if self.radicle_node_process:
                bt.logging.info("Stopping Radicle node process...")
                self.radicle_node_process.terminate()
                try:
                    self.radicle_node_process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.radicle_node_process.kill()
                bt.logging.info("Radicle node process stopped.")
            bt.logging.success("Miner killed by keyboard interrupt.")
        except Exception:
            self.radicle_node_process.terminate()
            bt.logging.error(traceback.format_exc())
        finally:
            if self.axon:
                self.axon.stop()
            if self.radicle_node_process and self.radicle_node_process.pid is None:
                self.radicle_node_process.kill()


if __name__ == "__main__":
    miner = Miner()
    miner.run()