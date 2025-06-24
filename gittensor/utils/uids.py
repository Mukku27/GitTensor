import torch
import random
import bittensor as bt
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from gittensor.base.neuron import BaseNeuron # For type hinting

def check_uid_availability(
    metagraph: "bt.metagraph.Metagraph", uid: int, vpermit_tao_limit: int = 1024
) -> bool:
    if not metagraph.axons[uid].is_serving:
        return False
    # Example for validator permit check (if applicable, adjust for your subnet's needs)
    # if metagraph.validator_permit[uid] and metagraph.S[uid] < vpermit_tao_limit:
    #     return False
    return True

def get_available_uids(
    neuron: "BaseNeuron", 
    k: int, 
    exclude_uids: List[int] = None
) -> torch.LongTensor:
    if exclude_uids is None:
        exclude_uids = []

    # Ensure the neuron's own UID is in the exclusion list if it's a validator querying others
    if hasattr(neuron, 'uid') and neuron.uid not in exclude_uids:
        exclude_uids.append(neuron.uid)

    available_axon_uids = []
    for uid_check in range(neuron.metagraph.n.item()):
        if uid_check not in exclude_uids:
            # Using basic check_uid_availability; can be expanded
            if check_uid_availability(neuron.metagraph, uid_check): 
                available_axon_uids.append(uid_check)

    if not available_axon_uids:
        return torch.LongTensor([])

    num_to_sample = min(k, len(available_axon_uids))
    sampled_uids = random.sample(available_axon_uids, num_to_sample)
    
    return torch.LongTensor(sampled_uids)