# The MIT License (MIT)
# Copyright © 2023 Yuma Rao
# Copyright © 2023 Opentensor Foundation

# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the “Software”), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.

# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

import os
import torch
import argparse
import bittensor as bt
from loguru import logger


def check_config(cls, config: "bt.Config"):
    r"""Checks/validates the config namespace object."""
    bt.logging.check_config(config)

    full_path = os.path.expanduser(
        "{}/{}/{}/netuid{}/{}".format(
            "~/.bittensor/neurons",  # TODO: change from ~/.bittensor/miners to ~/.bittensor/neurons
            config.wallet.name,
            config.wallet.hotkey,
            config.netuid,
            config.neuron.name,
        )
    )
    bt.logging.info("full path:", full_path)
    config.neuron.full_path = os.path.expanduser(full_path)
    if not os.path.exists(config.neuron.full_path):
        os.makedirs(config.neuron.full_path, exist_ok=True)

    if not config.neuron.dont_save_events:
        # Add custom event logger for the events.
        logger.level("EVENTS", no=38, icon="📝")
        logger.add(
            os.path.join(config.neuron.full_path, "events.log"),
            rotation=config.neuron.events_retention_size,
            serialize=True,
            enqueue=True,
            backtrace=False,
            diagnose=False,
            level="EVENTS",
            format="{time:YYYY-MM-DD at HH:mm:ss} | {level} | {message}",
        )

def add_args(cls, parser):
    """
    Adds relevant arguments to the parser for operation.
    """
    ##TODO: update the default netuid to testnet_netuid or mainnet_netuid
    parser.add_argument("--netuid", type=int, help="Subnet netuid", default=2)

    parser.add_argument(
        "--neuron.device",
        type=str,
        help="Device to run on.",
        default="cpu",
    )

    parser.add_argument(
        "--neuron.metagraph_resync_length",
        type=int,
        help="The number of blocks until metagraph is resynced.",
        default=100,
    )

    parser.add_argument(
        "--neuron.epoch_length",
        type=int,
        help="The default epoch length (how often we set weights, measured in 12 second blocks).",
        default=100,
    )

    parser.add_argument(
        "--mock",
        action="store_true",
        help="Mock neuron and all network components.",
        default=False,
    )

    parser.add_argument(
        "--neuron.mock",
        action="store_true",
        help="Dry run.",
        default=False,
    )

    parser.add_argument(
        "--neuron.events_retention_size",
        type=str,
        help="Events retention size.",
        default="100 MB",
    )

    parser.add_argument(
        "--neuron.dont_save_events",
        action="store_true",
        help="If set, we dont save events to a log file.",
        default=False,
    )

    parser.add_argument(
        "--wandb.off",
        action="store_true",
        help="Turn off wandb.",
        default=False,
    )

    parser.add_argument(
        "--wandb.offline",
        action="store_true",
        help="Runs wandb in offline mode.",
        default=False,
    )

    parser.add_argument(
        "--wandb.notes",
        type=str,
        help="Notes to add to the wandb run.",
        default="",
    )

def add_miner_args(cls, parser):
    """Add miner specific arguments to the parser."""

    parser.add_argument(
        "--neuron.rad_alias_miner",
        type=str,
        default="gittensor-miner-default", # Default alias
        help="Radicle alias for the miner. Miner will attempt to auth with this alias if it doesn't exist.",
    )
    parser.add_argument(
        "--neuron.miner_repo_base_dir",
        type=str,
        default="~/.gittensor/miner_repos",
        help="Base directory for the miner to store cloned/seeded Radicle repositories.",
    )
    parser.add_argument(
        "--neuron.start_rad_node_auto",
        action="store_true",
        default=True, # Attempt to start rad node automatically
        help="If set, the miner will attempt to start the radicle node daemon if not running.",
    )
    parser.add_argument(
        "--neuron.initialize_rad_identity_auto",
        action="store_true",
        default=True, # Attempt to initialize rad identity automatically
        help="If set, the miner will attempt to 'rad auth' with the specified alias if not found.",
    )
    parser.add_argument(
        "--neuron.name",
        type=str,
        help="Trials for this neuron go in neuron.root / (wallet_cold - wallet_hot) / neuron.name. ",
        default="miner",
    )

    parser.add_argument(
        "--neuron.suppress_cmd_output",
        action="store_true",
        help="If set, we suppress the text output of terminal commands to reduce terminal clutter.",
        default=True,
    )

    parser.add_argument(
        "--neuron.max_workers",
        type=int,
        help="Total number of subprocess that the miner is designed to run.",
        default=8,
    )

    parser.add_argument(
        "--blacklist.force_validator_permit",
        action="store_true",
        help="If set, we will force incoming requests to have a validator permit.",
        default=False,
    )

    parser.add_argument(
        "--blacklist.allow_non_registered",
        action="store_true",
        help="If set, miners will accept queries from non registered entities. (Dangerous!)",
        default=False,
    )

    parser.add_argument(
        "--wandb.project_name",
        type=str,
        default="folding-miners",
        help="Wandb project to log to.",
    )

    parser.add_argument(
        "--wandb.entity",
        type=str,
        default="opentensor-dev",
        help="Wandb entity to log to.",
    )

def add_validator_args(cls, parser):
    """Add validator specific arguments to the parser."""

    parser.add_argument(
        "--neuron.rad_alias_validator",
        type=str,
        default="gittensor-validator-default", # Default alias
        help="Radicle alias for the validator (primarily for inspection and local operations).",
    )
    parser.add_argument(
        "--neuron.validator_repo_base_dir",
        type=str,
        default="~/.gittensor/validator_repos",
        help="Base directory for the validator to manage its test Radicle repositories.",
    )
    parser.add_argument(
        "--neuron.num_test_repos",
        type=int,
        default=5,
        help="Number of unique test repositories the validator will try to maintain/create for testing.",
    )
    parser.add_argument(
        "--neuron.test_repo_prefix",
        type=str,
        default="gittensor-val-test-",
        help="Prefix for repositories created by the validator for testing.",
    )

    parser.add_argument(
        "--neuron.name",
        type=str,
        help="Trials for this neuron go in neuron.root / (wallet_cold - wallet_hot) / neuron.name. ",
        default="validator",
    )

    parser.add_argument(
        "--neuron.timeout",
        type=float,
        help="The timeout for each forward call. (seconds)",
        default=45,
    )

    parser.add_argument(
        "--neuron.update_interval",
        type=float,
        help="The interval in which the validators query the miners for updates. (seconds)",
        default=60,  # samples every 5-minutes in the simulation.
    )

    parser.add_argument(
        "--neuron.queue_size",
        type=int,
        help="The number of jobs to keep in the queue.",
        default=10,
    )

    parser.add_argument(
        "--neuron.sample_size",
        type=int,
        help="The number of miners to query in a single step.",
        default=10,
    )

    parser.add_argument(
        "--neuron.disable_set_weights",
        action="store_true",
        help="Disables setting weights.",
        default=False,
    )

    parser.add_argument(
        "--neuron.positive_alpha",
        type=float,
        help="Positive alpha parameter, how much to add of the new observation when the reward is positive.",
        default=0.10,
    )

    parser.add_argument(
        "--neuron.negative_alpha",
        type=float,
        help="Negative alpha parameter, how much to add of the new observation when the reward is 0.",
        default=0.03,
    )

    parser.add_argument(
        "--neuron.axon_off",
        "--axon_off",
        action="store_true",
        # Note: the validator needs to serve an Axon with their IP or they may
        #   be blacklisted by the firewall of serving peers on the network.
        help="Set this flag to not attempt to serve an Axon.",
        default=False,
    )

    parser.add_argument(
        "--neuron.vpermit_tao_limit",
        type=int,
        help="The maximum number of TAO allowed to query a validator with a vpermit.",
        default=20_000,
    )

    parser.add_argument(
        "--neuron.synthetic_job_interval",
        type=float,
        help="The amount of time that the synthetic job creation loop should wait before checking the queue size again.",
        default=60,
    )

    parser.add_argument(
        "--neuron.organic_enabled",
        action="store_true",
        help="Set this flag to enable organic scoring.",
        default=False,
    )

    parser.add_argument(
        "--neuron.organic_trigger",
        type=str,
        help="Organic query validation trigger mode (seconds or steps).",
        default="seconds",
    )

    parser.add_argument(
        "--neuron.organic_trigger_frequency",
        type=float,
        help="Organic query sampling frequency (seconds or steps value).",
        default=120.0,
    )

    parser.add_argument(
        "--neuron.organic_trigger_frequency_min",
        type=float,
        help="Minimum organic query sampling frequency (seconds or steps value).",
        default=5.0,
    )

    parser.add_argument(
        "--wandb.project_name",
        type=str,
        help="The name of the project where you are sending the new run.",
        default="folding-openmm",
    )

    parser.add_argument(
        "--wandb.entity",
        type=str,
        help="The name of the project where you are sending the new run.",
        default="macrocosmos",
    )

    parser.add_argument(
        "--organic_whitelist",
        nargs="+",  # Accepts one or more values as a list
        help="The validator will only accept organic queries from a list of whitelisted hotkeys.",
        default=[
            "5Cg5QgjMfRqBC6bh8X4PDbQi7UzVRn9eyWXsB8gkyfppFPPy",
        ],
    )


def config(cls):
    """
    Returns the configuration object specific to this miner or validator after adding relevant arguments.
    """
    parser = argparse.ArgumentParser()
    bt.wallet.add_args(parser)
    bt.subtensor.add_args(parser)
    bt.logging.add_args(parser)
    bt.axon.add_args(parser)
    cls.add_args(parser)
    return bt.config(parser)