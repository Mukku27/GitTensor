# GitTensor

GitTensor is a decentralized GitHub-equivalent built as a subnet on the [BitTensor](https://bittensor.com) network. It allows developers to host, push, pull, and merge Git repositories in a fully decentralized, incentivized, and fault-tolerant manner — powered by miners and validators.

Built for the next generation of open-source collaboration, GitTensor removes central intermediaries while preserving the familiar workflows of GitHub.



## Structure

-   `gittensor/`: Contains the core logic.
    -   `protocol.py`: Defines the Bittensor synapse for communication.
    -   `base/`: Base classes for neurons (miner, validator).
    -   `utils/`: Utility functions, including Radicle CLI interactions and Bittensor configurations.
    -   `validator_logic/`: Specific validation sequences performed by the validator.
-   `neurons/`: Entry points for running miners and validators.


### TODO(Radicle)
- [x] Miner and  validator installation  and intialisation of the Radicle CLI
- [x] Miner running seed node
- [x] Validator creating repos and miner storing in the node
- [x]  Pushing chnages to the Existing Repositiry(need to be test with miner and validator in different machines in real time )
- [x]  Cloning from the repo from the Seed node of the miner(need to be test with miner and validator in different machines in real time )
- [x]  pulling from the repo
- [x]  Creating new branch (need to be test with miner and validator in different machines in real time ) and deletion happening through the repo deletion
- [x]  issues of the repo
- [x]  PR(patch in the  radicle)  of the repo
- [x]  deletion of the repo
---

### TODO(Bittensor)
- [x] Basic Miner working
- [x] Basic Validator Working
- [ ] Migrate this  three file codebase to  bigger codebase structure
- [ ] testing the migrated codebase 
- [ ] Emissions and incentives(not working now )

---
