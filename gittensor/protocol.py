
import bittensor as bt
from typing import Optional, List, ClassVar, Dict 
from pydantic import Field 

class RadicleGittensorSynapse(bt.Synapse):
    """
    A Synapse for the Radicle GitTensor Subnet.
    Used for operations related to decentralized git repository hosting.
    """
    # Required: The type of operation.
    # For now: "VALIDATE_PUSH" (Validator pushed, miner should validate by seeding)
    # Future: "GET_MINER_RAD_STATUS", "VALIDATE_CLONE", etc.
    operation_type: str 

    # === Validator-sent fields ===
    # For VALIDATE_PUSH operation (validator informs miner about a new repo)
    repo_rid: Optional[str] = None      # Radicle ID of the repository
    commit_hash: Optional[str] = None   # Latest commit hash of the pushed repo

    # === Miner-filled response fields ===
    # For VALIDATE_PUSH response
    validation_passed: Optional[bool] = None # True if miner successfully seeded/validated

    # General status and error messages
    status_message: Optional[str] = None 
    error_message: Optional[str] = None

    # For future use with GET_MINER_RAD_STATUS
    miner_radicle_node_id: Optional[str] = None
    miner_radicle_node_alias: Optional[str] = None
    is_miner_radicle_node_running: Optional[bool] = None
    seeded_rids_count: Optional[int] = None

    def deserialize(self) -> "RadicleGittensorSynapse":
        # Basic deserialization, can be extended.
        return self


    @property
    def required_hash_fields(self) -> List[str]:
        fields = ["operation_type"]
        if self.repo_rid: fields.append("repo_rid")
        if self.commit_hash: fields.append("commit_hash")
        # Add response fields if they are part of the signed response hash
        if self.validation_passed is not None: fields.append("validation_passed")
        return fields
    
    @property
    def body_hash(self) -> str:
        import hashlib
        m = hashlib.sha256()
        for field_name in self.required_hash_fields: # Call property if it is one
            value = getattr(self, field_name, None)
            if value is not None:
                m.update(str(value).encode('utf-8'))
        return m.hexdigest()

    