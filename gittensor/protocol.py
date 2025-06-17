import typing
import bittensor as bt


class gittensor(bt.Synapse):
    """
    A simple dummy protocol that inherits from bt.Synapse.
    This protocol handles dummy request and response communication between
    the miner and the validator.

    Attributes:
    - dummy_input: An integer value representing the input request sent by the validator.
    - dummy_output: An optional integer value representing the response from the miner.
    """

    # Required request input, filled by the dendrite caller.
    dummy_input: int

    # Optional request output, filled by the axon responder.
    dummy_output: typing.Optional[int] = None

    def deserialize(self) -> "gittensor":
        """
        Deserialize the miner response.
        
        This method can be extended to perform additional post-processing
        on the segments if necessary. Here, it simply logs and returns self.
        
        Returns:
            gittensor: The deserialized synapse instance.
        """
        bt.logging.info(f"Deserializing gittensor for job_id: {self.job_id}")
        if self.segments is not None:
            bt.logging.debug(f"Segments: {self.segments}")
        return self