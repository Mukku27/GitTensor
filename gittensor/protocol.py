import typing
import bittensor as bt
from typing import Optional, List

class RadicleSubnetSynapse(bt.Synapse):
    """
    A Synapse for the Radicle Subnet.
    It supports operations like validating a repository push by a validator
    and requesting the status of the miner's Radicle seed node.
    """

    operation_type: str  # "VALIDATE_PUSH", "GET_MINER_STATUS", "VALIDATE_CHANGES_SYNC", "VALIDATE_BRANCH_SYNC", "VALIDATE_ISSUE_SYNC", "VALIDATE_PATCH_SYNC", "UNSEED_REPO"

    repo_rid: Optional[str] = None
    commit_hash: Optional[str] = None
    
    repo_sync_rid: Optional[str] = None # Can be same as repo_rid for specific sync ops
    branch_sync_repo_id: Optional[str] = None 
    issue_sync_repo_id: Optional[str] = None
    patch_sync_repo_id: Optional[str] = None 

    validation_passed: Optional[bool] = None
    changes_synced_successfully: Optional[bool] = None 
    branch_changes_synced_successfully: Optional[bool] = None
    issue_synced_successfully: Optional[bool] = None 
    patch_synced_successfully: Optional[bool] = None
    unseed_command_successful: Optional[bool] = None

    miner_radicle_node_alias: Optional[str] = None
    miner_radicle_node_id: Optional[str] = None
    is_miner_radicle_node_running: Optional[bool] = None
    seeded_rids_count: Optional[int] = None

    status_message: Optional[str] = None
    error_message: Optional[str] = None

    def deserialize(self) -> "RadicleSubnetSynapse":
        return self

    @property
    def required_hash_fields(self) -> List[str]:
        fields = ["operation_type"]
        if self.repo_rid is not None: fields.append("repo_rid")
        if self.commit_hash is not None: fields.append("commit_hash")
        if self.repo_sync_rid is not None: fields.append("repo_sync_rid")
        if self.branch_sync_repo_id is not None: fields.append("branch_sync_repo_id")
        if self.issue_sync_repo_id is not None: fields.append("issue_sync_repo_id")  
        if self.patch_sync_repo_id is not None: fields.append("patch_sync_repo_id")  
        
        # Response fields that might be part of the hash if signed by miner
        if self.validation_passed is not None: fields.append("validation_passed")
        if self.changes_synced_successfully is not None: fields.append("changes_synced_successfully")
        if self.branch_changes_synced_successfully is not None: fields.append("branch_changes_synced_successfully") 
        if self.issue_synced_successfully is not None: fields.append("issue_synced_successfully")
        if self.patch_synced_successfully is not None: fields.append("patch_synced_successfully") 
        if self.unseed_command_successful is not None: fields.append("unseed_command_successful")
        
        if self.miner_radicle_node_alias is not None: fields.append("miner_radicle_node_alias")
        if self.miner_radicle_node_id is not None: fields.append("miner_radicle_node_id")
        if self.is_miner_radicle_node_running is not None: fields.append("is_miner_radicle_node_running")
        if self.seeded_rids_count is not None: fields.append("seeded_rids_count")
        if self.status_message is not None: fields.append("status_message")
        if self.error_message is not None: fields.append("error_message")
        return fields
    
    @property
    def body_hash(self) -> str:
        import hashlib
        m = hashlib.sha256()
        # Call the property to get the list of fields
        for field_name in self.required_hash_fields: 
            value = getattr(self, field_name, None)
            if value is not None:
                m.update(str(value).encode('utf-8'))
        return m.hexdigest()
