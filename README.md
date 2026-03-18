# npe2api

The REST API used by [napari-hub.org](https://napari-hub.org) to query plugins.

The endpoint for the API is: <https://api.napari.org>

## Endpoints

| Type             | Endpoint                                                             | Note                                             |
|------------------|----------------------------------------------------------------------|--------------------------------------------------|
| Plugin index     | [/api/plugins](https://api.napari.org/api/plugins)                   |                                                  |
| Summary info     | [/api/extended_summary](https://api.napari.org/api/extended_summary) |                                                  |
| PyPI information |                                                                      | For an individual plugin: /api/pypi/{pypi_name}  |
| Conda index      | [/api/conda](https://api.napari.org/api/conda)                       | For an individual plugin: /api/conda/{pypi_name} |
| Fetch errors     | [/errors.json](https://api.napari.org/errors.json)                   |                                                  |
| manifests        | For an individual manifest: /api/manifest/{plugin_name}              |                                                  |

## Development

### Prerequisites

- **[Conda/Miniconda](https://docs.conda.io/en/latest/miniconda.html)**
  - conda is required for the reindex script to fetch conda package metadata
  - you *can* run the rest of the pipeline locally without conda
- **Python 3.12+**
- **Node.js and npm** (required for pywrangler/wrangler)
  - Recommended: Install using [nvm (Node Version Manager)](https://github.com/nvm-sh/nvm)
  - pywrangler uses wrangler under the hood, which requires npm
  - used for local development of the API server, and deployment to Cloudflare Workers
- **Cloudflare account** with R2 and Workers enabled (for remote development/deployment)

### Setup

1. **Install conda/miniconda** if not already installed

2. **Clone the repository**
   ```bash
   git clone https://github.com/napari/npe2api.git
   cd npe2api
   ```

3. **Create environment and install dependencies**
   ```bash
   # Create conda environment
   conda create -n npe2api python=3.12
   conda activate npe2api

   # Install conda Python package (required for reindex script)
   conda install conda

   # Install package with pipeline dependencies
   pip install -e .[pipeline]
   ```

4. **Configure credentials** (for local testing)
   ```bash
   cp .env.example .env
   # Edit .env with your Cloudflare credentials
   ```

### Running the data pipeline

```bash
# Find plugins by classifier
python -m npe2api.find_by_classifier

# Fetch manifests
python -m npe2api.fetch_manifests

# Reindex and generate summary data
python -m npe2api.reindex

# Upload to R2
python -m npe2api.upload_to_r2 npe2api
```

### Fully local development

For testing the complete stack locally without connecting to remote Cloudflare resources:

```bash
# Install pywrangler (Python wrapper for wrangler, cloudflare's worker CLI)
pip install workers-py

# Generate a wrangler config for local development
python -m npe2api.wrangler_config --preview local

# Run the data pipeline (find plugins, fetch manifests, reindex)
python -m npe2api.find_by_classifier
python -m npe2api.fetch_manifests
python -m npe2api.reindex

# Upload to local R2 bucket
# IMPORTANT: The bucket name must match the bucket_name in your wrangler.jsonc
# (default is "npe2api" for production config, or "npe2api-<env_name>" for preview config)
python -m npe2api.upload_to_r2 npe2api-local --local

# Note: Local upload is slow (~5-10 minutes for full dataset) due to CLI overhead.
# This is an unfortunate known limitation of local R2 development.

# Run worker locally (connects to local R2 bucket)
pywrangler dev --local
```

The worker will be available at `http://localhost:8787`. The `--local` flag tells both the upload
script and pywrangler to use the local R2 bucket (stored in in `.wrangler/state/`) instead of
connecting to a remote Cloudflare R2 bucket.

### Deployment

The API is served by `worker.py`, a FastAPI application deployed to Cloudflare Workers. The worker handles:
- Name normalization for plugin manifest routes
- Dynamic shields.io badge generation
- Pass-through requests to R2 object storage for all API endpoints

For production deployment, the data pipeline modules generate JSON files that are uploaded to R2, and the worker serves them via the public API.

### Remote worker development

```bash
# Generate a wrangler config (wrangler.jsonc)
python -m npe2api.wrangler_config --preview \<env_name\>

# Run worker locally (connects to remote R2 bucket)
pywrangler dev

# Deploy code to Cloudflare Workers
# NOTE: this will deploy to prod or preview depending on the wrangler.jsonc
pywrangler deploy
```

## Contributing

If you are interested in contributing to this repo, see the [CONTRIBUTING.md](./CONTRIBUTING.md) page.

## Code of conduct

`npe2api` is maintained by the [napari](https://napari.org/) community. The `napari` community has a
[Code of Conduct](https://napari.org/dev/community/code_of_conduct.html) that should be honored by
everyone who participates in the `napari` community.
