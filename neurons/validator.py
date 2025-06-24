import os
import time
import traceback
import asyncio
import torch
import shutil # Added import
import uuid   # Added import
import shlex  # Import shlex for shell command quoting
import bittensor as bt
from typing import List, Dict, Optional, Tuple, Any 

from gittensor.base.validator import BaseValidatorNeuron
from gittensor.protocol import RadicleSubnetSynapse
from gittensor.utils.radicle_utils import RadicleUtils
from gittensor.utils.uids import get_available_uids
from gittensor.validator_logic.repo_operations import RepoValidatorOperations
from gittensor.validator_logic.reward import get_reward_weights # Import reward calculation

class Validator(BaseValidatorNeuron):
    def __init__(self):
        super().__init__()
        self.radicle_utils = RadicleUtils(config=self.config, logging=bt.logging)
        self.repo_ops = RepoValidatorOperations(rad_utils=self.radicle_utils, logging=bt.logging)
        
        self.radicle_utils.setup_radicle_dependencies()
        self.radicle_utils.ensure_radicle_auth_and_config(is_miner=False)
        
        self.query_timeout = self.config.validator.query_timeout # Use direct attribute from config
        self.persistent_clone_path_for_session: Optional[str] = None
        
        # Track which UIDs participated in the round for scoring
        self.uids_participated_in_round: List[int] = []


    async def forward(self):
        """
        Main validation forward pass. In GitTensor, this is effectively driven by `run_sync_loop`.
        This method can be a placeholder or trigger `run_sync_loop` if the base class's
        `concurrent_forward` mechanism is used. For a sequential validation like GitTensor's,
        `run_sync_loop` is the primary entry point called by `base_run`.
        """
        bt.logging.trace("Validator.forward() called (placeholder, run_sync_loop is main driver).")
        # If BaseValidatorNeuron's concurrent_forward is active and calls this,
        # you might want to await run_sync_loop here, but ensure it's not called twice.
        # For now, assuming base_run calls run_sync_loop directly.
        pass


    async def run_sync_loop(self):
        bt.logging.info(f"Validator {self.uid}: Starting new validation round (Step: {self.step}).")
        self.uids_participated_in_round = [] # Reset for the new round

        # === Stage 1: Create and Push Repository by Validator ===
        bt.logging.info(f"Validator {self.uid}: Stage 1 - Creating and pushing a new Radicle repository...")
        repo_to_validate_rid, commit_hash, push_error, _ = self.repo_ops.create_and_push_radicle_repo()
        
        if push_error or not repo_to_validate_rid or not commit_hash:
            bt.logging.error(f"Validator {self.uid}: Failed Stage 1 (validator create/push repo): {push_error}. Skipping this validation round.")
            if self.persistent_clone_path_for_session and os.path.exists(self.persistent_clone_path_for_session):
                try: shutil.rmtree(self.persistent_clone_path_for_session)
                except Exception as e_cl: self.logging.error(f"Error cleaning persistent clone after Stage 1 fail: {e_cl}")
                self.persistent_clone_path_for_session = None
            await asyncio.sleep(self.config.validator.get('error_sleep_time', 60)) 
            return

        bt.logging.info(f"Validator {self.uid}: Stage 1 complete. Validator pushed RID: {repo_to_validate_rid}, Commit: {commit_hash}")

        # Clean up persistent clone from any previous round before starting a new one
        if self.persistent_clone_path_for_session and os.path.exists(self.persistent_clone_path_for_session):
            bt.logging.debug(f"Validator {self.uid}: Cleaning up persistent clone from previous round: {self.persistent_clone_path_for_session}")
            try: shutil.rmtree(self.persistent_clone_path_for_session)
            except Exception as e_clean: bt.logging.error(f"Error cleaning old session clone: {e_clean}")
            self.persistent_clone_path_for_session = None

        # === Stage 2: Identify Available Miners ===
        num_miners_to_query = self.config.validator.sample_size
        
        available_uids_tensor = get_available_uids(self, k=num_miners_to_query, exclude_uids=[self.uid])
        available_uids = available_uids_tensor.tolist()
        self.uids_participated_in_round.extend(uid for uid in available_uids if uid not in self.uids_participated_in_round)


        if not available_uids:
            bt.logging.warning(f"Validator {self.uid}: No active miners found to query. Waiting for next cycle.")
            await asyncio.sleep(self.config.validator.empty_miner_list_sleep)
            return 
        bt.logging.info(f"Validator {self.uid}: Stage 2 complete. Found {len(available_uids)} active miners for testing: {available_uids}")
        
        # Store results per UID for this round
        miner_round_data: Dict[int, Dict[str, Any]] = {uid: {} for uid in available_uids} 

        # === Stage 3: Get Miner Status (and Node IDs) ===
        bt.logging.info(f"Validator {self.uid}: Stage 3 - Querying {len(available_uids)} miners for GET_MINER_STATUS...")
        get_status_synapse = RadicleSubnetSynapse(operation_type="GET_MINER_STATUS")
        target_axons_status = [self.metagraph.axons[uid] for uid in available_uids]
        
        get_status_responses: List[RadicleSubnetSynapse] = await self.dendrite.forward(
            axons=target_axons_status, synapse=get_status_synapse, timeout=self.query_timeout
        )
        uids_for_targeted_tests = [] # Miners who successfully provide status and node ID
        for i, uid in enumerate(available_uids):
            resp = get_status_responses[i]
            if resp and resp.dendrite.status_code == 200 and resp.is_miner_radicle_node_running and resp.miner_radicle_node_id and resp.miner_radicle_node_id != "N/A":
                miner_round_data[uid]['node_id'] = resp.miner_radicle_node_id
                miner_round_data[uid]['get_miner_status_success'] = True # For scoring
                uids_for_targeted_tests.append(uid)
                bt.logging.info(f"UID {uid}: GET_MINER_STATUS success. Node ID: {resp.miner_radicle_node_id}, Alias: {resp.miner_radicle_node_alias}, Seeds: {resp.seeded_rids_count}")
            else:
                err_msg = f"Dendrite: {resp.dendrite.status_code if resp and resp.dendrite else 'N/A'}-{resp.dendrite.status_message if resp and resp.dendrite else 'N/A'}, App Error: {resp.error_message if resp else 'No response'}"
                bt.logging.warning(f"UID {uid}: GET_MINER_STATUS failed or incomplete. {err_msg}")
                miner_round_data[uid]['get_miner_status_success'] = False
        
        if not uids_for_targeted_tests:
            bt.logging.warning(f"Validator {self.uid}: No miners provided valid Node IDs. Most subsequent tests will be skipped for this round.")
        else:
            # === Stage 4: Validate Push (Miner Seeds Repo) ===
            bt.logging.info(f"Validator {self.uid}: Stage 4 - Querying {len(uids_for_targeted_tests)} miners for VALIDATE_PUSH of RID {repo_to_validate_rid}...")
            validate_push_synapse = RadicleSubnetSynapse(operation_type="VALIDATE_PUSH", repo_rid=repo_to_validate_rid, commit_hash=commit_hash)
            target_axons_validate = [self.metagraph.axons[uid] for uid in uids_for_targeted_tests] # Use filtered list
            validate_push_responses: List[RadicleSubnetSynapse] = await self.dendrite.forward(
                axons=target_axons_validate, synapse=validate_push_synapse, timeout=self.query_timeout
            )
            for i, uid in enumerate(uids_for_targeted_tests): # Iterate over the same filtered list
                resp = validate_push_responses[i]
                if resp and resp.dendrite.status_code == 200 and resp.validation_passed:
                    miner_round_data[uid]['validated_push_success'] = True
                    bt.logging.info(f"UID {uid}: VALIDATE_PUSH success for RID {repo_to_validate_rid}.")
                else:
                    miner_round_data[uid]['validated_push_success'] = False
                    err_msg = f"Dendrite: {resp.dendrite.status_code if resp and resp.dendrite else 'N/A'}, App Error: {resp.error_message if resp else 'No response'}"
                    bt.logging.warning(f"UID {uid}: VALIDATE_PUSH failed for RID {repo_to_validate_rid}. {err_msg}")


            # === Obtain persistent clone for this session (from a successful seeder) ===
            # Try to find a miner that successfully seeded the repo to clone from for validator's modifications
            first_seeder_uid_for_clone = next((uid for uid in uids_for_targeted_tests if miner_round_data[uid].get('validated_push_success')), None)
            if first_seeder_uid_for_clone and 'node_id' in miner_round_data[first_seeder_uid_for_clone]:
                node_id_to_clone_from = miner_round_data[first_seeder_uid_for_clone]['node_id']
                self.persistent_clone_path_for_session = self._get_persistent_clone_for_session(repo_to_validate_rid, node_id_to_clone_from) 
            
            if not self.persistent_clone_path_for_session:
                 bt.logging.warning(f"Validator {self.uid}: Could not get a persistent clone of {repo_to_validate_rid}. Modification-based tests will be skipped.")
            else:
                bt.logging.info(f"Validator {self.uid}: Using persistent clone at {self.persistent_clone_path_for_session} for modification tests.")
                
                # === Stage 5: Initial Clone Test (Validator clones from each successful seeder) ===
                bt.logging.info(f"Validator {self.uid}: Stage 5 - Testing initial clone from miners who validated push...")
                for uid_clone_test in uids_for_targeted_tests:
                    if miner_round_data[uid_clone_test].get('validated_push_success') and 'node_id' in miner_round_data[uid_clone_test]:
                        # This uses repo_ops.clone_repository_locally which creates a *transient* clone and cleans it up.
                        clone_result_dict = self.repo_ops.clone_repository_locally(repo_to_validate_rid, miner_round_data[uid_clone_test]['node_id'])
                        miner_round_data[uid_clone_test]['initial_clone_test_success'] = clone_result_dict['status']
                        if clone_result_dict['status']:
                            bt.logging.info(f"UID {uid_clone_test}: Initial clone test SUCCESS from node {miner_round_data[uid_clone_test]['node_id']}.")
                            if clone_result_dict.get('dir') and os.path.exists(clone_result_dict['dir']): # Should be cleaned by repo_ops
                                bt.logging.trace(f"Transient clone dir {clone_result_dict['dir']} was not cleaned by repo_ops, attempting cleanup.")
                                try: shutil.rmtree(clone_result_dict['dir'])
                                except Exception as e_cl_ops: bt.logging.error(f"Error cleaning {clone_result_dict['dir']}: {e_cl_ops}")
                        else:
                            bt.logging.warning(f"UID {uid_clone_test}: Initial clone test FAILED from node {miner_round_data[uid_clone_test]['node_id']}. Error: {clone_result_dict.get('error')}")
                
                # === Stage 5.5: Validator Pushes Changes & Miners Sync ===
                bt.logging.info(f"Validator {self.uid}: Stage 5.5 - Pushing changes from validator's persistent clone: {self.persistent_clone_path_for_session}...")
                changes_pushed_by_validator = self.repo_ops.modify_and_push_changes(self.persistent_clone_path_for_session, repo_to_validate_rid)
                if changes_pushed_by_validator:
                    bt.logging.info(f"Validator successfully pushed changes to {repo_to_validate_rid}. Now testing miner sync.")
                    await self._test_miner_sync_operation(uids_for_targeted_tests, miner_round_data, "VALIDATE_CHANGES_SYNC", 
                                                          {"repo_sync_rid": repo_to_validate_rid}, 'changes_synced_by_miner', 'changes_synced_successfully')
                else: bt.logging.warning(f"Validator failed to push changes to {repo_to_validate_rid}. Skipping related miner sync test.")

                # === Stage 5.75: Validator Creates New Branch & Miners Sync ===
                bt.logging.info(f"Validator {self.uid}: Stage 5.75 - Creating new branch from {self.persistent_clone_path_for_session}...")
                branch_pushed_by_validator, new_branch_name = self.repo_ops.create_branch_and_push(self.persistent_clone_path_for_session, repo_to_validate_rid)
                if branch_pushed_by_validator and new_branch_name:
                    bt.logging.info(f"Validator successfully pushed new branch '{new_branch_name}' to {repo_to_validate_rid}. Testing miner sync.")
                    await self._test_miner_sync_operation(uids_for_targeted_tests, miner_round_data, "VALIDATE_BRANCH_SYNC", 
                                                          {"branch_sync_repo_id": repo_to_validate_rid}, 'branch_synced_by_miner', 'branch_changes_synced_successfully')
                else: bt.logging.warning(f"Validator failed to push new branch to {repo_to_validate_rid}. Skipping related miner sync test.")
                
                # === Stage 5.85: Validator Creates Issue & Miners Sync ===
                bt.logging.info(f"Validator {self.uid}: Stage 5.85 - Creating issue in {self.persistent_clone_path_for_session}...")
                issue_created_by_validator = self.repo_ops.create_issue(self.persistent_clone_path_for_session, repo_to_validate_rid)
                if issue_created_by_validator:
                    bt.logging.info(f"Validator successfully created issue in {repo_to_validate_rid}. Testing miner sync.")
                    await self._test_miner_sync_operation(uids_for_targeted_tests, miner_round_data, "VALIDATE_ISSUE_SYNC",
                                                          {"issue_sync_repo_id": repo_to_validate_rid}, 'issue_synced_by_miner', 'issue_synced_successfully')
                else: bt.logging.warning(f"Validator failed to create issue in {repo_to_validate_rid}. Skipping related miner sync test.")

                # === Stage 5.90: Validator Creates Patch & Miners Sync ===
                bt.logging.info(f"Validator {self.uid}: Stage 5.90 - Creating patch from {self.persistent_clone_path_for_session}...")
                patch_pushed_by_validator, patch_ref = self.repo_ops.create_and_push_patch(self.persistent_clone_path_for_session, repo_to_validate_rid)
                if patch_pushed_by_validator and patch_ref:
                    bt.logging.info(f"Validator successfully pushed patch '{patch_ref}' to {repo_to_validate_rid}. Testing miner sync.")
                    await self._test_miner_sync_operation(uids_for_targeted_tests, miner_round_data, "VALIDATE_PATCH_SYNC",
                                                          {"patch_sync_repo_id": repo_to_validate_rid}, 'patch_synced_by_miner', 'patch_synced_successfully')
                else: bt.logging.warning(f"Validator failed to push patch to {repo_to_validate_rid}. Skipping related miner sync test.")
                
                # Cleanup persistent clone after all modification tests are done for this round
                if self.persistent_clone_path_for_session and os.path.exists(self.persistent_clone_path_for_session):
                    bt.logging.debug(f"Validator {self.uid}: Cleaning up session clone: {self.persistent_clone_path_for_session} after modification tests.")
                    try: shutil.rmtree(self.persistent_clone_path_for_session)
                    except Exception as e_clean_sess: bt.logging.error(f"Error cleaning session clone: {e_clean_sess}")
                    self.persistent_clone_path_for_session = None

        # === Stage 6: Test Repository Unseeding by Miners ===
        bt.logging.info(f"Validator {self.uid}: Stage 6 - Testing UNSEED_REPO for RID {repo_to_validate_rid} with miners who had prior success...")
        for uid_test_unseed in uids_for_targeted_tests: # Iterate over miners who passed initial status check
            # Only test unseeding if miner previously showed some capability (e.g., successful clone or push validation)
            if miner_round_data[uid_test_unseed].get('initial_clone_test_success', False) or miner_round_data[uid_test_unseed].get('validated_push_success', False):
                node_id_for_unseed = miner_round_data[uid_test_unseed]['node_id'] # Should exist if in uids_for_targeted_tests
                unseed_test_ok = await self.test_repository_unseeding(repo_to_validate_rid, uid_test_unseed, node_id_for_unseed)
                miner_round_data[uid_test_unseed]['unseed_test_success'] = unseed_test_ok
                if unseed_test_ok: bt.logging.info(f"UID {uid_test_unseed}: UNSEED_REPO test PASSED for RID {repo_to_validate_rid}.")
                else: bt.logging.warning(f"UID {uid_test_unseed}: UNSEED_REPO test FAILED for RID {repo_to_validate_rid}.")
            else:
                miner_round_data[uid_test_unseed]['unseed_test_success'] = False # Skip unseed test if prior stages failed
        
        # Calculate scores based on miner_round_data
        current_round_scores = get_reward_weights(self, miner_round_data, self.uids_participated_in_round)
        
        # Update moving_avg_scores using the final current_round_scores from this round
        self.update_scores_for_uids(self.uids_participated_in_round, current_round_scores[self.uids_participated_in_round])

        bt.logging.info(f"Validator {self.uid}: Validation round {self.step} completed. Moving average scores updated.")
        # sync() and set_weights() are called by the base class loop.

    async def _test_miner_sync_operation(self, uids_to_test: List[int], miner_data: Dict[int, Dict[str, Any]], 
                                       operation_type: str, synapse_args: Dict[str, Any], 
                                       miner_data_key: str, synapse_success_flag: str):
        """Helper to test a generic miner sync operation."""
        bt.logging.info(f"Validator: Testing {operation_type} with {len(uids_to_test)} miners. Synapse args: {synapse_args}")
        sync_synapse = RadicleSubnetSynapse(operation_type=operation_type, **synapse_args)
        
        # Filter uids_to_test to only those who have a node_id recorded
        relevant_uids = [uid for uid in uids_to_test if miner_data[uid].get('node_id')]
        if not relevant_uids:
            bt.logging.warning(f"No miners with node_id available for {operation_type} test. Skipping.")
            return

        target_axons_sync = [self.metagraph.axons[uid] for uid in relevant_uids]
        sync_responses: List[RadicleSubnetSynapse] = await self.dendrite.forward(
            axons=target_axons_sync, synapse=sync_synapse, timeout=self.query_timeout
        )

        for i, uid in enumerate(relevant_uids):
            resp = sync_responses[i]
            success_flag_value = getattr(resp, synapse_success_flag, False) if resp else False
            if resp and resp.dendrite.status_code == 200 and success_flag_value:
                miner_data[uid][miner_data_key] = True
                bt.logging.info(f"UID {uid}: {operation_type} ({miner_data_key}) success.")
            else:
                miner_data[uid][miner_data_key] = False
                err_msg = f"Dendrite: {resp.dendrite.status_code if resp and resp.dendrite else 'N/A'}, App Error: {resp.error_message if resp else 'No resp'}, SuccessFlag ('{synapse_success_flag}') is {success_flag_value}"
                bt.logging.warning(f"UID {uid}: {operation_type} ({miner_data_key}) failed. {err_msg}")


    def _get_persistent_clone_for_session(self, repo_rid: str, miner_node_id_for_fetch: str) -> Optional[str]:
        if not repo_rid or not miner_node_id_for_fetch: 
            self.logging.error("Missing repo_rid or miner_node_id for persistent clone."); return None
        
        base_dir = "/tmp/gittensor_validator_session_clones" 
        os.makedirs(base_dir, exist_ok=True)
        sanitized_rid = repo_rid.replace(":", "_").replace("/", "_") # Basic sanitization
        # Make clone path unique per session/round to avoid conflicts if cleanup fails
        clone_path = os.path.join(base_dir, f"sess_{sanitized_rid}_{self.step}_{str(uuid.uuid4())[:4]}")

        if os.path.exists(clone_path):
            self.logging.warning(f"Persistent clone path {clone_path} already exists. Removing before new clone.")
            try: shutil.rmtree(clone_path)
            except Exception as e: self.logging.error(f"Error removing old session clone dir {clone_path}: {e}"); return None
        
        self.logging.info(f"Validator: Attempting persistent clone of {repo_rid} from miner {miner_node_id_for_fetch} to {clone_path}")
        try:
            # Using --no-follow as this clone is for validator's use, not for validator to become a long-term seeder.
            clone_cmd = f"rad clone {repo_rid} {shlex.quote(clone_path)} --seed {miner_node_id_for_fetch} --no-confirm --no-follow"
            clone_success, stdout, stderr = self.radicle_utils.run_rad_command(clone_cmd)
            
            if clone_success and os.path.exists(os.path.join(clone_path, ".git")):
                self.logging.info(f"Validator: Persistent clone success at {clone_path}. Stdout: {stdout}")
                # Set remote URL to use the validator's identity for push
                # This assumes validator's rad identity is default or configured for `rad remote add`
                # And that 'rad' remote doesn't exist yet, or we want to overwrite it.
                # `rad remote add <name> <rid> <nid> --default` could be an option.
                # For simplicity, relying on rad's default remote setup after clone,
                # which should use the current `rad auth` identity.
                # Or, more explicitly:
                # self.radicle_utils.run_rad_command(f"git remote set-url rad rad://{repo_rid}", cwd=clone_path)
                return clone_path
            else:
                self.logging.error(f"Validator: Persistent clone failed. Success: {clone_success}, Stdout: {stdout}, Stderr: {stderr}")
                if os.path.exists(clone_path): shutil.rmtree(clone_path) # Clean up failed attempt
                return None
        except Exception as e:
            self.logging.error(f"Validator: Persistent clone exception: {e}\n{traceback.format_exc()}")
            if 'clone_path' in locals() and os.path.exists(clone_path): shutil.rmtree(clone_path)
            return None
    
    async def test_repository_unseeding(self, repo_rid: str, target_miner_uid: int, target_miner_node_id: str) -> bool:
        self.logging.info(f"Validator [test_unseeding]: Testing unseeding for RID {repo_rid} by UID {target_miner_uid} (Node ID: {target_miner_node_id})")
        unseed_synapse = RadicleSubnetSynapse(operation_type="UNSEED_REPO", repo_rid=repo_rid)
        
        # Ensure target_miner_uid is valid before accessing metagraph.axons
        if not (0 <= target_miner_uid < len(self.metagraph.axons)):
            self.logging.error(f"Invalid target_miner_uid {target_miner_uid} for unseeding test.")
            return False
        target_axon = self.metagraph.axons[target_miner_uid]
        
        unseed_responses: List[RadicleSubnetSynapse] = await self.dendrite.forward(axons=[target_axon], synapse=unseed_synapse, timeout=self.query_timeout)

        if not unseed_responses or not unseed_responses[0] or not unseed_responses[0].dendrite or unseed_responses[0].dendrite.status_code != 200:
            status_code = unseed_responses[0].dendrite.status_code if unseed_responses and unseed_responses[0] and unseed_responses[0].dendrite else "N/A"
            self.logging.warning(f"No valid UNSEED_REPO response from UID {target_miner_uid}. Dendrite status: {status_code}."); return False
        
        response = unseed_responses[0]
        if not response.unseed_command_successful:
            self.logging.warning(f"Miner UID {target_miner_uid} reported 'rad unseed' FAILED. Error: {response.error_message}"); return False

        self.logging.info(f"Miner UID {target_miner_uid} reported 'rad unseed' SUCCESS. Verifying by attempting re-clone (expect failure)...")
        # Attempt re-clone from the miner who just unseeded; it should ideally fail or timeout.
        base_reclone_dir = "/tmp/validator_post_unseed_clones"
        os.makedirs(base_reclone_dir, exist_ok=True)
        # Make reclone dir unique
        reclone_target_dir = os.path.join(base_reclone_dir, f"post_unseed_{repo_rid.replace(':','_')}_{target_miner_uid}_{uuid.uuid4().hex[:4]}")
        
        reclone_failed_as_expected = False
        try:
            # Clone specifically from the target miner's node ID.
            # Timeout for this clone attempt should be shorter, as we expect it to fail quickly.
            clone_cmd = f"rad clone {repo_rid} {shlex.quote(reclone_target_dir)} --seed {target_miner_node_id} --no-confirm --no-follow"
            self.logging.debug(f"Attempting re-clone with command: {clone_cmd}")
            # Use a shorter timeout for the re-clone attempt, as failure is expected.
            # However, run_rad_command uses a fixed timeout. This might need adjustment or a new helper.
            # For now, rely on run_rad_command's timeout.
            reclone_cmd_success, stdout_reclone, stderr_reclone = self.radicle_utils.run_rad_command(clone_cmd, suppress_error=True) # Suppress error as failure is OK

            # If clone command fails OR succeeds but .git dir doesn't exist (incomplete clone)
            if not reclone_cmd_success or not os.path.exists(os.path.join(reclone_target_dir, ".git")):
                self.logging.info(f"Re-clone from UID {target_miner_uid} (Node {target_miner_node_id}) FAILED as expected after unseed. Test PASSED. Stdout: {stdout_reclone}, Stderr: {stderr_reclone}")
                reclone_failed_as_expected = True
            else:
                self.logging.warning(f"Re-clone from UID {target_miner_uid} (Node {target_miner_node_id}) SUCCEEDED unexpectedly after unseed. Unseeding test FAILED. Stdout: {stdout_reclone}")
        except Exception as e_reclone:
            self.logging.info(f"Exception during re-clone attempt from UID {target_miner_uid} (expected if unseed worked): {e_reclone}")
            reclone_failed_as_expected = True # Exception during clone also means data not easily available
        finally:
            if os.path.exists(reclone_target_dir): 
                try: shutil.rmtree(reclone_target_dir)
                except Exception as e_cl_final: self.logging.error(f"Error cleaning reclone dir {reclone_target_dir}: {e_cl_final}")
        return reclone_failed_as_expected

    def run(self):
        self.base_run()


if __name__ == "__main__":
    with Validator() as validator:
        validator.run()