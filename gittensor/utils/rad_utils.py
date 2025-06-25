import os
import time
import shlex
import shutil
import subprocess
import uuid
import pexpect # For Radicle passphrase
import re       # For pexpect patterns
from typing import Tuple, Optional, List
import bittensor as bt # For logging

def run_rad_command(command: str, suppress_error: bool = False, cwd: Optional[str] = None, timeout: int = 120) -> Tuple[bool, str, str]:
    """Executes a Radicle shell command and returns success, stdout, and stderr."""
    try:
        full_command = f"rad {command}" # Prepend 'rad'
        bt.logging.debug(f"Running Radicle command: {full_command} (cwd: {cwd})")
        process = subprocess.Popen(shlex.split(full_command), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=cwd)
        stdout, stderr = process.communicate(timeout=timeout)
        success = process.returncode == 0
        if not success and not suppress_error:
            bt.logging.error(f"Radicle command failed: {full_command}\nStderr: {stderr.strip()}\nStdout: {stdout.strip()}")
        return success, stdout.strip(), stderr.strip()
    except subprocess.TimeoutExpired:
        bt.logging.error(f"Radicle command timed out: {full_command}")
        if process.poll() is None: process.kill()
        return False, "", "Timeout expired"
    except Exception as e:
        bt.logging.error(f"Error running Radicle command {full_command}: {e}")
        return False, "", str(e)

def pexpect_rad_auth_if_needed(cwd: Optional[str], radicle_passphrase: Optional[str]):
    """
    Uses pexpect to handle 'rad auth' if it prompts for a passphrase.
    This is a simplified version; robust handling might need more specific expect patterns.
    """
    if not radicle_passphrase:
        bt.logging.warning("Radicle passphrase not provided for pexpect_rad_auth. Radicle operations might fail if locked.")
        return

    try:
        # Check if identity is already unlocked or if `rad auth` can proceed without passphrase
        # This is tricky. A simple 'rad self' might not trigger unlock.
        # For now, we'll assume if `rad init` or `rad push` needs it, pexpect will handle it there.
        # This function could be enhanced to explicitly try unlocking.
        bt.logging.debug(f"Pexpect: Assuming Radicle identity is available or will be unlocked by specific commands.")
    except Exception as e:
        bt.logging.error(f"Pexpect: Error during Radicle auth pre-check: {e}")


def pexpect_run_rad_command_with_passphrase(
    command_after_rad: str, # e.g., "init --name ..." or "node start"
    passphrase: Optional[str],
    cwd: Optional[str] = None,
    timeout: int = 70,
    expect_patterns: Optional[List[str]] = None, # For more complex interactions
    send_after_passphrase: Optional[List[str]] = None # Commands/input after passphrase
) -> Tuple[bool, str]:
    """
    Runs a Radicle command that might require a passphrase using pexpect.
    Returns (success_bool, combined_output_str).
    """
    if not passphrase:
        bt.logging.warning(f"Pexpect: No passphrase provided for 'rad {command_after_rad}'. Command may fail if identity is locked.")
        # Attempt to run without pexpect if no passphrase given, Radicle might not need it
        success, stdout, stderr = run_rad_command(command_after_rad, cwd=cwd, timeout=timeout)
        return success, stdout + "\n" + stderr

    full_command = f"rad {command_after_rad}"
    bt.logging.debug(f"Pexpect: Running '{full_command}' (cwd: {cwd})")
    output_buffer = ""
    try:
        child = pexpect.spawn(full_command, cwd=cwd, encoding="utf-8", timeout=timeout)
        
        # Simplified expect logic for passphrase
        # More robust would be a list of patterns.
        # Default patterns: passphrase prompt, EOF, TIMEOUT, common success/error indicators
        
        # Define common patterns
        # Order matters: more specific or frequent patterns first.
        # re.IGNORECASE can be useful for "Passphrase"
        patterns = [
            re.compile(r'(?i)passphrase[:\s]*$', re.MULTILINE), # Common passphrase prompts
            "✓", # Common success indicator
            "error:", # Common error indicator
            pexpect.EOF,
            pexpect.TIMEOUT,
        ]
        if expect_patterns: # Allow overriding default patterns
            patterns = expect_patterns
            
        index = child.expect(patterns)
        output_buffer += child.before + (child.after if isinstance(child.after, str) else "")

        if index == 0: # Passphrase prompt
            bt.logging.debug("Pexpect: Passphrase prompt detected. Sending passphrase.")
            child.sendline(passphrase)
            if send_after_passphrase:
                for item_to_send in send_after_passphrase:
                    # This part needs more complex expect logic for each send
                    child.sendline(item_to_send)
                    # child.expect(...) # Expect confirmation or next prompt
            child.expect(pexpect.EOF) # Wait for command to finish
            output_buffer += child.before
            # Check output for success indicators after passphrase
            if "error:" in output_buffer.lower() or "failed" in output_buffer.lower():
                 bt.logging.warning(f"Pexpect: Command 'rad {command_after_rad}' may have failed after passphrase. Output: {output_buffer}")
                 return False, output_buffer
            return True, output_buffer
        
        elif index == 1: # "✓" success indicator
            bt.logging.info(f"Pexpect: Command 'rad {command_after_rad}' likely succeeded (found '✓').")
            child.expect(pexpect.EOF)
            output_buffer += child.before
            return True, output_buffer

        elif index == 2: # "error:"
            bt.logging.error(f"Pexpect: Command 'rad {command_after_rad}' failed (found 'error:'). Output: {output_buffer}")
            child.expect(pexpect.EOF) # Consume rest of output
            output_buffer += child.before
            return False, output_buffer

        elif index == 3: # EOF
            bt.logging.warning(f"Pexpect: Reached EOF for 'rad {command_after_rad}'. Output: {output_buffer}. Assuming success if no error, but prompt might have been missed.")
            # Check output for errors if EOF is reached without specific success/failure patterns
            if "error:" in output_buffer.lower() or "failed" in output_buffer.lower():
                 return False, output_buffer
            return True, output_buffer # May be successful if no passphrase was needed

        elif index == 4: # Timeout
            bt.logging.error(f"Pexpect: Timeout for 'rad {command_after_rad}'. Output: {output_buffer}")
            return False, output_buffer
            
    except pexpect.exceptions.ExceptionPexpect as e:
        bt.logging.error(f"Pexpect: Exception for 'rad {command_after_rad}': {e}. Output so far: {output_buffer}")
        return False, output_buffer + str(e)
    except Exception as e_gen:
        bt.logging.error(f"Pexpect: Generic Exception for 'rad {command_after_rad}': {e_gen}. Output so far: {output_buffer}")
        return False, output_buffer + str(e_gen)
    
    return False, output_buffer # Default to failure