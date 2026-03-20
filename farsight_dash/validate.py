"""Config validator — checks that a client_config.yaml is valid and data files exist."""

import os, sys

from .core import load_config, find_file


def validate(config_path, shared_dir=None):
    """Validate a client config file and its referenced data sources.
    Returns list of (level, message) tuples where level is 'error', 'warning', or 'ok'.
    """
    results = []

    # Load config
    try:
        config = load_config(config_path)
        results.append(('ok', f"Config loaded: {config['client_name']} ({config['client_slug']})"))
    except Exception as e:
        results.append(('error', f"Failed to load config: {e}"))
        return results

    if shared_dir is None:
        shared_dir = os.path.join(os.path.dirname(os.path.abspath(config_path)), 'shared_data')

    if not os.path.isdir(shared_dir):
        results.append(('error', f"shared_data directory not found: {shared_dir}"))
        return results
    results.append(('ok', f"shared_data: {shared_dir}"))

    # Check required identity fields
    for field in ['client_name', 'client_slug', 'password']:
        if config.get(field):
            results.append(('ok', f"{field}: {config[field]}"))
        else:
            results.append(('error', f"Missing required field: {field}"))

    # Check data sources
    sources = config.get('sources', {})

    # Sales Database
    sales_cfg = sources.get('sales_database')
    if sales_cfg:
        fp = find_file(sales_cfg.get('file_pattern', ''), [shared_dir])
        if fp:
            results.append(('ok', f"Sales Database: {os.path.basename(fp)}"))
            _check_columns(fp, sales_cfg, results, 'Sales Database')
        else:
            results.append(('error', f"Sales Database not found (pattern: {sales_cfg.get('file_pattern')})"))
    else:
        results.append(('error', "No sales_database config"))

    # SKU Master
    sku_cfg = sources.get('sku_master')
    if sku_cfg:
        fp = find_file(sku_cfg.get('file_pattern', ''), [shared_dir])
        if fp:
            results.append(('ok', f"SKU Master: {os.path.basename(fp)}"))
        else:
            results.append(('error', f"SKU Master not found (pattern: {sku_cfg.get('file_pattern')})"))
    else:
        results.append(('warning', "No sku_master config — SKU info will be limited"))

    # Demand Plan
    dp_cfg = sources.get('demand_plan')
    if dp_cfg:
        fp = find_file(dp_cfg.get('file_pattern', ''), [shared_dir])
        if fp:
            results.append(('ok', f"Demand Plan: {os.path.basename(fp)}"))
        else:
            results.append(('warning', f"Demand Plan not found (pattern: {dp_cfg.get('file_pattern')})"))
    elif config.get('tabs', {}).get('forecast'):
        results.append(('warning', "Forecast tab enabled but no demand_plan configured"))

    # DTC
    dtc_cfg = sources.get('dtc')
    if dtc_cfg:
        fp = find_file(dtc_cfg.get('file_pattern', ''), [shared_dir])
        if fp:
            results.append(('ok', f"DTC data: {os.path.basename(fp)}"))
        else:
            results.append(('warning', f"DTC file not found (pattern: {dtc_cfg.get('file_pattern')})"))
    elif config.get('tabs', {}).get('dtc'):
        results.append(('warning', "DTC tab enabled but no dtc config"))

    # Location
    loc_cfg = sources.get('location')
    if loc_cfg:
        fp = find_file(loc_cfg.get('file_pattern', ''), [shared_dir])
        if fp:
            results.append(('ok', f"Location data: {os.path.basename(fp)}"))
        else:
            results.append(('warning', f"Location file not found (pattern: {loc_cfg.get('file_pattern')})"))
    elif config.get('tabs', {}).get('doors'):
        results.append(('warning', "Doors tab enabled but no location config"))

    # Branding
    logo_file = config.get('branding', {}).get('logo_file')
    if logo_file:
        logo_path = os.path.join(shared_dir, 'Branding', logo_file)
        if os.path.exists(logo_path):
            results.append(('ok', f"Client logo: {logo_file}"))
        else:
            results.append(('warning', f"Client logo not found: {logo_path}"))

    # Tabs summary
    tabs = config.get('tabs', {})
    enabled = [k for k, v in tabs.items() if v]
    disabled = [k for k, v in tabs.items() if not v]
    results.append(('ok', f"Tabs enabled: {', '.join(enabled)}"))
    if disabled:
        results.append(('ok', f"Tabs disabled: {', '.join(disabled)}"))

    return results


def _check_columns(filepath, source_cfg, results, source_name):
    """Check that configured column names exist in the Excel file."""
    import pandas as pd
    sheet = source_cfg.get('sheet_name')
    cols_cfg = source_cfg.get('columns', {})

    try:
        df = pd.read_excel(filepath, sheet_name=sheet, nrows=5) if sheet else pd.read_excel(filepath, nrows=5)
        actual_cols = set(df.columns.tolist())

        missing = []
        for cfg_key, col_name in cols_cfg.items():
            if col_name and col_name not in actual_cols:
                missing.append(f"{cfg_key}='{col_name}'")

        if missing:
            results.append(('error', f"{source_name} missing columns: {', '.join(missing)}"))
            results.append(('warning', f"  Available columns: {', '.join(sorted(actual_cols))}"))
        else:
            mapped_count = sum(1 for v in cols_cfg.values() if v)
            results.append(('ok', f"{source_name}: all {mapped_count} column mappings valid"))
    except Exception as e:
        results.append(('warning', f"Could not verify {source_name} columns: {e}"))


def main():
    """CLI entry point: python3 -m farsight_dash.validate path/to/client_config.yaml"""
    if len(sys.argv) < 2:
        print("Usage: python3 -m farsight_dash.validate path/to/client_config.yaml")
        sys.exit(1)

    config_path = sys.argv[1]
    if not os.path.exists(config_path):
        print(f"ERROR: Config file not found: {config_path}")
        sys.exit(1)

    results = validate(config_path)

    print()
    print("=" * 60)
    print("  Config Validation Results")
    print("=" * 60)
    print()

    errors = 0
    warnings = 0
    for level, msg in results:
        if level == 'error':
            print(f"  ✗ {msg}")
            errors += 1
        elif level == 'warning':
            print(f"  ⚠ {msg}")
            warnings += 1
        else:
            print(f"  ✓ {msg}")

    print()
    if errors:
        print(f"  {errors} error(s), {warnings} warning(s) — fix errors before building")
        sys.exit(1)
    elif warnings:
        print(f"  {warnings} warning(s) — build will proceed but some features may be limited")
    else:
        print("  All checks passed!")


if __name__ == '__main__':
    main()
