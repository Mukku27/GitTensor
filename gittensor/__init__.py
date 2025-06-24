# Set version
__version__ = "0.1.0" # Initial version
version_split = __version__.split(".")
__spec_version__ = (
    (10000 * int(version_split[0]))
    + (100 * int(version_split[1]))
    + (1 * int(version_split[2]))
)

# Import submodules for easier access
from . import protocol
from . import base
from . import utils
from . import validator_logic

# Define a project root for easy access to resources if needed later
import os
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))