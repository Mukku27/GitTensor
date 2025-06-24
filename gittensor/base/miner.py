import time
import asyncio
import threading
import traceback
import pexpect 
import bittensor as bt
from gittensor.base.neuron import BaseNeuron
from gittensor.utils.radicle_utils import RadicleUtils

class BaseMinerNeuron(BaseNeuron):
    """ Base class for GitTensor Miners. """

    def __init__(self, config=None):
        super().__init__(config=config)
        self.radicle_utils = RadicleUtils(config=self.config, logging=bt.logging)

        if not self.config.miner.get('blacklist', {}).get('force_validator_permit', False):
            bt.logging.warning(
                "Miner is allowing non-validators to send requests. This is potentially a misconfiguration or a security risk depending on the subnet's design."
            )
        if self.config.miner.get('blacklist', {}).get('allow_non_registered', False):
            bt.logging.warning(
                "Miner is allowing non-registered entities to send requests. This is a security risk."
            )

        self.axon = bt.axon(wallet=self.wallet, port=self.config.axon.port)
        bt.logging.info(f"Axon created: {self.axon}")

        self.should_exit: bool = False
        self.is_running: bool = False
        self.thread: threading.Thread = None
        self.lock = asyncio.Lock()

        self.radicle_node_process: pexpect.spawn = None # Type hint for clarity
        self.radicle_utils.setup_radicle_dependencies()
        self.radicle_utils.ensure_radicle_auth_and_config(is_miner=True)
        self.start_radicle_node()


    def start_radicle_node(self):
        bt.logging.info("Attempting to start Radicle seed node...")
        success, status_stdout, _ = self.radicle_utils.run_rad_command("rad node status", suppress_error=True)
        if success and "running" in status_stdout.lower() and "offline" not in status_stdout.lower():
            bt.logging.info("Radicle node appears to be already running.")
            return

        try:
            command = "rad node start"
            passphrase = self.config.radicle.get("passphrase", "<YOUR_RADICAL_PASSPHRASE>")
            if passphrase == "<YOUR_RADICAL_PASSPHRASE>":
                 bt.logging.warning("Using default placeholder for Radicle passphrase. Set --radicle.passphrase or RADICLE_PASSPHRASE env var.")

            self.radicle_node_process = self.radicle_utils.start_radicle_node_with_pexpect(command, passphrase)
            if self.radicle_node_process and self.radicle_node_process.isalive():
                bt.logging.info(f"Radicle node process started via pexpect (PID: {self.radicle_node_process.pid}).")
                time.sleep(5) 
            else:
                bt.logging.error("Radicle node process failed to start or is not alive after pexpect attempt.")
                self.radicle_node_process = None
        except Exception as e:
            bt.logging.error(f"Failed to start Radicle node: {e}\n{traceback.format_exc()}")
            self.radicle_node_process = None


    def _log_radicle_node_output_placeholder(self):
        if self.radicle_node_process and self.radicle_node_process.isalive():
            try:
                # This is a very basic, potentially blocking way to read.
                # A more robust solution would use async reads or select.
                # For pexpect, it's often better to check `child.before` after an `expect` call.
                # Since `rad node start` usually daemonizes or outputs and exits (if already running),
                # continuous reading might not be the primary way to interact with it.
                # However, if it runs in foreground with pexpect, this could be useful.
                # For now, this is a placeholder.
                # output = self.radicle_node_process.read_nonblocking(size=1024, timeout=0.1)
                # if output:
                #    bt.logging.debug(f"[RadicleNode STDOUT/ERR] {output.strip()}")
                pass 
            except Exception as e:
                bt.logging.trace(f"Error reading from radicle node process (placeholder log): {e}")


    def base_run(self):
        self.sync() 
        bt.logging.info(f"Serving axon on port {self.config.axon.port} with netuid {self.config.netuid}")
        self.axon.serve(netuid=self.config.netuid, subtensor=self.subtensor)
        bt.logging.info(f"Starting axon")
        self.axon.start()
        bt.logging.info(f"Miner {self.uid} starting at block: {self.current_block}")

        try:
            while not self.should_exit:
                if self.radicle_node_process is None or not self.radicle_node_process.isalive():
                    # If `rad node start` daemonizes, `isalive()` might be false even if the node is running.
                    # A better check is `rad node status`.
                    status_ok, status_out, _ = self.radicle_utils.run_rad_command("rad node status", suppress_error=True)
                    if not (status_ok and "running" in status_out.lower() and "offline" not in status_out.lower()):
                        bt.logging.warning("Radicle node process seems not running. Attempting to restart...")
                        self.start_radicle_node()
                
                if self.step % 30 == 0: 
                    self._log_radicle_node_output_placeholder()

                # Sync metagraph and potentially other chain state.
                self.sync()
                
                self.step += 1
                time.sleep(self.config.miner.get('loop_interval', 12)) # Sleep for a short duration

        except KeyboardInterrupt:
            bt.logging.success("Miner killed by keyboard interrupt.")
        except Exception as e:
            bt.logging.error(f"Error in miner run loop: {traceback.format_exc()}")
        finally:
            if self.axon: self.axon.stop()
            if self.radicle_node_process and self.radicle_node_process.isalive():
                bt.logging.info("Stopping Radicle node process via pexpect child...")
                try:
                    self.radicle_node_process.close(force=True) # Close pexpect child
                except Exception as e_close:
                    bt.logging.error(f"Error closing pexpect child for Radicle node: {e_close}")
            # Ensure the Radicle node is stopped if it was started by this miner
            # This might require a `rad node stop` command if pexpect didn't manage it as a foreground process.
            # For now, relying on the process termination or manual stop.
            bt.logging.info("Exiting miner.")


    def run_in_background_thread(self):
        if not self.is_running:
            bt.logging.debug("Starting miner in background thread.")
            self.should_exit = False
            self.thread = threading.Thread(target=self.base_run, daemon=True)
            self.thread.start()
            self.is_running = True
            bt.logging.debug("Miner started in background.")

    def stop_run_thread(self):
        if self.is_running:
            bt.logging.debug("Stopping miner background thread.")
            self.should_exit = True
            if self.thread.is_alive():
                self.thread.join(timeout=10) # Wait for thread to finish
            self.is_running = False
            bt.logging.debug("Miner background thread stopped.")

    def __enter__(self):
        self.run_in_background_thread()
        return self

    def __exit__(self, exc_type, exc_value, traceback_obj):
        self.stop_run_thread()