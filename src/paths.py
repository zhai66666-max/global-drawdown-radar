"""仓库路径常量。

三个被合并进来的旧项目原本各自用「文件层级数」推导根目录，
移动位置后会失效，这里统一从本文件推导，避免脆弱的 parent.parent 计数。
"""
from pathlib import Path

# src/paths.py -> src -> 仓库根
REPO_ROOT = Path(__file__).resolve().parent.parent

CONFIG_DIR = REPO_ROOT / "config"
DATA_DIR = REPO_ROOT / "data"
TEMPLATES_DIR = REPO_ROOT / "templates"

# 归档与状态文件
ARCHIVE_DIR = DATA_DIR / "archive"
DUCKDB_PATH = DATA_DIR / "market.duckdb"
RADAR_STATE_FILE = DATA_DIR / "radar_state.json"
NDX_CACHE_CSV = DATA_DIR / "ndx_cache.csv"
