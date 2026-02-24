# Demo Data

Demo data has been moved to a separate repository to keep the main codebase small and fast.

## For Development

The application works fine without demo data - it will use whatever files you have in your `data/` directory.

## For Cloud Deployments (Render.com)

Demo data is now in a separate repository: **drawio-supersearch-demo-data**

### Option 1: Manual Setup (Recommended)
1. Clone/download the demo data repo separately
2. Place the contents in a `demo_data/` folder in your deployment
3. Run the deployment as normal

### Option 2: Download During Build
Update `render.yaml` build command to download demo data:

```yaml
buildCommand: |
  pip install -r requirements.txt &&
  cp settings.ini.example settings.ini &&
  curl -L https://github.com/YOUR_USERNAME/drawio-supersearch-demo-data/archive/refs/heads/main.zip -o demo.zip &&
  unzip demo.zip &&
  mv drawio-supersearch-demo-data-main demo_data &&
  python scripts/use_demo_data.py &&
  python scripts/index.py --rebuild
```

### Option 3: Skip Demo Data
If you don't need demo data, remove the `python scripts/use_demo_data.py` step from `render.yaml`.

## Creating the Separate Demo Data Repo

Instructions are in `demo_data_backup/README.md`
