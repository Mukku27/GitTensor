import os
import argparse
import uuid # Added import
import bittensor as bt
from loguru import logger 

def check_config(cls, config: "bt.Config"):
    bt.logging.check_config(config)

    neuron_type_path_name = "validator" if "validator" in cls.__name__.lower() else "miner"
    
    # Use hotkey_str if available, otherwise hotkey (which might be the object)
    hotkey_name = config.wallet.hotkey if isinstance(config.wallet.hotkey, str) else config.wallet.hotkey_str # Prefer hotkey_str for path
    if not hotkey_name: # Fallback if hotkey_str is not set (e.g. during initial config load)
        hotkey_name = "default_hotkey" # Provide a fallback or load wallet to get it

    full_path = os.path.expanduser(
        "{}/{}/{}/netuid{}/{}".format(
            config.logging.logging_dir,
            config.wallet.name,
            hotkey_name, 
            config.netuid,
            neuron_type_path_name,
        )
    )
    # Ensure neuron config section exists
    if not hasattr(config, 'neuron'):
        config.neuron = bt.Config() # Create if not exists
        
    config.neuron.full_path = os.path.expanduser(full_path)
    if not os.path.exists(config.neuron.full_path):
        os.makedirs(config.neuron.full_path, exist_ok=True)
        bt.logging.debug(f"Created neuron directory: {config.neuron.full_path}")

    if not config.neuron.get('dont_save_events', False):
        try: # Check if level already exists
            logger.level("EVENTS")
        except ValueError:
            logger.level("EVENTS", no=38, icon="📝")
        
        logger.add(
            os.path.join(config.neuron.full_path, "events.log"),
            rotation=config.neuron.get('events_retention_size', "2 GB"),
            serialize=True,
            enqueue=True,
            backtrace=False,
            diagnose=False,
            level="EVENTS",
            format="{time:YYYY-MM-DD at HH:mm:ss} | {level} | {message}",
        )

def add_args(cls, parser: argparse.ArgumentParser):
    parser.add_argument("--netuid", type=int, help="Subnet netuid", default=1)

    # General neuron args from BaseNeuron
    if not hasattr(parser.add_argument_group('neuron'), '_group_actions'): # Check if group exists
        neuron_parser = parser.add_argument_group('neuron')
    else: # If it does, it might be from a parent class or another module
        neuron_parser = next((g for g in parser._action_groups if g.title == 'neuron'), None)
        if neuron_parser is None: # Still not found, create it
             neuron_parser = parser.add_argument_group('neuron')


    neuron_parser.add_argument(
        "--neuron.device", type=str, help="Device to run on (e.g., 'cpu', 'cuda:0').", default="cpu"
    )
    neuron_parser.add_argument(
        "--neuron.epoch_length", type=int, help="Blocks per epoch for sync/weight setting.", default=100
    )
    neuron_parser.add_argument(
        "--neuron.metagraph_resync_length", type=int, help="Blocks per metagraph resync.", default=100
    )
    neuron_parser.add_argument("--neuron.dont_save_events", action="store_true", help="If set, events are not saved to a log file.", default=False)
    neuron_parser.add_argument("--neuron.events_retention_size", type=str, help="Events retention size.", default="2 GB")
    neuron_parser.add_argument("--neuron.name", type=str, help="Name of the neuron (validator/miner).", default="base_neuron")


    # Radicle specific arguments
    if not hasattr(parser.add_argument_group('radicle'), '_group_actions'):
        radicle_parser = parser.add_argument_group('radicle')
    else:
        radicle_parser = next((g for g in parser._action_groups if g.title == 'radicle'), None)
        if radicle_parser is None: radicle_parser = parser.add_argument_group('radicle')


    radicle_parser.add_argument(
        "--radicle.passphrase",
        type=str,
        default=os.environ.get("RADICLE_PASSPHRASE", "<YOUR_RADICAL_PASSPHRASE>"), # Corrected placeholder
        help="Passphrase for Radicle identity. Best to set via RADICLE_PASSPHRASE env var."
    )

    if "validator" in cls.__name__.lower():
        neuron_parser.set_defaults(name="validator") # Set default name for validator

        if not hasattr(parser.add_argument_group('validator'), '_group_actions'):
            validator_parser = parser.add_argument_group('validator')
        else:
            validator_parser = next((g for g in parser._action_groups if g.title == 'validator'), None)
            if validator_parser is None: validator_parser = parser.add_argument_group('validator')

        validator_parser.add_argument("--validator.alpha", type=float, help="Moving average alpha for scores.", default=0.05)
        validator_parser.add_argument("--validator.num_concurrent_forwards", type=int, help="Number of concurrent forward calls.", default=1)
        validator_parser.add_argument("--validator.sample_size", type=int, help="Number of miners to query in a step.", default=5) # Default from your validator
        validator_parser.add_argument("--validator.disable_set_weights", action="store_true", help="Disable setting weights.", default=False)
        validator_parser.add_argument("--validator.axon_off", action="store_true", help="Do not serve an Axon for the validator.", default=False)
        validator_parser.add_argument("--validator.query_timeout", type=int, help="Timeout for dendrite queries in seconds.", default=55)
        validator_parser.add_argument("--validator.loop_interval", type=int, help="Validator main loop interval in seconds.", default=60)
        validator_parser.add_argument("--validator.error_sleep_time", type=int, help="Time to sleep after an error in the validation loop.", default=60)
        validator_parser.add_argument("--validator.empty_miner_list_sleep", type=int, help="Time to sleep if no miners are available.", default=60)


        radicle_parser.add_argument(
            "--radicle.validator.alias",
            default=f"bittensor-validator-{uuid.uuid4().hex[:8]}",
            help="Radicle identity alias for this validator."
        )

    elif "miner" in cls.__name__.lower():
        neuron_parser.set_defaults(name="miner") # Set default name for miner

        if not hasattr(parser.add_argument_group('miner'), '_group_actions'):
            miner_parser = parser.add_argument_group('miner')
        else:
            miner_parser = next((g for g in parser._action_groups if g.title == 'miner'), None)
            if miner_parser is None: miner_parser = parser.add_argument_group('miner')
            
        miner_blacklist_parser = miner_parser.add_argument_group('blacklist')
        miner_blacklist_parser.add_argument(
            "--miner.blacklist.force_validator_permit", action="store_true", help="Force incoming requests to have a validator permit.", default=False
        )
        miner_blacklist_parser.add_argument(
            "--miner.blacklist.allow_non_registered", action="store_true", help="Allow queries from non-registered entities (DANGEROUS).", default=False
        )
        miner_parser.add_argument("--miner.loop_interval", type=int, help="Miner main loop interval in seconds.", default=12)


        radicle_node_parser = radicle_parser.add_argument_group('node')
        radicle_node_parser.add_argument(
            "--radicle.node.alias", default="bittensor-miner-seed", help="Radicle node alias for the miner."
        )
        radicle_node_parser.add_argument(
            "--radicle.node.external_address", default=None, help="Publicly reachable Radicle node address (domain:port or ip:port)."
        )

def get_config(cls):
    parser = argparse.ArgumentParser()
    bt.wallet.add_args(parser)
    bt.subtensor.add_args(parser)
    bt.logging.add_args(parser)
    bt.axon.add_args(parser)
    cls.add_args(parser)
    return bt.config(parser)