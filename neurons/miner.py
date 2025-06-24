import os # Added for shlex path quoting safety
import shlex # Added for command construction
import time
import traceback
import bittensor as bt
from typing import Tuple
from gittensor.base.miner import BaseMinerNeuron
from gittensor.protocol import RadicleSubnetSynapse

class Miner(BaseMinerNeuron):
    def __init__(self):
        super().__init__()
        
        bt.logging.info(f"Attaching GitTensor specific functions to axon for Miner {self.uid}")
        self.axon.attach(
            forward_fn=self.forward_radicle_operation,
            blacklist_fn=self.blacklist_gittensor,
            priority_fn=self.priority_gittensor,
        )
        bt.logging.info(f"Axon handlers attached: {self.axon.attached_fns}")


    async def forward_radicle_operation(self, synapse: RadicleSubnetSynapse) -> RadicleSubnetSynapse:
        bt.logging.info(f"Miner {self.uid}: Received op: {synapse.operation_type} from {synapse.dendrite.hotkey} for RID: {synapse.repo_rid or synapse.repo_sync_rid or synapse.branch_sync_repo_id or synapse.issue_sync_repo_id or synapse.patch_sync_repo_id or 'N/A'}")

        if synapse.operation_type == "VALIDATE_PUSH":
            if not synapse.repo_rid:
                synapse.status_message = "FAILURE"; synapse.error_message = "repo_rid missing"; synapse.validation_passed = False; return synapse
            bt.logging.info(f"Miner {self.uid}: VALIDATE_PUSH for RID: {synapse.repo_rid}")
            # Ensure identity is unlocked for seeding, if Radicle requires it.
            # `rad seed` might not need passphrase if node is already configured and identity is default/unlocked.
            success, stdout_seed, stderr_seed = self.radicle_utils.run_rad_command(f"rad seed {synapse.repo_rid}")
            time.sleep(5) # Give time for seed to propagate/register
            list_success, list_stdout, list_stderr = self.radicle_utils.run_rad_command("rad ls --seeded")
            
            if success and list_success and synapse.repo_rid in list_stdout:
                synapse.status_message = "SUCCESS"; synapse.validation_passed = True
                bt.logging.info(f"Miner {self.uid}: VALIDATE_PUSH success for {synapse.repo_rid}. Seed stdout: {stdout_seed}")
            else:
                synapse.status_message = "FAILURE"; synapse.validation_passed = False
                synapse.error_message = f"Failed seed confirmation. Seed ok: {success}, Seed err: {stderr_seed}, Seed out: {stdout_seed}. List ok: {list_success}, List out: {list_stdout}, List err: {list_stderr}"
                bt.logging.warning(f"Miner {self.uid}: VALIDATE_PUSH failed for {synapse.repo_rid}. {synapse.error_message}")
            return synapse

        elif synapse.operation_type == "GET_MINER_STATUS":
            bt.logging.info(f"Miner {self.uid}: GET_MINER_STATUS request from {synapse.dendrite.hotkey}.")
            status_success, status_stdout, status_stderr = self.radicle_utils.run_rad_command("rad node status")
            _, alias_stdout, _ = self.radicle_utils.run_rad_command("rad self --alias", suppress_error=True)
            _, nid_stdout, _ = self.radicle_utils.run_rad_command("rad self --nid", suppress_error=True) # NID = Node ID
            
            synapse.is_miner_radicle_node_running = status_success and "running" in status_stdout.lower() and "offline" not in status_stdout.lower()
            synapse.miner_radicle_node_alias = alias_stdout.strip() or "N/A"
            synapse.miner_radicle_node_id = nid_stdout.strip() or "N/A" # This is the Radicle Node ID (Peer ID)

            if synapse.is_miner_radicle_node_running:
                list_success, list_stdout, _ = self.radicle_utils.run_rad_command("rad ls --seeded")
                if list_success: synapse.seeded_rids_count = len([line for line in list_stdout.splitlines() if line.strip().startswith("rad:")])
                else: synapse.seeded_rids_count = 0
                synapse.status_message = "SUCCESS"
            else:
                synapse.seeded_rids_count = 0; synapse.status_message = "FAILURE"
                synapse.error_message = f"Node not running or status check failed. Status cmd ok: {status_success}, Status output: {status_stdout} {status_stderr}"
            bt.logging.info(f"Miner {self.uid}: GET_MINER_STATUS response: NodeRunning={synapse.is_miner_radicle_node_running}, NodeAlias={synapse.miner_radicle_node_alias}, NodeID={synapse.miner_radicle_node_id}, SeedCount={synapse.seeded_rids_count}")
            return synapse
                
        elif synapse.operation_type == "VALIDATE_CHANGES_SYNC":
            rid_to_sync = synapse.repo_sync_rid
            if not rid_to_sync:
                synapse.status_message = "FAILURE"; synapse.error_message = "repo_sync_rid missing"; synapse.changes_synced_successfully = False; return synapse
            bt.logging.info(f"Miner {self.uid}: VALIDATE_CHANGES_SYNC for RID: {rid_to_sync}")
            # `rad sync --fetch` is crucial.
            sync_success, stdout_sync, stderr_sync = self.radicle_utils.run_rad_command(f"rad sync {rid_to_sync} --fetch")
            if sync_success and ("✓ Synced" in stdout_sync or "up to date" in stdout_sync.lower() or "nothing to sync" in stdout_sync.lower() or "✓ Project data fetched" in stdout_sync):
                synapse.changes_synced_successfully = True; synapse.status_message = "SUCCESS"
                bt.logging.info(f"Miner {self.uid}: VALIDATE_CHANGES_SYNC success for {rid_to_sync}. Output: {stdout_sync}")
            else:
                synapse.changes_synced_successfully = False; synapse.status_message = "FAILURE"
                synapse.error_message = f"Sync fail or success msg not found. Ok: {sync_success}, Out: {stdout_sync}, Err: {stderr_sync}"
                bt.logging.warning(f"Miner {self.uid}: VALIDATE_CHANGES_SYNC failed for {rid_to_sync}. {synapse.error_message}")
            return synapse

        elif synapse.operation_type == "VALIDATE_BRANCH_SYNC":
            rid_to_sync_branch = synapse.branch_sync_repo_id
            if not rid_to_sync_branch:
                synapse.status_message = "FAILURE"; synapse.error_message = "branch_sync_repo_id missing"; synapse.branch_changes_synced_successfully = False; return synapse
            bt.logging.info(f"Miner {self.uid}: VALIDATE_BRANCH_SYNC for RID: {rid_to_sync_branch}")
            sync_success, stdout_sync, stderr_sync = self.radicle_utils.run_rad_command(f"rad sync {rid_to_sync_branch} --fetch")
            if sync_success and ("✓ Synced" in stdout_sync or "up to date" in stdout_sync.lower() or "nothing to sync" in stdout_sync.lower() or "✓ Project data fetched" in stdout_sync):
                synapse.branch_changes_synced_successfully = True; synapse.status_message = "SUCCESS"
            else:
                synapse.branch_changes_synced_successfully = False; synapse.status_message = "FAILURE"
                synapse.error_message = f"Branch sync fail/msg not found. Ok: {sync_success}, Out: {stdout_sync}, Err: {stderr_sync}"
            return synapse
        
        elif synapse.operation_type == "VALIDATE_ISSUE_SYNC":
            rid_to_sync_issue = synapse.issue_sync_repo_id
            if not rid_to_sync_issue:
                synapse.status_message = "FAILURE"; synapse.error_message = "issue_sync_repo_id missing"; synapse.issue_synced_successfully = False; return synapse
            bt.logging.info(f"Miner {self.uid}: VALIDATE_ISSUE_SYNC for RID: {rid_to_sync_issue}")
            sync_success, stdout_sync, stderr_sync = self.radicle_utils.run_rad_command(f"rad sync {rid_to_sync_issue} --fetch")
            # Issue data sync might show "✓ Project data fetched" or similar, not always "✓ Synced <hash>"
            if sync_success and ("✓ Synced" in stdout_sync or "up to date" in stdout_sync.lower() or "nothing to sync" in stdout_sync.lower() or "✓ Project data fetched" in stdout_sync or "✓ Issues synchronized" in stdout_sync):
                synapse.issue_synced_successfully = True; synapse.status_message = "SUCCESS"
            else:
                synapse.issue_synced_successfully = False; synapse.status_message = "FAILURE"
                synapse.error_message = f"Issue sync fail/msg not found. Ok: {sync_success}, Out: {stdout_sync}, Err: {stderr_sync}"
            return synapse

        elif synapse.operation_type == "VALIDATE_PATCH_SYNC":
            rid_to_sync_patch = synapse.patch_sync_repo_id
            if not rid_to_sync_patch:
                synapse.status_message = "FAILURE"; synapse.error_message = "patch_sync_repo_id missing"; synapse.patch_synced_successfully = False; return synapse
            bt.logging.info(f"Miner {self.uid}: VALIDATE_PATCH_SYNC for RID: {rid_to_sync_patch}")
            sync_success, stdout_sync, stderr_sync = self.radicle_utils.run_rad_command(f"rad sync {rid_to_sync_patch} --fetch")
            if sync_success and ("✓ Synced" in stdout_sync or "up to date" in stdout_sync.lower() or "nothing to sync" in stdout_sync.lower() or "✓ Project data fetched" in stdout_sync or "✓ Patches synchronized" in stdout_sync):
                synapse.patch_synced_successfully = True; synapse.status_message = "SUCCESS"
            else:
                synapse.patch_synced_successfully = False; synapse.status_message = "FAILURE"
                synapse.error_message = f"Patch sync fail/msg not found. Ok: {sync_success}, Out: {stdout_sync}, Err: {stderr_sync}"
            return synapse
        
        elif synapse.operation_type == "UNSEED_REPO":
            if not synapse.repo_rid:
                synapse.status_message = "FAILURE"; synapse.error_message = "repo_rid missing"; synapse.unseed_command_successful = False; return synapse
            bt.logging.info(f"Miner {self.uid}: UNSEED_REPO for RID: {synapse.repo_rid}")
            unseed_success, stdout_unseed, stderr_unseed = self.radicle_utils.run_rad_command(f"rad unseed {synapse.repo_rid}")
            if unseed_success: # `rad unseed` usually doesn't output much on success.
                synapse.unseed_command_successful = True; synapse.status_message = "SUCCESS"
                bt.logging.info(f"Miner {self.uid}: UNSEED_REPO success for {synapse.repo_rid}. Output: {stdout_unseed}")
                
                # Deleting from storage is an aggressive step, ensure it's desired.
                # `rad unseed` only removes it from seeding policy, data might remain until `radicle-gc`
                rad_path_success, rad_path_stdout, _ = self.radicle_utils.run_rad_command("rad path")
                if rad_path_success and rad_path_stdout.strip():
                    rad_storage_base = rad_path_stdout.strip()
                    # RID needs to be the unique ID part, not full "rad:<id>" for path construction under storage/
                    rid_id_part = synapse.repo_rid.split(':')[-1] if ':' in synapse.repo_rid else synapse.repo_rid
                    if rid_id_part:
                        # The actual storage path is more complex, usually under ~/.radicle/storage/git/refs/rad/<rid>
                        # And also the packed objects. A simple `rm -rf` on a high-level RID path might be too broad or incorrect.
                        # For now, let's assume `rad unseed` is enough and `radicle-gc` will handle actual data removal.
                        # If direct deletion is needed, the exact path discovery for a RID is non-trivial.
                        # Placeholder for a more accurate deletion if required:
                        # storage_path_to_delete = os.path.join(rad_storage_base, "storage", "git", "refs", "rad", rid_id_part) # This is an example, actual path varies
                        # bt.logging.info(f"Miner {self.uid}: (Placeholder) Would attempt to delete storage path for {rid_id_part}")
                        # self.radicle_utils.run_rad_command(f"rm -rf {shlex.quote(storage_path_to_delete)}")
                        bt.logging.info(f"Miner {self.uid}: `rad unseed` completed. Data will be garbage collected by Radicle eventually.")
                    else:
                        bt.logging.warning(f"Miner {self.uid}: Could not parse RID for potential storage deletion: {synapse.repo_rid}")
            else:
                synapse.unseed_command_successful = False; synapse.status_message = "FAILURE"
                synapse.error_message = f"rad unseed failed. ok: {unseed_success}, stderr: {stderr_unseed}, stdout: {stdout_unseed}"
                bt.logging.warning(f"Miner {self.uid}: UNSEED_REPO failed for {synapse.repo_rid}. {synapse.error_message}")
            return synapse

        else:
            synapse.status_message = "FAILURE"; synapse.error_message = f"Unknown operation_type: {synapse.operation_type}"
            # Ensure all boolean flags are set to False for unknown op
            synapse.validation_passed = False; synapse.changes_synced_successfully = False
            synapse.branch_changes_synced_successfully = False; synapse.issue_synced_successfully = False
            synapse.patch_synced_successfully = False; synapse.unseed_command_successful = False
            bt.logging.error(f"Miner {self.uid}: {synapse.error_message} from {synapse.dendrite.hotkey}")
            return synapse


    async def blacklist_gittensor(self, synapse: RadicleSubnetSynapse) -> Tuple[bool, str]:
        if synapse.dendrite.hotkey not in self.metagraph.hotkeys:
            bt.logging.trace(f"Miner {self.uid}: Blacklisting unrecognized hotkey {synapse.dendrite.hotkey}")
            return True, "Unrecognized hotkey"
        
        try:
            requester_uid = self.metagraph.hotkeys.index(synapse.dendrite.hotkey)
            # Example: Blacklist if stake is less than a certain threshold (e.g., 1 TAO)
            # This threshold should be configurable. For now, using a placeholder.
            stake_threshold = self.config.miner.get('blacklist_stake_threshold', 1.0) # Example: 1 TAO in bittensor units (needs conversion)
            
            # Stake in metagraph is already in Tao (float)
            if self.metagraph.S[requester_uid] < stake_threshold:
                bt.logging.trace(f"Miner {self.uid}: Blacklisting {synapse.dendrite.hotkey} (UID {requester_uid}) due to low stake: {self.metagraph.S[requester_uid]} < {stake_threshold}")
                return True, f"Low stake (below {stake_threshold} TAO)"
        except ValueError: 
            bt.logging.trace(f"Miner {self.uid}: Hotkey {synapse.dendrite.hotkey} not found in metagraph (should have been caught by first check). Blacklisting.")
            return True, "Hotkey not in metagraph (consistency issue)"

        bt.logging.trace(f"Miner {self.uid}: Not blacklisting {synapse.dendrite.hotkey}")
        return False, "Allowed"

    async def priority_gittensor(self, synapse: RadicleSubnetSynapse) -> float:
        if synapse.dendrite.hotkey not in self.metagraph.hotkeys:
            return 0.0 # No priority for unknown hotkeys
        try:
            caller_uid = self.metagraph.hotkeys.index(synapse.dendrite.hotkey)
            priority = float(self.metagraph.S[caller_uid]) # Priority based on stake
            bt.logging.trace(f"Miner {self.uid}: Priority for {synapse.dendrite.hotkey} (UID {caller_uid}): {priority}")
            return priority
        except ValueError:
            return 0.0 # Should not happen if hotkey is in metagraph.hotkeys

    def run(self):
        self.base_run()

if __name__ == "__main__":
    with Miner() as miner:
        miner.run()