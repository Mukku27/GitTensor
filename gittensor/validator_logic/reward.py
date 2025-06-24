import torch
import bittensor as bt
from typing import List, TYPE_CHECKING, Dict # Added Dict

if TYPE_CHECKING:
    from gittensor.base.validator import BaseValidatorNeuron # For type hinting neuron
    from gittensor.protocol import RadicleSubnetSynapse


def get_reward_weights(
    neuron: "BaseValidatorNeuron", 
    responses_data: Dict[int, Dict[str, any]], # UID -> test results map
    uids_participated: List[int]
) -> torch.Tensor:
    """
    Calculates reward scores for UIDs based on their performance in validation tests.
    This version uses the `responses_data` dictionary populated in the validator's `run_sync_loop`.
    """
    if not uids_participated:
        return torch.zeros(neuron.metagraph.n.item(), dtype=torch.float32).to(neuron.device)

    # Initialize scores for all UIDs to 0
    raw_scores = torch.zeros(neuron.metagraph.n.item(), dtype=torch.float32).to(neuron.device)

    # Define weights for each successful test stage
    # These should sum up to roughly 1.0 for a perfectly performing miner
    # Max 0.05 (status) + 0.15 (seed) + 0.15 (clone) + 0.15 (changes) + 0.1 (branch) + 0.1 (issue) + 0.1 (patch) + 0.2 (unseed) = 1.0
    reward_map = {
        'get_miner_status_success': 0.05,
        'validated_push_success': 0.15,
        'initial_clone_test_success': 0.15,
        'changes_synced_by_miner': 0.15,
        'branch_synced_by_miner': 0.10,
        'issue_synced_by_miner': 0.10,
        'patch_synced_by_miner': 0.10,
        'unseed_test_success': 0.20,
        # Add more keys as per tests in validator's run_sync_loop
    }
    
    for uid in uids_participated:
        if uid in responses_data:
            miner_data = responses_data[uid]
            current_miner_score = 0.0
            
            # GET_MINER_STATUS (implicit via node_id presence for subsequent tests)
            if miner_data.get('node_id'): # Basic check that status was OK enough to get node_id
                current_miner_score += reward_map['get_miner_status_success']

            if miner_data.get('validated_push_success', False):
                current_miner_score += reward_map['validated_push_success']
            
            if miner_data.get('initial_clone_test_success', False):
                current_miner_score += reward_map['initial_clone_test_success']

            if miner_data.get('changes_synced_by_miner', False):
                current_miner_score += reward_map['changes_synced_by_miner']
            
            if miner_data.get('branch_synced_by_miner', False):
                current_miner_score += reward_map['branch_synced_by_miner']

            if miner_data.get('issue_synced_by_miner', False):
                current_miner_score += reward_map['issue_synced_by_miner']

            if miner_data.get('patch_synced_by_miner', False):
                current_miner_score += reward_map['patch_synced_by_miner']
            
            if miner_data.get('unseed_test_success', False): # Key used in validator
                current_miner_score += reward_map['unseed_test_success']

            raw_scores[uid] = current_miner_score
            bt.logging.debug(f"UID {uid} final raw score for round: {current_miner_score:.4f}")
        else:
            bt.logging.trace(f"No response data found for UID {uid} in this round.")

    # Ensure scores are not negative and clip at 1.0 (or max possible score)
    raw_scores = torch.clamp(raw_scores, min=0.0, max=1.0) 
    
    # Return a 1D tensor of scores for all UIDs (many will be 0 if not participated or failed all)
    return raw_scores