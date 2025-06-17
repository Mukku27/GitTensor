# gittensor

A Python project using Poetry for dependency management.

## Getting Started with Poetry

### Prerequisites

- Python 3.10 or higher
- Poetry (Python package manager)

### Installing Poetry

If you don't have Poetry installed, you can install it using one of these methods:

**Option 1: Official installer (Recommended)**
```bash
curl -sSL https://install.python-poetry.org | python3 -
```

**Option 2: Using pip**
```bash
pip install poetry
```

**Option 3: Using Homebrew (macOS)**
```bash
brew install poetry
```

### Project Setup

1. **Clone the repository** (if applicable):
   ```bash
   git clone <repository-url>
   cd gittensor
   ```

2. **Install dependencies**:
   ```bash
   poetry install
   ```
   This will create a virtual environment and install all dependencies listed in `pyproject.toml`.

3. **Activate the virtual environment**:
   ```bash
   poetry shell
   ```
   Or run commands within the environment using `poetry run`:
   ```bash
   poetry run python your_script.py
   ```

## Common Poetry Commands

### Dependency Management

**Add a new dependency:**
```bash
# Add a runtime dependency
poetry add requests numpy pandas

# Add a development dependency
poetry add --group dev pytest black flake8

# Add a dependency with version constraints
poetry add "django>=4.0,<5.0"
```

**Remove a dependency:**
```bash
poetry remove requests
```

**Update dependencies:**
```bash
# Update all dependencies
poetry update

# Update specific dependency
poetry update requests
```

**View installed packages:**
```bash
poetry show
poetry show --tree  # Show dependency tree
```

### Virtual Environment Management

**Create/activate virtual environment:**
```bash
poetry shell
```

**Run commands in the virtual environment:**
```bash
poetry run python script.py
poetry run pytest
poetry run black .
```

**Show virtual environment path:**
```bash
poetry env info --path
```

**Remove virtual environment:**
```bash
poetry env remove python
```

### Building and Publishing

**Build the project:**
```bash
poetry build
```

**Publish to PyPI:**
```bash
poetry publish
```

**Publish to test PyPI:**
```bash
poetry publish --repository testpypi
```

## Development Workflow

### Setting up Development Environment

1. Install the project with development dependencies:
   ```bash
   poetry install --with dev
   ```

2. Common development dependencies you might want to add:
   ```bash
   poetry add --group dev pytest pytest-cov black isort flake8 mypy
   ```

### Running Tests

```bash
# Run tests
poetry run pytest

# Run tests with coverage
poetry run pytest --cov=gittensor

# Run specific test file
poetry run pytest tests/test_example.py
```

### Code Formatting and Linting

```bash
# Format code with Black
poetry run black .

# Sort imports with isort
poetry run isort .

# Lint with flake8
poetry run flake8 .

# Type checking with mypy
poetry run mypy gittensor/
```

## Project Structure

```
gittensor/
├── gittensor/          # Main package directory
│   └── __init__.py
├── tests/               # Test directory
│   └── __init__.py
├── pyproject.toml       # Project configuration and dependencies
└── README.md           # This file
```

## Configuration

The `pyproject.toml` file contains all project configuration:

- **Project metadata**: name, version, description, authors
- **Dependencies**: runtime and development dependencies
- **Build system**: Poetry configuration
- **Tool configurations**: for linters, formatters, etc.

### Example pyproject.toml sections:

```toml
[project]
dependencies = [
    "requests",
    "numpy>=1.20.0",
]

[tool.poetry.group.dev.dependencies]
pytest = "^7.0.0"
black = "^22.0.0"
flake8 = "^4.0.0"

[tool.black]
line-length = 88
target-version = ['py310']

[tool.isort]
profile = "black"
```

## Useful Tips

1. **Lock file**: `poetry.lock` ensures reproducible installs. Commit this file to version control.

2. **Environment variables**: Use `.env` files for environment-specific configuration.

3. **Scripts**: Define custom scripts in `pyproject.toml`:
   ```toml
   [tool.poetry.scripts]
   gittensor = "gittensor.main:main"
   ```

4. **Export requirements**: Generate requirements.txt if needed:
   ```bash
   poetry export -f requirements.txt --output requirements.txt
   ```

## Troubleshooting

**Clear Poetry cache:**
```bash
poetry cache clear --all pypi
```

**Reinstall dependencies:**
```bash
poetry install --sync
```

**Check Poetry configuration:**
```bash
poetry config --list
```

For more information, visit the [Poetry documentation](https://python-poetry.org/docs/).
