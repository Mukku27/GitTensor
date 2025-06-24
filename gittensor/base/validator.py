import copy
import torch
import asyncio
import threading
import time # Added import
import traceback # Added import
import bittensor as bt
from typing import List
from abc import abstractmethod # Added import
from traceback import print_exception

from gittensor.base.neuron import BaseNeuron
# from gittensor.utils.uids import get_random_uids # If used, ensure this exists

class BaseValidatorNeuron(BaseNeuron):
    """ Base class for GitTensor Validators. """

    def __init__(self, config=None):
        super().__init__(config=config)

        self.hotkeys = copy.deepcopy(self.metagraph.hotkeys)
        self.dendrite = bt.dendrite(wallet=self.wallet)
        bt.logging.info(f"Dendrite: {self.dendrite}")

        self.scores = torch.zeros(self.metagraph.n.item(), dtype=torch.float32).to(self.device)
        self.moving_avg_scores = torch.zeros(self.metagraph.n.item(), dtype=torch.float32).to(self.device)
        self.alpha = self.config.validator.get('alpha', 0.05) # Use validator.alpha from config

        self.sync() 

        if not self.config.validator.get('axon_off', False):
            self.serve_axon()
        else:
            bt.logging.warning("Validator axon is off, not serving IP to chain.")

        # Attempt to get existing loop or create a new one
        try:
            self.loop = asyncio.get_event_loop()
            if self.loop.is_closed():
                raise RuntimeError("Event loop is closed")
        except RuntimeError:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
        
        self.should_exit: bool = False
        self.is_running: bool = False
        self.thread: threading.Thread = None
        self.lock = asyncio.Lock()

    def serve_axon(self):
        bt.logging.info("Serving validator axon to chain...")
        try:
            self.axon = bt.axon(wallet=self.wallet, config=self.config)
            
            # Attach dummy forward, blacklist, priority functions if required by axon.serve()
            # or ensure the concrete validator attaches its own.
            # For a validator axon primarily for serving IP, these might not be critical
            # if no actual synapses are handled by it.
            async def dummy_forward(synapse: bt.Synapse) -> bt.Synapse: return synapse
            def dummy_blacklist(synapse: bt.Synapse) -> tuple[bool, str]: return False, "allowed"
            def dummy_priority(synapse: bt.Synapse) -> float: return 0.0

            self.axon.attach(dummy_forward, dummy_blacklist, dummy_priority)
            
            self.subtensor.serve_axon(
                netuid=self.config.netuid,
                axon=self.axon,
            )
            self.axon.start() # Start axon serving
            bt.logging.info(f"Validator axon served and started: {self.axon}")
        except Exception as e:
            bt.logging.error(f"Failed to serve validator Axon: {e}\n{traceback.format_exc()}")

    async def concurrent_forward(self):
        # This is a placeholder. GitTensor validator uses run_sync_loop.
        num_concurrent = self.config.validator.get('num_concurrent_forwards', 1)
        coroutines = [self.forward() for _ in range(num_concurrent)] 
        await asyncio.gather(*coroutines)

    def base_run(self):
        self.sync()
        bt.logging.info(
            f"Running validator (UID: {self.uid}) on network: {self.config.subtensor.chain_endpoint} with netuid: {self.config.netuid}"
        )
        bt.logging.info(f"Validator starting at block: {self.current_block}")

        try:
            while not self.should_exit:
                bt.logging.debug(f"Validator {self.uid} run loop. Step: {self.step}, Block: {self.current_block}")
                
                try:
                    if self.loop.is_closed(): # Ensure loop is open before running
                        asyncio.set_event_loop(asyncio.new_event_loop())
                        self.loop = asyncio.get_event_loop()
                    
                    # Ensure run_sync_loop is awaited properly
                    # Check if run_sync_loop is an async def, then use loop.run_until_complete
                    if asyncio.iscoroutinefunction(self.run_sync_loop):
                        self.loop.run_until_complete(self.run_sync_loop())
                    else:
                        # If run_sync_loop is not async, but the logic inside might be,
                        # this part needs careful review. For now, assume it's designed to be run like this.
                        # Or, it could be a synchronous method that internally manages async operations.
                        bt.logging.warning("run_sync_loop is not an async function, calling it directly.")
                        self.run_sync_loop() # Call directly if not async

                except Exception as e:
                    bt.logging.error(f"Error in validator's run_sync_loop execution: {e}")
                    traceback.print_exc()
                    time.sleep(self.config.validator.get('error_sleep_time', 60))


                if self.should_exit: break
                self.sync() 
                self.step += 1
                
                time.sleep(self.config.validator.get('loop_interval', 60)) # Default loop interval

        except KeyboardInterrupt:
            bt.logging.success("Validator killed by keyboard interrupt.")
            if hasattr(self, 'axon') and self.axon and self.axon.is_serving: self.axon.stop()
        except Exception as err:
            bt.logging.error(f"Error during validation run: {err}")
            bt.logging.debug(print_exception(type(err), err, err.__traceback__))
        finally:
            if hasattr(self, 'axon') and self.axon and self.axon.is_serving:
                self.axon.stop()
            bt.logging.info("Exiting validator.")


    def run_in_background_thread(self):
        if not self.is_running:
            bt.logging.debug("Starting validator in background thread.")
            self.should_exit = False
            self.thread = threading.Thread(target=self.base_run, daemon=True)
            self.thread.start()
            self.is_running = True
            bt.logging.debug("Validator started in background.")

    def stop_run_thread(self):
        if self.is_running:
            bt.logging.debug("Stopping validator background thread.")
            self.should_exit = True
            if self.thread and self.thread.is_alive():
                 self.thread.join(timeout=15)
            self.is_running = False
            bt.logging.debug("Validator background thread stopped.")

    def __enter__(self):
        self.run_in_background_thread()
        return self

    def __exit__(self, exc_type, exc_value, traceback_obj):
        self.stop_run_thread()

    def should_set_weights(self) -> bool:
        if self.config.validator.get('disable_set_weights', False):
            return False
        return (self.current_block - self.metagraph.last_update[self.uid]) > self.config.neuron.get('epoch_length', 100)


    def set_weights(self):
        try:
            if torch.all(self.moving_avg_scores == 0): # Check if all scores are zero
                bt.logging.info("All moving_avg_scores are zero. Attempting to set zero weights.")
                # If you want to set zero weights, create a tensor of zeros.
                # Otherwise, this might result in an error or no weights being set.
                # For now, let's proceed and see how subtensor.set_weights handles it.
                # Or, explicitly skip if no scores to set.
                # return # Optionally skip if no scores to set or all are zero.

            if torch.isnan(self.moving_avg_scores).any():
                bt.logging.warning(f"Scores for weights contain NaN values. Replacing with 0. Scores: {self.moving_avg_scores}")
                self.moving_avg_scores = torch.nan_to_num(self.moving_avg_scores, 0.0)

            # Normalize scores to get weights
            if torch.sum(self.moving_avg_scores) == 0: # If all scores are zero after NaN replacement
                raw_weights = torch.zeros_like(self.moving_avg_scores)
                bt.logging.info("All scores are zero, setting zero weights.")
            else:
                raw_weights = torch.nn.functional.normalize(self.moving_avg_scores, p=1, dim=0)
            
            bt.logging.trace("Raw weights for chain:", raw_weights)

            processed_weight_uids, processed_weights = bt.utils.weight_utils.process_weights_for_netuid(
                uids=self.metagraph.uids.to(self.device),
                weights=raw_weights.to(self.device),    
                netuid=self.config.netuid,
                subtensor=self.subtensor,
                metagraph=self.metagraph
            )
            bt.logging.info(f"Processed UIDs for weights: {processed_weight_uids.tolist()}")
            bt.logging.info(f"Processed weights for chain: {processed_weights.tolist()}")

            success = self.subtensor.set_weights(
                wallet=self.wallet,
                netuid=self.config.netuid,
                uids=processed_weight_uids,
                weights=processed_weights,
                wait_for_finalization=False, 
                version_key=self.spec_version,
            )
            if success:
                bt.logging.info("Successfully set weights.")
            else:
                bt.logging.error("Failed to set weights.")
        except Exception as e:
            bt.logging.error(f"Error setting weights: {e}\n{traceback.format_exc()}")


    def resync_metagraph(self):
        bt.logging.info("Resyncing metagraph for validator.")
        previous_hotkeys = copy.deepcopy(self.metagraph.hotkeys)
        
        super().resync_metagraph() # Call base neuron's resync

        if previous_hotkeys != self.metagraph.hotkeys:
            bt.logging.info("Metagraph hotkeys changed. Re-initializing scores and hotkey list.")
            self.hotkeys = copy.deepcopy(self.metagraph.hotkeys) # Update local hotkey list
            
            # Reinitialize scores based on the new metagraph size/structure
            new_scores = torch.zeros(self.metagraph.n.item(), dtype=torch.float32).to(self.device)
            new_moving_avg_scores = torch.zeros(self.metagraph.n.item(), dtype=torch.float32).to(self.device)
            
            # Preserve scores for UIDs that still exist if possible (more complex logic)
            # For simplicity, we'll reinitialize. If fine-grained preservation is needed:
            # You'd map old UIDs to new UIDs if the hotkey still exists.
            
            self.scores = new_scores
            self.moving_avg_scores = new_moving_avg_scores
            bt.logging.info(f"Scores re-initialized for {self.metagraph.n.item()} UIDs.")
        else:
            bt.logging.info("Metagraph resynced, hotkeys unchanged.")


    def update_scores_for_uids(self, uids_to_update: List[int], round_scores_for_uids: torch.Tensor):
        if not isinstance(uids_to_update, list) or not all(isinstance(uid, int) for uid in uids_to_update):
            bt.logging.error("uids_to_update must be a list of integers.")
            return
        if not isinstance(round_scores_for_uids, torch.Tensor) or round_scores_for_uids.ndim != 1:
            bt.logging.error("round_scores_for_uids must be a 1D torch.Tensor.")
            return
        if len(uids_to_update) != round_scores_for_uids.size(0):
            bt.logging.error(f"Mismatch in lengths: uids_to_update ({len(uids_to_update)}) and round_scores_for_uids ({round_scores_for_uids.size(0)})")
            return

        scattered_scores = torch.zeros(self.metagraph.n.item(), dtype=torch.float32).to(self.device)
        valid_uids_tensor = torch.tensor(uids_to_update, dtype=torch.long).to(self.device)
        
        # Ensure round_scores_for_uids is on the correct device
        round_scores_for_uids_device = round_scores_for_uids.to(self.device)

        # Scatter the round scores to their respective UID positions
        scattered_scores.scatter_(0, valid_uids_tensor, round_scores_for_uids_device)

        # Update moving average scores
        self.moving_avg_scores = (1 - self.alpha) * self.moving_avg_scores + self.alpha * scattered_scores
        
        bt.logging.debug(f"Updated moving_avg_scores for UIDs {uids_to_update}. Current averages for these UIDs: {self.moving_avg_scores[uids_to_update]}")


    @abstractmethod
    async def forward(self): # As per your structure, though run_sync_loop is primary
        ...
    
    @abstractmethod
    async def run_sync_loop(self): # This is the main async validation driver
        ...