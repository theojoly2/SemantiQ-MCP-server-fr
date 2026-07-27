import yaml
import os
from pathlib import Path

def load_config():
    cwd = Path(os.getcwd())
    # First try current directory, then the project directory (for imports from other CWDs)
    candidates = [cwd / "config.yaml", Path(__file__).resolve().parent / "config.yaml"]
    for config_path in candidates:
        if config_path.exists():
            with open(config_path, "r") as file:
                return yaml.safe_load(file)
    # Fallback: return minimal config using project dir
    project_dir = Path(__file__).resolve().parent
    return {
        "file_paths": {
            "style_guide": str(project_dir / "resources/semantic_conventions/style_guide/style_guide.txt"),
            "style_guide_xls": str(project_dir / "resources/semantic_conventions/style_guide/SEMIC_Style_Guide.xlsx"),
        },
        "dir_paths": {
            "models": str(project_dir / "resources/semantic_model/models"),
        },
    }

config = load_config()
