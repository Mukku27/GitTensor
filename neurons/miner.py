# neurons/miner.py
import os
import time
import traceback
import bittensor as bt
from dotenv import load_dotenv
import subprocess
import re
import asyncio
from typing import Tuple, List, Optional
import json
import pexpect
import shlex

# Import from captionize project structure
from gittensor.base.miner import BaseMinerNeuron
from gittensor.protocol import RadicleSubnetSynapse # Use the new Radicle synapse


# Load environment variables from .env file
load_dotenv()

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


class Miner(BaseMinerNeuron):
    def __init__(self, config=None):
        super().__init__(config=config) # BaseMinerNeuron handles wallet, subtensor, metagraph, axon, logging
        
        bt.logging.info("Initializing Radicle GitTensor Miner...")
        
        self.radicle_node_process = None
        self.setup_radicle_dependencies() # Check/install Radicle
        self.ensure_radicle_auth_and_config() # Ensure miner identity and config
        
        self.start_radicle_node() 
        bt.logging.info(f"Radicle GitTensor Miner Initialized. Alias: {self.radicle_node_alias}")

    @classmethod
    def add_args(cls, parser):
        super().add_args(parser) # Add base neuron args
        # Add Radicle specific arguments
        parser.add_argument(
            "--radicle.node.alias",
            type=str,
            default="gittensor-miner-seed", # Default alias for this subnet's miner
            help="Alias for the Radicle node identity for this miner.",
        )
        parser.add_argument(
            "--radicle.node.external_address",
            type=str,
            default=None, 
            help="Publicly reachable address for the Radicle node (domain:port or ip:port). If None, Radicle tries to use local discovery.",
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


    async def forward(self, synapse: RadicleSubnetSynapse) -> RadicleSubnetSynapse:
        """
        Miner's forward pass for Radicle GitTensor.
        Currently handles: VALIDATE_PUSH.
        """
        bt.logging.info(f"Miner {self.uid}: Received operation '{synapse.operation_type}' for RID '{synapse.repo_rid or 'N/A'}' from {synapse.dendrite.hotkey}")

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


    async def blacklist(self, synapse: RadicleSubnetSynapse) -> Tuple[bool, str]:
        """
        Blacklist logic for Radicle GitTensor Miner.
        You can customize this based on your subnet's needs.
        """
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

    
    async def priority(self, synapse: RadicleSubnetSynapse) -> float:
        """
        Priority logic for Radicle GitTensor Miner.
        Validators with higher stake might get higher priority.
        """
        # Prioritize validators with higher stake.
        caller_uid = self.metagraph.hotkeys.index(synapse.dendrite.hotkey)
        priority = float(self.metagraph.S[caller_uid])
        bt.logging.trace(f"Priority for {synapse.dendrite.hotkey}: {priority}")
        return priority

    # The run, run_in_background, stop_run_thread, __enter__, __exit__
    # are handled by BaseMinerNeuron.
    # set_weights and resync_metagraph are also in BaseMinerNeuron.

# Entry point
if __name__ == "__main__":
    with Miner() as miner:
        bt.logging.info("Radicle GitTensor Miner running...")
        # BaseMinerNeuron's run method handles the main loop.
        # We just need to keep the main thread alive.
        while True:
            time.sleep(60)