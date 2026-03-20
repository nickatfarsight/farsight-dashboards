"""Core utilities shared across all dashboard modules."""

import os, json, math, base64
from datetime import date, datetime
from collections import defaultdict

import pandas as pd
import numpy as np
import yaml


# ─────────────────────────────────────────────────────────────
# Config loading
# ─────────────────────────────────────────────────────────────
def load_config(config_path):
    """Load and validate a client_config.yaml file."""
    with open(config_path, 'r', encoding='utf-8') as f:
        cfg = yaml.safe_load(f)

    # Ensure required top-level keys exist
    required = ['client_name', 'client_slug', 'password', 'sources', 'tabs']
    for k in required:
        if k not in cfg:
            raise ValueError(f"Missing required config key: '{k}'")

    # Set defaults
    cfg.setdefault('branding', {})
    cfg['branding'].setdefault('primary_color', '#4A90D9')
    cfg['branding'].setdefault('font_family', None)
    cfg['branding'].setdefault('font_files', None)
    cfg['branding'].setdefault('logo_file', None)

    cfg.setdefault('calendar', {})
    cfg['calendar'].setdefault('type', '445')
    cfg['calendar'].setdefault('current_year', datetime.now().year)

    cfg.setdefault('slack', {})
    cfg['slack'].setdefault('channel_name', '')
    cfg['slack'].setdefault('message_prefix', cfg['client_name'])

    cfg.setdefault('dashboard_url', '')

    return cfg


# ─────────────────────────────────────────────────────────────
# Numeric / string helpers
# ─────────────────────────────────────────────────────────────
def num(v):
    """Convert to number or return 0."""
    if v is None:
        return 0.0
    if isinstance(v, float) and (pd.isna(v) or math.isnan(v)):
        return 0.0
    if isinstance(v, (int, float, np.integer, np.floating)):
        return float(v)
    if isinstance(v, str):
        v = v.strip().replace(",", "").replace("$", "")
        try:
            return float(v)
        except ValueError:
            return 0.0
    return 0.0


def safe_str(v):
    """Convert to string, returning 'nan' for null/NaN values."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "nan"
    return str(v).strip()


# ─────────────────────────────────────────────────────────────
# File finding
# ─────────────────────────────────────────────────────────────
def find_file(name, dirs):
    """Find a file by exact or partial name. Searches subdirectories too."""
    all_dirs = []
    for d in dirs:
        all_dirs.append(d)
        if os.path.isdir(d):
            for sub in os.listdir(d):
                sub_path = os.path.join(d, sub)
                if os.path.isdir(sub_path) and not sub.startswith('_'):
                    all_dirs.append(sub_path)
    # Exact match first
    for d in all_dirs:
        fp = os.path.join(d, name)
        if os.path.isfile(fp):
            return fp
    # Partial match
    for d in all_dirs:
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            if name.lower() in f.lower():
                fp = os.path.join(d, f)
                if os.path.isfile(fp):
                    return fp
    return None


# ─────────────────────────────────────────────────────────────
# JSON encoder for dashboard data
# ─────────────────────────────────────────────────────────────
class DashEncoder(json.JSONEncoder):
    """Custom encoder: NaN→null, round floats."""
    def default(self, o):
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            v = float(o)
            if math.isnan(v) or math.isinf(v):
                return None
            return round(v, 2)
        if isinstance(o, np.bool_):
            return bool(o)
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        return super().default(o)

    def encode(self, o):
        return super().encode(self._clean(o))

    def _clean(self, o):
        if isinstance(o, float):
            if math.isnan(o) or math.isinf(o):
                return None
            return round(o, 2)
        if isinstance(o, (np.floating,)):
            v = float(o)
            if math.isnan(v) or math.isinf(v):
                return None
            return round(v, 2)
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, np.bool_):
            return bool(o)
        if isinstance(o, dict):
            return {k: self._clean(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [self._clean(v) for v in o]
        return o


# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────
MONTH_ABBR = {1: 'Jan', 2: 'Feb', 3: 'Mar', 4: 'Apr', 5: 'May', 6: 'Jun',
              7: 'Jul', 8: 'Aug', 9: 'Sep', 10: 'Oct', 11: 'Nov', 12: 'Dec'}

MONTH_NUM = {'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
             'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12}


# ─────────────────────────────────────────────────────────────
# Branding helpers
# ─────────────────────────────────────────────────────────────
def load_logo_b64(shared_dir, logo_file):
    """Load a logo file and return base64 string, or '' if not found."""
    if not logo_file:
        return ''
    path = os.path.join(shared_dir, 'Branding', logo_file)
    if not os.path.exists(path):
        # Try finding it anywhere in shared_data
        path = find_file(logo_file, [shared_dir])
    if path and os.path.exists(path):
        with open(path, 'rb') as f:
            return base64.b64encode(f.read()).decode()
    return ''


def build_font_css(shared_dir, font_config):
    """Build @font-face CSS from font config. Returns CSS string."""
    if not font_config:
        return ''
    font_family = font_config.get('family', 'Custom Font')
    font_files = font_config.get('files', [])
    if not font_files:
        return ''

    parts = []
    font_dir = os.path.join(shared_dir, 'Branding')
    for entry in font_files:
        fname = entry.get('file', '')
        weight = entry.get('weight', 400)
        style = entry.get('style', 'normal')
        fpath = os.path.join(font_dir, fname)
        if os.path.exists(fpath):
            with open(fpath, 'rb') as f:
                b64 = base64.b64encode(f.read()).decode('ascii')
            ext = os.path.splitext(fname)[1].lstrip('.')
            fmt_map = {'otf': 'opentype', 'ttf': 'truetype', 'woff': 'woff', 'woff2': 'woff2'}
            fmt = fmt_map.get(ext, 'opentype')
            parts.append(
                f"@font-face {{ font-family:'{font_family}'; font-weight:{weight}; "
                f"font-style:{style}; font-display:swap; "
                f"src:url(data:font/{ext};base64,{b64}) format('{fmt}'); }}"
            )
    return "\n".join(parts)


# ─────────────────────────────────────────────────────────────
# Formatting helpers (for Slack/email output)
# ─────────────────────────────────────────────────────────────
def fmt_dollar(v):
    """Format as $1,234"""
    return f"${v:,.0f}"


def fmtK(v):
    """Format as $XXK or $X.XM"""
    if abs(v) >= 1_000_000:
        return f"${v/1_000_000:,.1f}M"
    elif abs(v) >= 1_000:
        return f"${v/1_000:,.0f}K"
    else:
        return f"${v:,.0f}"


def pct_or_na(numerator, denominator):
    """Return (formatted_pct_string, css_color) or ('N/A', '#666')."""
    if abs(denominator) < 1:
        return "N/A", "#666"
    pct = (numerator - denominator) / abs(denominator) * 100
    color = "#5CB85C" if pct >= 0 else "#D9534F"
    return f"{pct:+.1f}%", color
