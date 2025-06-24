import os
import shlex
import subprocess
import time
import pexpect 
import json
import traceback # Added for detailed error logging
from typing import Tuple, Optional, Any # Added Any for config type flexibility

class RadicleUtils:
    def __init__(self, config: Any, logging: Any): # Using Any for bittensor objects for simplicity
        self.config = config
        self.logging = logging

    def run_rad_command(self, command: str, suppress_error: bool = False, cwd: Optional[str] = None) -> Tuple[bool, str, str]:
        try:
            self.logging.debug(f"Running command: {command} (cwd: {cwd or os.getcwd()})")
            process = subprocess.Popen(shlex.split(command), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=cwd)
            stdout, stderr = process.communicate(timeout=120) 
            success = process.returncode == 0
            if not success and not suppress_error:
                self.logging.error(f"Command failed: {command}\nReturn Code: {process.returncode}\nStderr: {stderr.strip()}\nStdout: {stdout.strip()}")
            elif success and suppress_error: # Log success if suppress_error is True, for debugging
                self.logging.trace(f"Command success (suppressed error): {command}\nStdout: {stdout.strip()}")
            return success, stdout.strip(), stderr.strip()
        except subprocess.TimeoutExpired:
            self.logging.error(f"Command timed out: {command}")
            if 'process' in locals() and process.poll() is None: process.kill()
            return False, "", "Timeout expired"
        except Exception as e:
            self.logging.error(f"Error running command {command}: {e}\n{traceback.format_exc()}")
            return False, "", str(e)

    def setup_radicle_dependencies(self):
        self.logging.info("Checking Radicle CLI installation...")
        success, stdout, _ = self.run_rad_command("rad --version", suppress_error=True)
        if success:
            self.logging.info(f"Radicle CLI found: {stdout}")
        else:
            self.logging.warning("Radicle CLI not found or 'rad --version' failed. Attempting to install (experimental)...")
            # This installation is very basic and might require sudo or manual intervention.
            # Only suitable for controlled environments.
            install_cmd = "curl -sSf https://radicle.xyz/install | sh"
            self.logging.info(f"Running installation command: {install_cmd}")
            # Running this via run_rad_command might be problematic due to shell piping.
            # Prefer manual installation or a more robust setup script.
            try:
                process = subprocess.run(install_cmd, shell=True, check=True, capture_output=True, text=True, timeout=300)
                self.logging.info(f"Radicle install script stdout: {process.stdout}")
                self.logging.info("Radicle CLI installation script completed. Ensure Radicle binaries are in your PATH or restart your shell/environment.")
            except subprocess.CalledProcessError as e:
                self.logging.error(f"Failed to install Radicle CLI via script: {e.stderr}. Please install it manually.")
            except subprocess.TimeoutExpired:
                 self.logging.error("Radicle installation script timed out. Please install manually.")
            except Exception as e_gen:
                self.logging.error(f"General error during Radicle installation script: {e_gen}. Please install manually.")


    def ensure_radicle_auth_and_config(self, is_miner: bool = False):
        self.logging.info("Ensuring Radicle identity and configuration...")
        radicle_home = os.path.expanduser("~/.radicle")
        keys_path = os.path.join(radicle_home, "keys")
        
        alias_to_check = ""
        if is_miner:
            alias_to_check = self.config.radicle.node.alias
        else: 
            alias_to_check = self.config.radicle.validator.alias
        
        if not alias_to_check:
             alias_to_check = "default-gittensor-identity"
             self.logging.warning(f"Radicle alias not found in config, using fallback: {alias_to_check}")

        # Check if rad auth <alias> is already configured
        auth_check_success, auth_check_stdout, _ = self.run_rad_command(f"rad auth {alias_to_check} --check", suppress_error=True)

        if not auth_check_success or not (os.path.exists(keys_path) and alias_to_check in auth_check_stdout):
            self.logging.info(f"Radicle identity for alias '{alias_to_check}' not found or not active. Attempting 'rad auth --alias {alias_to_check}'.")
            # `rad auth` can be interactive if a new key is created and needs a passphrase.
            # This is a known limitation. For full automation, pre-configure or use pexpect if it prompts.
            # Using pexpect for `rad auth` is more complex than `rad node start`.
            # For now, assume it's pre-configured or works non-interactively for the selected alias.
            passphrase = self.config.radicle.get("passphrase", "<YOUR_RADICAL_PASSPHRASE>")
            
            # Simple attempt, may require pexpect for passphrase entry on new key
            rad_auth_cmd = f"rad auth --alias {alias_to_check}"
            if passphrase and passphrase != "<YOUR_RADICAL_PASSPHRASE>": # Only use --stdin if passphrase is set
                 rad_auth_cmd += " --stdin" # Attempt to provide passphrase via stdin

            self.logging.info(f"Running: {rad_auth_cmd} (passphrase will be piped if specified and --stdin used)")
            try:
                if "--stdin" in rad_auth_cmd:
                    process = subprocess.Popen(shlex.split(rad_auth_cmd), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    stdout, stderr = process.communicate(input=passphrase + "\n" + passphrase + "\n", timeout=60) # Send twice for new key
                    success = process.returncode == 0
                else: # Run without stdin if no passphrase or not using --stdin
                    success, stdout, stderr = self.run_rad_command(rad_auth_cmd)

                if not success:
                    self.logging.error(f"Failed to authenticate Radicle identity '{alias_to_check}': {stderr}. Stdout: {stdout}. Please run 'rad auth' manually or ensure pre-configuration.")
                else:
                    self.logging.info(f"Radicle identity authenticated/selected for alias '{alias_to_check}': {stdout}")
            except subprocess.TimeoutExpired:
                self.logging.error(f"Timeout during 'rad auth' for alias '{alias_to_check}'. Manual intervention likely needed.")
            except Exception as e_auth:
                self.logging.error(f"Exception during 'rad auth' for alias '{alias_to_check}': {e_auth}. Manual intervention likely needed.")
        else:
            self.logging.info(f"Radicle identity for alias '{alias_to_check}' seems to be configured and active.")


        if is_miner:
            config_path = os.path.join(radicle_home, "config.json")
            if not os.path.exists(config_path):
                self.logging.info(f"Miner Radicle config not found at {config_path}. Creating default.")
                node_config_dict = {
                    "node": {
                        "alias": self.config.radicle.node.alias,
                        "externalAddresses": [self.config.radicle.node.external_address] if self.config.radicle.node.external_address else [],
                        "listen": ["0.0.0.0:8776"], # Default Radicle port
                        "seedingPolicy": {"default": "allow", "scope": "all"}
                    }
                }
                try:
                    os.makedirs(radicle_home, exist_ok=True)
                    with open(config_path, 'w') as f: json.dump(node_config_dict, f, indent=2)
                    self.logging.info(f"Created Radicle config at {config_path} for miner.")
                    if not self.config.radicle.node.external_address:
                        self.logging.warning("Miner's radicle.node.external_address is not set. Node might be local only and not reachable by external validators.")
                except Exception as e:
                    self.logging.error(f"Failed to create miner Radicle config: {e}. Please create manually or check permissions.")
            else:
                self.logging.info(f"Miner Radicle config found at {config_path}.")
    
    def start_radicle_node_with_pexpect(self, command: str, passphrase: str) -> Optional[pexpect.spawn]:
        try:
            self.logging.debug(f"Pexpect: Spawning command: {command}")
            # Ensure LANG is set for UTF-8, common pexpect issue
            env = os.environ.copy()
            env['LANG'] = 'en_US.UTF-8' 
            child = pexpect.spawn(command, encoding="utf-8", timeout=30, env=env)
            
            # More robust pattern matching for passphrase or other outputs
            # Common patterns: "Passphrase:", "Node already running", "Error:", EOF, TIMEOUT
            # Order matters.
            patterns = [
                r"(?i)passphrase.*:",       # 0: Passphrase prompt (case-insensitive)
                r"Node already running",   # 1: Node already running
                r"Node RID: rad:",         # 2: Node started successfully (example, adjust to actual success message)
                r"HTTP API listening on",  # 3: Another success indicator
                pexpect.EOF,               # 4: End of file
                pexpect.TIMEOUT,           # 5: Timeout
                r"(?i)error[:\s]",         # 6: Error message (case-insensitive)
            ]
            index = child.expect(patterns, timeout=60) # Increased timeout for expect

            if index == 0: 
                self.logging.debug("Pexpect: Passphrase prompt detected. Sending passphrase.")
                child.sendline(passphrase)
                # Expect a success message or EOF after sending passphrase
                # This needs to be tailored to the actual output of `rad node start` after passphrase
                try:
                    # Look for positive confirmation or EOF if it backgrounds
                    success_patterns_after_pass = [
                        r"Node RID: rad:", 
                        r"HTTP API listening on",
                        pexpect.EOF # If it backgrounds and exits parent quickly
                    ]
                    # Or a more generic success pattern for your Radicle version
                    # For example, if it prints "Node started" or similar.
                    idx_after_pass = child.expect(success_patterns_after_pass, timeout=60)
                    self.logging.info(f"Pexpect: Radicle node started with passphrase (or backgrounded). Index: {idx_after_pass}, Output: {child.before}{child.buffer}")
                    return child # Return child, caller can check isalive() or manage it
                except pexpect.exceptions.TIMEOUT:
                    self.logging.error(f"Pexpect: Timeout after sending passphrase. Node start uncertain. Output: {child.before}{child.buffer}")
                    return None
                except pexpect.exceptions.EOF: # Can be normal if it daemonizes
                    self.logging.info(f"Pexpect: EOF after sending passphrase. Node likely daemonized. Output: {child.before}{child.buffer}")
                    # If it daemonizes, child.isalive() might be False but node is running.
                    # Best to confirm with `rad node status` externally after this.
                    return child # Return child; it might be dead if it fully exited, or alive if still attached

            elif index == 1: 
                 self.logging.info(f"Pexpect: Radicle node reported as already running. Output: {child.before}{child.buffer}")
                 return None 
            elif index == 2 or index == 3:
                self.logging.info(f"Pexpect: Radicle node started successfully without passphrase prompt (or already unlocked). Output: {child.before}{child.buffer}")
                return child
            elif index == 4: # EOF
                 self.logging.warning(f"Pexpect: Reached EOF unexpectedly. Node start likely failed or command exited. Output: {child.before}{child.buffer}")
                 return None
            elif index == 5: # Timeout
                self.logging.error(f"Pexpect: Timeout waiting for Radicle node prompt or start confirmation. Output before timeout: {child.before}{child.buffer}")
                return None
            elif index == 6: # Error
                self.logging.error(f"Pexpect: Radicle node start reported an error. Output: {child.before}{child.buffer}")
                return None
            
        except pexpect.exceptions.ExceptionPexpect as e:
            self.logging.error(f"Pexpect: Error starting Radicle node: {e}. Output: {child.before if 'child' in locals() and hasattr(child, 'before') else 'N/A'}")
            return None
        except Exception as e_gen:
            self.logging.error(f"Pexpect: Generic error starting Radicle node: {e_gen}\n{traceback.format_exc()}")
            return None
        return None