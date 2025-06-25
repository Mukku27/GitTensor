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

# Import from captionize project structure
from gittensor.base.miner import BaseMinerNeuron
from gittensor.protocol import RadicleGittensorSynapse # Use the new Radicle synapse
from  gittensor.utils import rad_utils # Import new rad_utils

# Load environment variables from .env file
load_dotenv()

class Miner(BaseMinerNeuron):
    def __init__(self, config=None):
        super().__init__(config=config) # BaseMinerNeuron handles wallet, subtensor, metagraph, axon, logging
        
        bt.logging.info("Initializing Radicle GitTensor Miner...")
        
        # Radicle specific config (can be moved to add_args later if more are needed)
        self.radicle_node_alias = self.config.radicle.node.alias # Get from config
        self.radicle_passphrase = os.getenv("RADICLE_PASSPHRASE")
        if not self.radicle_passphrase:
            bt.logging.warning("RADICLE_PASSPHRASE environment variable not set. Radicle operations requiring a passphrase may fail.")
            # self.radicle_passphrase = "<your_radicle_passphrase>" # Fallback to placeholder from old code - NOT RECOMMENDED

        self.radicle_node_process_pexpect = None # For pexpect managed process

        self.setup_radicle_cli_dependencies()
        self.ensure_radicle_miner_auth_and_config()
        self.start_radicle_seed_node()

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
        # No need to add --neuron.name here as BaseNeuron's config() and add_args handle it.
        # It defaults to "miner" or "validator".

    def setup_radicle_cli_dependencies(self):
        bt.logging.info("Miner: Checking Radicle CLI installation...")
        # Reusing run_rad_command from rad_utils
        success, stdout, _ = rad_utils.run_rad_command("--version", suppress_error=True)
        if success:
            bt.logging.info(f"Miner: Radicle CLI found: {stdout}")
        else:
            bt.logging.warning("Miner: Radicle CLI not found. Attempting to install via curl...")
            # The generic run_command from captionize.base.neuron might not exist or might be different.
            # For simplicity, using subprocess directly here for the curl command.
            try:
                install_command = "curl -sSf https://radicle.xyz/install | sh"
                process = subprocess.Popen(install_command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                stdout_install, stderr_install = process.communicate(timeout=180)
                if process.returncode == 0:
                    bt.logging.info("Miner: Radicle CLI installed successfully via curl. Please ensure it's in your PATH or restart the shell/miner.")
                else:
                    bt.logging.error(f"Miner: Failed to install Radicle CLI via curl: {stderr_install}. Please install it manually.")
                    # exit(1) # Consider if this is fatal
            except Exception as e:
                bt.logging.error(f"Miner: Exception during Radicle CLI install: {e}")
                # exit(1)


    def ensure_radicle_miner_auth_and_config(self):
        bt.logging.info(f"Miner: Ensuring Radicle identity '{self.radicle_node_alias}' and configuration...")
        radicle_home = os.path.expanduser("~/.radicle")
        keys_path = os.path.join(radicle_home, "keys") # Default keys dir
        config_path = os.path.join(radicle_home, "config.json")

        # Check for identity
        if not os.path.exists(os.path.join(keys_path, self.radicle_node_alias)): # Check for specific alias key
            bt.logging.info(f"Miner: Radicle key for alias '{self.radicle_node_alias}' not found. Attempting 'rad auth --alias {self.radicle_node_alias}'.")
            # This might require passphrase. Using pexpect for it.
            auth_success, auth_output = rad_utils.pexpect_run_rad_command_with_passphrase(
                f"auth --alias {self.radicle_node_alias} --no-create", # Attempt to select existing or use default
                self.radicle_passphrase
            )
            if not auth_success or "Error" in auth_output:
                 bt.logging.warning(f"Miner: Failed to select alias {self.radicle_node_alias} (may not exist). Output: {auth_output}. Trying to create if necessary via node start.")
                 # Actual creation/unlocking often handled by 'rad node start' if alias is default or configured
            else:
                 bt.logging.info(f"Miner: Radicle alias '{self.radicle_node_alias}' auth/selection output: {auth_output}")
        else:
            bt.logging.info(f"Miner: Radicle key for alias '{self.radicle_node_alias}' found.")

        # Check and create config if missing
        if not os.path.exists(config_path):
            bt.logging.info(f"Miner: Radicle config not found at {config_path}. Creating default seeding configuration.")
            node_config = {
                "node": {
                    "alias": self.radicle_node_alias,
                    "externalAddresses": [self.config.radicle.node.external_address] if self.config.radicle.node.external_address else [],
                    "listen": ["0.0.0.0:8776"],
                    "seedingPolicy": {"default": "allow", "scope": "all"}
                }
            }
            try:
                os.makedirs(radicle_home, exist_ok=True)
                with open(config_path, 'w') as f:
                    json.dump(node_config, f, indent=2)
                bt.logging.info(f"Miner: Created Radicle config at {config_path}.")
            except Exception as e:
                bt.logging.error(f"Miner: Failed to create Radicle config: {e}.")
        else:
            bt.logging.info(f"Miner: Radicle config found at {config_path}.")


    def start_radicle_seed_node(self):
        bt.logging.info("Miner: Attempting to start Radicle seed node...")
        status_ok, stdout_status, _ = rad_utils.run_rad_command("node status", suppress_error=True)
        if status_ok and "running" in stdout_status.lower() and "offline" not in stdout_status.lower():
            bt.logging.info("Miner: Radicle node appears to be already running.")
            return

        bt.logging.info("Miner: Starting Radicle node using pexpect...")
        # Using pexpect_run_rad_command_with_passphrase from rad_utils
        # For `rad node start`, pexpect interaction is more complex than just sending a passphrase.
        # The provided pexpect logic in your old miner.py was:
        # child = pexpect.spawn("rad node start", encoding="utf-8")
        # child.expect("Passphrase:")
        # child.sendline(self.radicle_passphrase)
        # self.radicle_node_process_pexpect = child
        # This doesn't capture the process for long-term management as a daemon.
        # For a persistent node, systemd or running `rad node start` in a screen/tmux session is better.
        # Here, we'll try to start it, but robust backgrounding is outside simple pexpect script.
        
        # For now, let's adapt the pexpect logic from your old miner.py
        # but it won't be stored in self.radicle_node_process_pexpect as that's for daemonized processes.
        # This will just attempt to start it if it's not running.
        try:
            command = "rad node start"
            # This will run in the foreground if not managed by systemd/pm2
            # For a miner script, it's better if `rad node` runs as a separate daemon.
            # This script will *attempt* to start it if not running, but won't manage it as a child process long-term.
            child = pexpect.spawn(command, encoding="utf-8", timeout=30) 
            # Expect passphrase prompt or EOF if already unlocked/no passphrase
            index = child.expect([
                re.compile(r'(?i)passphrase[:\s]*$', re.MULTILINE), 
                "Node already running.", # Check this specific message
                pexpect.EOF, 
                pexpect.TIMEOUT
            ], timeout=20) # Shorter timeout for this initial interaction

            if index == 0: # Passphrase prompt
                bt.logging.info("Miner: Radicle node asking for passphrase. Sending...")
                child.sendline(self.radicle_passphrase)
                # After sending passphrase, it might print more logs and then run.
                # We can't easily capture this as a background process here without more complex pexpect.
                # We assume if passphrase is sent, it will try to start.
                bt.logging.info("Miner: Passphrase sent to Radicle node. It should start if successful. Monitor Radicle logs separately.")
                # Don't assign to self.radicle_node_process_pexpect as it's not managed as a child by this script.
            elif index == 1: # Node already running
                 bt.logging.info("Miner: 'rad node start' reported node is already running.")
            elif index == 2: # EOF - could be good or bad
                output_so_far = child.before + (child.after if isinstance(child.after, str) else "")
                if "error" in output_so_far.lower() or "failed" in output_so_far.lower():
                    bt.logging.error(f"Miner: Radicle node start reached EOF with potential errors: {output_so_far}")
                else:
                    bt.logging.info(f"Miner: Radicle node start reached EOF, assuming it started or was already unlocked. Output: {output_so_far}")
            elif index == 3: # Timeout
                bt.logging.error(f"Miner: Timeout waiting for Radicle node passphrase prompt or start. Output: {child.before}")
            
            # Important: This pexpect instance `child` will terminate when this function returns
            # if it's not daemonized properly outside this script.
            # This function's role is just to *attempt* to start it.

        except pexpect.exceptions.ExceptionPexpect as e:
            bt.logging.error(f"Miner: Pexpect error starting Radicle node: {e}")
        except Exception as e_gen:
            bt.logging.error(f"Miner: Generic error starting Radicle node: {e_gen}")


    async def forward(self, synapse: RadicleGittensorSynapse) -> RadicleGittensorSynapse:
        """
        Miner's forward pass for Radicle GitTensor.
        Currently handles: VALIDATE_PUSH.
        """
        bt.logging.info(f"Miner {self.uid}: Received operation '{synapse.operation_type}' for RID '{synapse.repo_rid or 'N/A'}' from {synapse.dendrite.hotkey}")

        if synapse.operation_type == "VALIDATE_PUSH":
            if not synapse.repo_rid:
                synapse.status_message = "FAILURE: repo_rid not provided for VALIDATE_PUSH"
                synapse.validation_passed = False
                bt.logging.warning(f"Miner {self.uid}: {synapse.status_message}")
                return synapse

            bt.logging.info(f"Miner {self.uid}: Validating push for RID: {synapse.repo_rid} (Commit: {synapse.commit_hash or 'N/A'})")
            
            # Miner attempts to seed the repository. This implicitly validates its availability.
            # `rad seed <rid>` or `rad track <rid> --seed`
            seed_success, stdout_seed, stderr_seed = rad_utils.run_rad_command(f"seed {synapse.repo_rid}")
            
            # Give Radicle network some time to propagate/process
            await asyncio.sleep(5) # Using asyncio.sleep as forward is async

            # Verify if the miner is now seeding it
            # `rad ls --seeded` lists RIDs the node is actively seeding.
            verify_seed_success, stdout_ls, stderr_ls = rad_utils.run_rad_command("ls --seeded")

            if verify_seed_success and synapse.repo_rid in stdout_ls:
                synapse.validation_passed = True
                synapse.status_message = "SUCCESS: Repository seeded."
                bt.logging.info(f"Miner {self.uid}: Successfully seeded RID {synapse.repo_rid}.")
            else:
                synapse.validation_passed = False
                error_details = f"Seed cmd success: {seed_success}, out: '{stdout_seed}', err: '{stderr_seed}'. " \
                                f"Verify seed (ls) success: {verify_seed_success}, out: '{stdout_ls}', err: '{stderr_ls}'."
                synapse.status_message = f"FAILURE: Could not confirm seeding of RID {synapse.repo_rid}."
                synapse.error_message = error_details
                bt.logging.warning(f"Miner {self.uid}: {synapse.status_message} Details: {error_details}")
            return synapse
        
        # Handle other operation types in the future
        else:
            synapse.status_message = f"FAILURE: Unknown operation_type '{synapse.operation_type}'"
            synapse.validation_passed = False # Default for safety
            bt.logging.warning(f"Miner {self.uid}: {synapse.status_message}")
            return synapse

    async def blacklist(self, synapse: RadicleGittensorSynapse) -> Tuple[bool, str]:
        """
        Blacklist logic for Radicle GitTensor Miner.
        You can customize this based on your subnet's needs.
        """
        # Use the blacklist logic from BaseMinerNeuron or customize.
        # For now, let's use a simple hotkey check.
        if synapse.dendrite.hotkey not in self.metagraph.hotkeys:
            bt.logging.trace(f"Miner {self.uid}: Blacklisting unrecognized hotkey {synapse.dendrite.hotkey}")
            return True, "Unrecognized hotkey"
        
        # Example: Blacklist if stake is too low (adjust threshold as needed)
        # validator_uid = self.metagraph.hotkeys.index(synapse.dendrite.hotkey)
        # if self.metagraph.S[validator_uid] < 1: # Min stake of 1 Tao for example
        #     bt.logging.trace(f"Miner {self.uid}: Blacklisting {synapse.dendrite.hotkey} due to low stake: {self.metagraph.S[validator_uid]}")
        #     return True, "Low stake"

        bt.logging.trace(f"Miner {self.uid}: Not blacklisting recognized hotkey {synapse.dendrite.hotkey}")
        return False, "Allowed"

    async def priority(self, synapse: RadicleGittensorSynapse) -> float:
        """
        Priority logic for Radicle GitTensor Miner.
        Validators with higher stake might get higher priority.
        """
        if synapse.dendrite.hotkey not in self.metagraph.hotkeys:
            return 0.0 # Should be caught by blacklist

        caller_uid = self.metagraph.hotkeys.index(synapse.dendrite.hotkey)
        priority_value = float(self.metagraph.S[caller_uid])
        bt.logging.trace(f"Miner {self.uid}: Priority for {synapse.dendrite.hotkey} (UID {caller_uid}): {priority_value}")
        return priority_value

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