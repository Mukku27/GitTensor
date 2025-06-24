import os
import shutil
import uuid
import time
import random
import pexpect
import re
import shlex
import traceback # Added import
from typing import Tuple, Optional, Dict, Any # Added Dict, Any
from gittensor.utils.radicle_utils import RadicleUtils

class RepoValidatorOperations:
    def __init__(self, rad_utils: RadicleUtils, logging: Any): # Using Any for bittensor logging
        self.rad_utils = rad_utils
        self.logging = logging

    def create_and_push_radicle_repo(self) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
        repo_name = f"val-test-repo-{str(uuid.uuid4())[:8]}"
        temp_dir_for_creation = os.path.join("/tmp", f"val_create_{repo_name}")

        try:
            if os.path.exists(temp_dir_for_creation):
                shutil.rmtree(temp_dir_for_creation)
            os.makedirs(temp_dir_for_creation)

            # Git init and initial commit
            self.rad_utils.run_rad_command("git init", cwd=temp_dir_for_creation)
            self.rad_utils.run_rad_command("git checkout -b main", cwd=temp_dir_for_creation) # Ensure main branch

            with open(os.path.join(temp_dir_for_creation, "file1.py"), "w") as f:
                f.write(f"# Python test file {random.randint(1, 1000)}\nprint('Hello Radicle Validator!')")
            with open(os.path.join(temp_dir_for_creation, "README.md"), "w") as f:
                f.write(f"# Validator Test Repo\nRandom content: {uuid.uuid4()}")
            
            self.rad_utils.run_rad_command("git add .", cwd=temp_dir_for_creation)
            commit_msg = f"Initial commit by validator {time.time()}"
            git_commit_success, _, git_commit_stderr = self.rad_utils.run_rad_command(f"git commit -m \"{commit_msg}\"", cwd=temp_dir_for_creation)
            if not git_commit_success:
                self.logging.error(f"Git commit failed: {git_commit_stderr}")
                return None, None, f"Git commit failed: {git_commit_stderr}", temp_dir_for_creation

            _, commit_hash, _ = self.rad_utils.run_rad_command("git rev-parse HEAD", cwd=temp_dir_for_creation)
            if not commit_hash:
                self.logging.error("Failed to get commit hash after validator's initial commit.")
                return None, None, "Failed to get commit hash", temp_dir_for_creation

            # Radicle Init with pexpect (passphrase for key unlock)
            self.logging.debug(f"Running rad init for {repo_name} in {temp_dir_for_creation} using pexpect.")
            rad_init_command = f"rad init --name {repo_name} --description 'Validator test repo' --default-branch main --public --no-confirm"
            passphrase = self.rad_utils.config.radicle.get("passphrase", "<YOUR_RADICAL_PASSPHRASE>")

            try:
                # Using rad_utils helper for pexpect if available, or direct pexpect here
                # Assuming direct pexpect as per original structure:
                env = os.environ.copy()
                env['LANG'] = 'en_US.UTF-8'
                child = pexpect.spawn(rad_init_command, cwd=temp_dir_for_creation, encoding="utf-8", timeout=70, env=env)
                
                # More robust expect patterns
                patterns = [
                    r"(?i)passphrase.*:",    # Passphrase prompt
                    r"Project initialized",  # Success message (example, adjust if different)
                    r"Error:",              # Error
                    pexpect.EOF,
                    pexpect.TIMEOUT
                ]
                idx = child.expect(patterns)

                if idx == 0: # Passphrase
                    if passphrase == "<YOUR_RADICAL_PASSPHRASE>": self.logging.warning("Rad init: Using placeholder passphrase for pexpect."); 
                    child.sendline(passphrase)
                    # Expect success or EOF after sending passphrase
                    idx_after_pass = child.expect([patterns[1], patterns[2], pexpect.EOF], timeout=60)
                    if idx_after_pass == 0: self.logging.info("Rad init: Success after passphrase.")
                    elif idx_after_pass == 1: raise Exception(f"Rad init: Error after passphrase: {child.before}{child.buffer}")
                    else: self.logging.info("Rad init: EOF after passphrase, assuming success.")

                elif idx == 1: # Project initialized directly
                    self.logging.info("Rad init: Project initialized without passphrase prompt.")
                elif idx == 2: # Error
                    raise Exception(f"Rad init: Error: {child.before}{child.buffer}")
                elif idx == 3: # EOF
                     self.logging.warning(f"Rad init: Reached EOF unexpectedly. Output: {child.before}{child.buffer}")
                     # Check if RID was created anyway
                else: # Timeout
                    raise Exception(f"Rad init: Timeout. Output: {child.before}{child.buffer}")
                
                self.logging.debug(f"Rad init output (full): {child.before if hasattr(child, 'before') else ''}{child.buffer if hasattr(child, 'buffer') else ''}")

            except pexpect.exceptions.ExceptionPexpect as e_pexpect:
                self.logging.error(f"Radicle init via pexpect failed: {e_pexpect}")
                return None, None, f"Radicle init pexpect error: {e_pexpect}", temp_dir_for_creation
            except Exception as e_init: # Catch other exceptions during init
                self.logging.error(f"Radicle init general error: {e_init}")
                return None, None, f"Radicle init general error: {e_init}", temp_dir_for_creation


            time.sleep(2) # Allow filesystem/Radicle state to settle
            _, rid_stdout, rid_stderr = self.rad_utils.run_rad_command("rad inspect --rid", cwd=temp_dir_for_creation)
            repo_rid = rid_stdout.strip()
            if not repo_rid.startswith("rad:"):
                self.logging.error(f"Failed to get Radicle RID after init. Stdout: '{rid_stdout}', Stderr: '{rid_stderr}'")
                return None, None, f"Failed to get Radicle RID: {rid_stderr or 'No RID found'}", temp_dir_for_creation
            self.logging.info(f"Radicle project initialized by validator. RID: {repo_rid}")

            # Radicle Push
            # `rad push` might also require passphrase if the key used for project isn't default or unlocked
            # For simplicity, assuming `rad push` works if `rad init` with passphrase worked.
            # A more robust solution would also use pexpect for `rad push` if needed.
            push_success, push_stdout, push_stderr = self.rad_utils.run_rad_command("rad push --all", cwd=temp_dir_for_creation)
            if not push_success:
                self.logging.error(f"Validator: Radicle push failed for {repo_rid}: {push_stderr}. Stdout: {push_stdout}")
                return repo_rid, commit_hash, f"Radicle push failed: {push_stderr}", temp_dir_for_creation
            
            self.logging.info(f"Validator: Radicle project {repo_rid} (commit {commit_hash}) pushed successfully. Output: {push_stdout}")
            return repo_rid, commit_hash, None, temp_dir_for_creation

        except Exception as e:
            self.logging.error(f"Error in create_and_push_radicle_repo: {e}\n{traceback.format_exc()}")
            return None, None, str(e), temp_dir_for_creation if 'temp_dir_for_creation' in locals() else None
        finally:
            if 'temp_dir_for_creation' in locals() and os.path.exists(temp_dir_for_creation):
                try:
                    shutil.rmtree(temp_dir_for_creation)
                    self.logging.debug(f"Cleaned up validator's initial creation directory: {temp_dir_for_creation}")
                except Exception as e_clean:
                    self.logging.error(f"Error cleaning up initial creation directory {temp_dir_for_creation}: {e_clean}")


    def modify_and_push_changes(self, local_repo_path: str, repo_rid_for_logging: str) -> bool:
        if not os.path.isdir(os.path.join(local_repo_path, ".git")): 
            self.logging.error(f"Not a git repo: {local_repo_path} for modify_and_push_changes."); return False
        self.logging.info(f"Validator: Modifying and pushing changes in {local_repo_path} for RID {repo_rid_for_logging}.")
        try:
            readme_path = os.path.join(local_repo_path, "README.md")
            if not os.path.exists(readme_path): 
                # Create a new file if README.md doesn't exist, to ensure a change
                readme_path = os.path.join(local_repo_path, "validator_change_file.md")
                self.logging.info(f"README.md not found, creating {readme_path} for changes.")
            
            with open(readme_path, "a+") as f: f.write(f"\nValidator update: {uuid.uuid4()} at {time.time()}") # a+ to create if not exist
            
            self.rad_utils.run_rad_command("git add .", cwd=local_repo_path)
            commit_msg = f"Automated validator update {uuid.uuid4()} for {repo_rid_for_logging}"
            commit_success, _, stderr_commit = self.rad_utils.run_rad_command(f"git commit -m \"{commit_msg}\"", cwd=local_repo_path)
            if not commit_success and not ("nothing to commit" in stderr_commit.lower() or "no changes added" in stderr_commit.lower()):
                self.logging.error(f"Git commit failed in {local_repo_path}. Stderr: {stderr_commit}"); return False

            # `git push rad main` might need pexpect if identity used for repo requires passphrase for push
            # This is a major simplification. Realistically, you'd need pexpect for `git push rad main` too.
            push_success, stdout_push, stderr_push = self.rad_utils.run_rad_command("git push rad main", cwd=local_repo_path)
            if not push_success:
                self.logging.error(f"Failed to push changes from {local_repo_path}. Stdout: {stdout_push}, Stderr: {stderr_push}"); return False
            self.logging.info(f"Successfully pushed changes from {local_repo_path}. Stdout: {stdout_push}")
            return True
        except Exception as e:
            self.logging.error(f"Exception in modify_and_push_changes for {repo_rid_for_logging}: {e}\n{traceback.format_exc()}"); return False

    def create_branch_and_push(self, local_repo_path: str, repo_rid_for_logging: str) -> Tuple[bool, Optional[str]]:
        if not os.path.isdir(os.path.join(local_repo_path, ".git")): 
            self.logging.error(f"Not a git repo: {local_repo_path} for create_branch_and_push."); return False, None
        
        new_branch_name = f"val-feat-{uuid.uuid4().hex[:6]}"
        self.logging.info(f"Validator: Creating branch {new_branch_name} in {local_repo_path} for RID {repo_rid_for_logging}.")
        try:
            # Ensure we are on main and up-to-date before creating a new branch
            self.rad_utils.run_rad_command("git checkout main", cwd=local_repo_path)
            self.rad_utils.run_rad_command("git pull rad main --ff-only", cwd=local_repo_path) # Fast-forward only pull

            checkout_success, _, stderr_checkout = self.rad_utils.run_rad_command(f"git checkout -b {new_branch_name}", cwd=local_repo_path)
            if not checkout_success: 
                self.logging.error(f"Failed to create branch {new_branch_name}. Stderr: {stderr_checkout}"); return False, None
            
            change_file = os.path.join(local_repo_path, f"branch_{new_branch_name.replace('/','_')}.txt")
            with open(change_file, "w") as f: f.write(f"Content for branch {new_branch_name} {time.time()}")
            
            self.rad_utils.run_rad_command("git add .", cwd=local_repo_path)
            commit_success, _, stderr_commit = self.rad_utils.run_rad_command(f"git commit -m \"Changes on branch {new_branch_name}\"", cwd=local_repo_path)
            if not commit_success and "nothing to commit" not in stderr_commit.lower():
                 self.logging.error(f"Commit on branch {new_branch_name} failed. Stderr: {stderr_commit}"); return False, new_branch_name

            # Simplified push; may require pexpect like `git push rad main`
            push_success, stdout_push, stderr_push = self.rad_utils.run_rad_command(f"git push -u rad {new_branch_name}", cwd=local_repo_path)
            if not push_success:
                self.logging.error(f"Failed to push branch {new_branch_name}. Stdout: {stdout_push}, Stderr: {stderr_push}"); return False, new_branch_name
            self.logging.info(f"Successfully pushed branch {new_branch_name}. Stdout: {stdout_push}")
            return True, new_branch_name
        except Exception as e:
            self.logging.error(f"Exception creating branch for {repo_rid_for_logging}: {e}\n{traceback.format_exc()}"); return False, new_branch_name


    def create_issue(self, local_repo_path: str, repo_rid_for_logging: str) -> bool:
        if not os.path.isdir(os.path.join(local_repo_path, ".git")):
            self.logging.error(f"Not a git repo: {local_repo_path} for create_issue."); return False
        self.logging.info(f"Validator: Creating issue in {local_repo_path} for RID {repo_rid_for_logging}.")
        title = f"Val-Issue-{uuid.uuid4().hex[:6]}"
        desc = f"Automated test issue by validator for {repo_rid_for_logging} at {time.ctime()}."
        
        # `rad issue open` might require passphrase for identity if not unlocked
        # This is a simplification. Pexpect might be needed.
        cmd = f"rad issue open --title {shlex.quote(title)} --description {shlex.quote(desc)} --no-confirm"
        success, stdout, stderr = self.rad_utils.run_rad_command(cmd, cwd=local_repo_path)
        
        if not success or not ("✓ Synced" in stdout or "Issue" in stdout and "created" in stdout): # Check for sync or creation message
            self.logging.error(f"Failed to create issue. Success: {success}, Stdout: {stdout}, Stderr: {stderr}"); return False
        self.logging.info(f"Successfully created issue for {repo_rid_for_logging}. Output: {stdout}")
        return True


    def create_and_push_patch(self, local_repo_path: str, repo_rid_for_logging: str) -> Tuple[bool, Optional[str]]:
        if not os.path.isdir(os.path.join(local_repo_path, ".git")): 
            self.logging.error(f"Not a git repo: {local_repo_path} for create_and_push_patch."); return False, None
        
        feature_branch = f"val-patch-feat-{uuid.uuid4().hex[:6]}"
        patch_title = f"Patch for {feature_branch}" # Radicle uses patch title from commit msg or interactive
        
        self.logging.info(f"Validator: Creating patch from branch {feature_branch} in {local_repo_path} for RID {repo_rid_for_logging}.")
        try:
            self.rad_utils.run_rad_command(f"git checkout main", cwd=local_repo_path) 
            self.rad_utils.run_rad_command(f"git pull rad main --ff-only", cwd=local_repo_path)
            checkout_success, _, stderr_checkout = self.rad_utils.run_rad_command(f"git checkout -b {feature_branch}", cwd=local_repo_path)
            if not checkout_success: 
                self.logging.error(f"Failed to create feature branch {feature_branch} for patch. Stderr: {stderr_checkout}"); return False, None

            patch_file = os.path.join(local_repo_path, f"patch_{feature_branch}.md")
            with open(patch_file, "w") as f: f.write(f"# Patch contribution for {feature_branch}\nContent: {uuid.uuid4()}")
            
            self.rad_utils.run_rad_command("git add .", cwd=local_repo_path)
            commit_msg = f"Feature for patch: {patch_title}"
            commit_success, _, stderr_commit = self.rad_utils.run_rad_command(f"git commit -m \"{commit_msg}\"", cwd=local_repo_path)
            if not commit_success and "nothing to commit" not in stderr_commit.lower(): 
                self.logging.error(f"Commit for patch branch {feature_branch} failed. Stderr: {stderr_commit}"); return False, None
            
            # Simplified push; may require pexpect
            # `rad patch open` is the typical command, which internally does git push.
            # Using `rad patch open` is more idiomatic for Radicle.
            # It might be interactive for title/description if not provided.
            # Let's try to provide them.
            # rad patch open --title "My Patch Title" --description "Description of my patch."
            # The patch ref is typically the branch name or commit.
            
            # First, push the branch itself, as `rad patch open` might expect it on the remote.
            push_branch_success, _, stderr_push_branch = self.rad_utils.run_rad_command(f"git push -u rad {feature_branch}", cwd=local_repo_path)
            if not push_branch_success:
                 self.logging.error(f"Failed to push feature branch {feature_branch} for patch. Stderr: {stderr_push_branch}"); return False, None


            # Now, open the patch. This command also syncs.
            # Using current HEAD of the feature_branch as the revision for the patch.
            patch_open_cmd = f"rad patch open --title {shlex.quote(patch_title)} --description 'Automated patch from validator.' --no-confirm --push {feature_branch}"

            # This command can also require passphrase for identity. Simplified for now.
            patch_create_success, stdout_patch, stderr_patch = self.rad_utils.run_rad_command(patch_open_cmd, cwd=local_repo_path)
            
            if not patch_create_success or not ("✓ Patch" in stdout_patch and "opened" in stdout_patch):
                self.logging.error(f"Failed to create/push patch. Command: {patch_open_cmd}, Success: {patch_create_success}, Stdout: {stdout_patch}, Stderr: {stderr_patch}"); return False, None
            
            # Extract patch ID from stdout (example pattern, adjust to actual output)
            patch_id_match = re.search(r"✓ Patch\s+([a-zA-Z0-9]+)\s+opened", stdout_patch)
            created_patch_id = patch_id_match.group(1) if patch_id_match else f"unknown_patch_id_from_{feature_branch}"

            self.rad_utils.run_rad_command(f"git checkout main", cwd=local_repo_path) 
            self.logging.info(f"Successfully created and pushed patch ID {created_patch_id} (from branch {feature_branch}). Stdout: {stdout_patch}")
            return True, created_patch_id # Return the patch ID or a reference
        except Exception as e:
            self.logging.error(f"Exception creating patch for {repo_rid_for_logging}: {e}\n{traceback.format_exc()}"); return False, None

    def clone_repository_locally(self, repo_rid: str, miner_node_id: str) -> Dict[str, Any]: # Changed from internal to public
        if not repo_rid or not miner_node_id: return {"status":False,"dir":None, "error": "Missing RID or Miner Node ID"}
        
        base_clone_dir = "/tmp/validator_clones_transient" 
        os.makedirs(base_clone_dir, exist_ok=True)
        sanitized_rid_for_path = repo_rid.replace(":", "_").replace("/", "_") # Basic sanitization
        clone_target_dir = os.path.join(base_clone_dir, f"clone_{sanitized_rid_for_path}_{str(uuid.uuid4())[:8]}")
        
        self.logging.info(f"Validator: Transient clone of RID {repo_rid} from miner {miner_node_id} to {clone_target_dir}")
        
        # Radicle clone command. --no-follow prevents continuous seeding by the validator.
        # --seed specifies the peer to clone from.
        clone_cmd = f"rad clone {repo_rid} {clone_target_dir} --no-confirm --seed {miner_node_id} --no-follow"
        
        try:
            clone_success_flag, stdout, stderr = self.rad_utils.run_rad_command(clone_cmd)
            
            if clone_success_flag and os.path.exists(os.path.join(clone_target_dir, ".git")):
                self.logging.info(f"Validator: Transient clone SUCCESS to {clone_target_dir}.")
                return {"status": True, "dir": clone_target_dir, "error": None}
            else:
                err_msg = f"Clone failed. SuccessFlag: {clone_success_flag}, Stdout: '{stdout}', Stderr: '{stderr}'"
                self.logging.warning(f"Validator: Transient clone FAILED for RID {repo_rid} from {miner_node_id}. {err_msg}")
                # Cleanup failed clone attempt directory
                if os.path.exists(clone_target_dir):
                    try: shutil.rmtree(clone_target_dir)
                    except Exception as e_cl: self.logging.error(f"Error cleaning failed transient clone dir {clone_target_dir}: {e_cl}")
                return {"status": False, "dir": None, "error": err_msg}
        except Exception as e:
            err_msg = f"Transient clone EXCEPTION for {repo_rid}: {e}\n{traceback.format_exc()}"
            self.logging.error(f"Validator: {err_msg}")
            if 'clone_target_dir' in locals() and os.path.exists(clone_target_dir):
                try: shutil.rmtree(clone_target_dir)
                except Exception as e_cl: self.logging.error(f"Error cleaning transient clone dir {clone_target_dir} after exception: {e_cl}")
            return {"status": False, "dir": None, "error": str(e)}
