"""配置加载：YAML 配置文件统一入口"""
import os

import yaml

from src.paths import CONFIG_DIR

# 合并后配置文件统一放在仓库根的 config/（原先在 <repo>/config，移动位置后原推导会失效）
BASE_DIR = str(CONFIG_DIR.parent)


def load_yaml(filename):
    path = os.path.join(BASE_DIR, 'config', filename)
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def load_etfs():
    """ETF 监控名单"""
    return load_yaml('etfs.yaml')


def load_strategy():
    """策略配置"""
    return load_yaml('strategy.yaml')
