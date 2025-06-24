# The MIT License (MIT)
# Copyright © 2023 Your Name/Org

# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the “Software”), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.

# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

import re
import os
import codecs
from os import path
from setuptools import setup, find_packages

def read_requirements(path_req):
    with open(path_req, "r") as f:
        requirements = f.read().splitlines()
        processed_requirements = []
        for req in requirements:
            if req.startswith("git+") or "@" in req:
                pkg_name = re.search(r"(#egg=)([\w\-_]+)", req)
                if pkg_name:
                    processed_requirements.append(pkg_name.group(2))
                else:
                    continue
            else:
                processed_requirements.append(req)
        return processed_requirements

requirements_list = read_requirements("requirements.txt")
here = path.abspath(path.dirname(__file__))

with open(path.join(here, "README.md"), encoding="utf-8") as f:
    long_description = f.read()

# loading version from __init__.py
with codecs.open(
    os.path.join(here, "gittensor/__init__.py"), encoding="utf-8"
) as init_file:
    version_match = re.search(
        r"^__version__ = ['\"]([^'\"]*)['\"]", init_file.read(), re.M
    )
    version_string = version_match.group(1)

setup(
    name="gittensor",
    version=version_string,
    description="GitTensor: A Decentralized Git Subnet on Bittensor using Radicle",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/Mukku27/GitTensor.git", 
    author="Tidal-flow", 
    packages=find_packages(exclude=["tests*"]), # Added exclude for tests
    include_package_data=True,
    author_email="vemulapallimukesh@gmail.com", 
    license="MIT",
    python_requires=">=3.9",
    install_requires=requirements_list,
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "Topic :: Software Development :: Version Control",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
    ],)