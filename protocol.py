import typing
import bittensor as bt
from typing import Optional, List

class RadicleSubnetSynapse(bt.Synapse):
    """
    A Synapse for the Radicle Subnet.
    It supports operations like validating a repository push by a validator
    and requesting the status of the miner's Radicle seed node.

    Attributes:
    - operation_type: Defines the action to be performed by the miner.
                      Can be "VALIDATE_PUSH" or "GET_MINER_STATUS".
    - repo_rid: (For VALIDATE_PUSH) The Radicle ID (RID) of the repository pushed by the validator.
    - commit_hash: (For VALIDATE_PUSH) The commit hash of the push to be validated.

    - validation_passed: (Response for VALIDATE_PUSH) Boolean indicating if the miner successfully
                         verified the validator's push (e.g., by being able to track/seed it).
    - miner_status: (Response for GET_MINER_STATUS) A dictionary containing status information
                    from the miner's Radicle node (e.g., uptime, seeded RIDs).
    - error_message: Optional error message if an operation failed.
    """

    # Required: The type of operation the validator is requesting.
    operation_type: str

    # --- Validator-sent fields ---
    # For "VALIDATE_PUSH" operation
    repo_rid: Optional[str] = None
    commit_hash: Optional[str] = None

    # --- Miner-filled response fields ---
    # For "VALIDATE_PUSH" response
    validation_passed: Optional[bool] = None

    # For "GET_MINER_STATUS" response
    miner_radicle_node_alias: Optional[str] = None
    miner_radicle_node_id: Optional[str] = None
    is_miner_radicle_node_running: Optional[bool] = None
    seeded_rids_count: Optional[int] = None # Number of RIDs the miner is actively seeding
    # A more detailed list could be added if needed, but count is simpler for scoring.

    # General response fields
    status_message: Optional[str] = None # General status like "SUCCESS", "FAILURE"
    error_message: Optional[str] = None

    # Define the axon_hotkey and dendrite_hotkey for Bittensor's signature verification
    # These are filled automatically by Bittensor.
    # axon_hotkey: Optional[str] = None
    # dendrite_hotkey: Optional[str] = None

    def deserialize(self) -> bytes:
        return self

    @property
    def required_hash_fields(self) -> List[str]:
        fields = ["operation_type"]
        # If these fields are part of the request, they should be hashed.
        # The Synapse base class handles None values appropriately during hashing.
        if self.repo_rid is not None:
            fields.append("repo_rid")
        if self.commit_hash is not None:
            fields.append("commit_hash")
        return fields
    
    @property
    def body_hash(self) -> str:
        """
        Override body_hash to ensure required_hash_fields is accessed correctly.
        """
        import hashlib
        m = hashlib.sha256()
        for field in self.required_hash_fields: 
            value = getattr(self, field, None)
            if value is not None:
                m.update(str(value).encode('utf-8'))
        return m.hexdigest()