# SceneAPI

API for scene reconstruction.

This repository is currently a minimal Python package scaffold prepared for PyPI publishing.

## Installation

```bash
pip install SceneAPI
```

## Publishing

Publishing is handled by `.github/workflows/publish.yml` through PyPI Trusted Publishing.

Configure the PyPI trusted publisher with:

- Owner: `SceneAPI`
- Repository: `SceneAPI`
- Workflow name: `publish.yml`
- Environment name: `pypi`

Then publish a GitHub release for a version that has not already been uploaded to PyPI.
