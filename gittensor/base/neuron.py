import copy
import os
import bittensor as bt
from abc import ABC, abstractmethod
from gittensor.utils.config import check_config, add_args, get_config
from gittensor import __spec_version__ as spec_version

class BaseNeuron(ABC):
    """
    Base class for GitTensor neurons (miners and validators).
    Handles Bittensor object initialization (wallet, subtensor, metagraph)
    and basic synchronization.
    """

    @classmethod
    def check_config(cls, config: "bt.Config"):
        check_config(cls, config)

    @classmethod
    def add_args(cls, parser):
        add_args(cls, parser)

    @classmethod
    def config(cls):
        return get_config(cls)

    subtensor: "bt.subtensor"
    wallet: "bt.wallet"
    metagraph: "bt.metagraph"
    spec_version: int = spec_version
    
    current_block: int

    def __init__(self, config=None):
        base_config = copy.deepcopy(config or BaseNeuron.config())
        self.config = self.config() 
        self.config.merge(base_config)
        self.check_config(self.config)

        bt.logging(config=self.config, logging_dir=self.config.full_path)
        bt.logging.info(self.config)

        self.device = self.config.neuron.get('device', 'cpu')

        bt.logging.info("Setting up bittensor objects.")
        self.wallet = bt.wallet(config=self.config)
        bt.logging.info(f"Wallet: {self.wallet}")
        self.subtensor = bt.subtensor(config=self.config)
        bt.logging.info(f"Subtensor: {self.subtensor}")
        self.metagraph = self.subtensor.metagraph(self.config.netuid)
        bt.logging.info(f"Metagraph: {self.metagraph}")

        self.check_registered()

        self.uid = self.metagraph.hotkeys.index(self.wallet.hotkey.ss58_address)
        bt.logging.info(
            f"Running neuron on subnet: {self.config.netuid} with uid {self.uid} using network: {self.subtensor.chain_endpoint}"
        )
        self.step = 0
        self.current_block = self.subtensor.get_current_block()


    @abstractmethod
    def run(self):
        ...

    def sync(self):
        """ Synchronizes the state of the neuron with the network. """
        bt.logging.debug(f"BaseNeuron.sync() for UID {self.uid}")
        self.current_block = self.subtensor.get_current_block() # Update current block
        
        self.check_registered() # Ensure still registered

        if self.should_sync_metagraph():
            self.resync_metagraph()

        if hasattr(self, 'should_set_weights') and self.should_set_weights():
            self.set_weights()
        
        self.save_state()

    def check_registered(self):
        if not self.subtensor.is_hotkey_registered(
            netuid=self.config.netuid,
            hotkey_ss58=self.wallet.hotkey.ss58_address,
        ):
            bt.logging.error(
                f"Wallet: {self.wallet} is not registered on netuid {self.config.netuid}."
                f" Please register hotkey using `btcli subnets register` or `recycle_register`."
            )
            exit()

    def should_sync_metagraph(self):
        """ Determines if the metagraph should be resynced. """
        return (
            self.current_block - self.metagraph.last_update[self.uid]
        ) > self.config.neuron.get('metagraph_resync_length', 100)

    def resync_metagraph(self):
        bt.logging.info(f"Resyncing metagraph for UID {self.uid}...")
        self.metagraph.sync(subtensor=self.subtensor)
        # Update UID if it changed, though for a running neuron this is unlikely unless reregistered.
        try:
            new_uid = self.metagraph.hotkeys.index(self.wallet.hotkey.ss58_address)
            if self.uid != new_uid:
                bt.logging.info(f"Neuron UID changed from {self.uid} to {new_uid} after metagraph sync.")
                self.uid = new_uid
        except ValueError:
            bt.logging.error(f"Hotkey {self.wallet.hotkey.ss58_address} not found in metagraph after sync. Exiting.")
            exit()
        bt.logging.info(f"Metagraph synced for UID {self.uid}. Current block: {self.current_block}, Metagraph last_update for UID: {self.metagraph.last_update[self.uid]}")


    def save_state(self):
        bt.logging.trace(
            "save_state() not implemented for this neuron. Implement if state needs saving."
        )

    def load_state(self):
        bt.logging.trace(
            "load_state() not implemented for this neuron. Implement if state needs loading."
        )