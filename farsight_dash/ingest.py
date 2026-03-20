"""Config-driven data readers for sell-through dashboard."""

import os, time
from collections import defaultdict

import pandas as pd
import numpy as np

from .core import num, safe_str, find_file, MONTH_ABBR, MONTH_NUM


# ─────────────────────────────────────────────────────────────
# Column mapper: translates config column names to canonical names
# ─────────────────────────────────────────────────────────────
def _col(cfg_columns, canonical_name):
    """Get the actual column name from config mapping. Returns None if not mapped."""
    return cfg_columns.get(canonical_name)


def _get_col(df, cfg_columns, canonical_name, default=None):
    """Get a column from a dataframe using config mapping. Returns Series or default."""
    col_name = _col(cfg_columns, canonical_name)
    if col_name and col_name in df.columns:
        return df[col_name]
    return default


# ─────────────────────────────────────────────────────────────
# 1. Read SKU Master
# ─────────────────────────────────────────────────────────────
def read_sku_master(config, shared_dir):
    """Read SKU master from demand plan or standalone file.
    Returns dict: sku_int → {sku, product, franchise, category, sub_category,
                              status_current_year, srp, active, is_new}
    """
    src = config['sources'].get('sku_master')
    if not src:
        print("  No SKU master configured — skipping")
        return {}

    cols = src.get('columns', {})
    active_value = src.get('active_value', 'A')
    new_value = src.get('new_value', 'New')
    disco_value = src.get('disco_value', 'Disco')

    print("Reading SKU master...")
    t = time.time()

    fp = find_file(src['file_pattern'], [shared_dir])
    if not fp:
        print(f"  WARNING: SKU master file not found (pattern: {src['file_pattern']})")
        return {}

    df = pd.read_excel(fp, sheet_name=src.get('sheet_name', 'Sku List'))

    sku_info = {}
    sku_col = _col(cols, 'sku')
    if not sku_col or sku_col not in df.columns:
        print(f"  WARNING: SKU column '{sku_col}' not found in sheet")
        return {}

    for _, row in df.iterrows():
        sku_val = row.get(sku_col)
        if pd.isna(sku_val):
            continue
        try:
            sku = int(float(sku_val))
        except (ValueError, TypeError):
            continue

        srp = num(row.get(_col(cols, 'srp'), 0))

        # Determine active status
        active_flag_col = _col(cols, 'active_flag')
        status_col = _col(cols, 'status_current_year')

        active = 'A'
        if active_flag_col and active_flag_col in df.columns:
            flag_val = safe_str(row.get(active_flag_col))
            if flag_val in ('0', 'N', 'No', '', 'nan'):
                active = 'D'
            elif flag_val == active_value:
                active = 'A'

        status_val = safe_str(row.get(status_col)) if status_col else ''
        if status_val and disco_value.lower() in status_val.lower():
            active = 'D'

        is_new = (status_val == new_value) if status_val else False

        sku_info[sku] = {
            'sku': sku,
            'product': safe_str(row.get(_col(cols, 'product'), '')),
            'franchise': safe_str(row.get(_col(cols, 'franchise'), '')) if _col(cols, 'franchise') else 'nan',
            'category': safe_str(row.get(_col(cols, 'category'), '')),
            'sub_category': safe_str(row.get(_col(cols, 'sub_category'), '')) if _col(cols, 'sub_category') else 'nan',
            'status_current_year': status_val,
            'srp': srp,
            'active': active,
            'is_new': is_new,
        }

    print(f"  {len(sku_info)} SKUs loaded ({time.time()-t:.1f}s)")
    return sku_info


# ─────────────────────────────────────────────────────────────
# 2. Read Sales Database
# ─────────────────────────────────────────────────────────────
def read_sales_database(config, shared_dir):
    """Read the weekly sales database.
    Returns: (sales_df, week_date_map, week_month_map, meta_dict)
    where meta_dict = {current_week, current_month_445, current_week_end, all_weeks, l4w_weeks, retailers_needing_ly}
    """
    src = config['sources']['sales_database']
    cols = src.get('columns', {})
    current_year = config['calendar']['current_year']
    use_445 = config['calendar']['type'] == '445'

    print("Reading Sales Database...")
    t = time.time()

    fp = find_file(src['file_pattern'], [shared_dir])
    if not fp:
        raise FileNotFoundError(f"Sales Database not found (pattern: {src['file_pattern']})")

    df = pd.read_excel(fp, sheet_name=src.get('sheet_name', 'Sales Database'))
    print(f"  {len(df):,} rows loaded ({time.time()-t:.1f}s)")

    # Map config column names to canonical names for internal use
    ic_col = _col(cols, 'item_code') or 'Item Code'
    yr_col = _col(cols, 'year') or 'Year'
    wk_col = _col(cols, 'week') or 'Week'
    we_col = _col(cols, 'week_end_date') or 'Week End Date'
    mo_col = _col(cols, 'month_445') if use_445 else None
    ret_col = _col(cols, 'retailer') or 'Retailer'

    # Clean Item Code
    df = df.dropna(subset=[ic_col])
    df[ic_col] = pd.to_numeric(df[ic_col], errors='coerce')
    df = df.dropna(subset=[ic_col])
    df[ic_col] = df[ic_col].astype(int)
    df[yr_col] = df[yr_col].astype(int)
    df[wk_col] = df[wk_col].astype(int)

    # Rename columns to canonical names for downstream code
    rename_map = {
        ic_col: 'Item Code',
        yr_col: 'Year',
        wk_col: 'Week',
        we_col: 'Week End Date',
        ret_col: 'Retailer',
    }

    # Sales columns
    sales_cols = {
        'ty_total_dollars': 'TY Total Sales $',
        'ty_bm_dollars': 'TY B&M Sales $',
        'ty_dc_dollars': 'TY Dotcom Sales $',
        'ty_total_units': 'TY Total Sales Units',
        'ly_total_dollars': 'LY Total Sales $',
        'ly_bm_dollars': 'LY B&M Sales $',
        'ly_dc_dollars': 'LY Dotcom Sales $',
    }

    for cfg_key, canonical in sales_cols.items():
        src_col = _col(cols, cfg_key)
        if src_col and src_col in df.columns:
            rename_map[src_col] = canonical
        elif canonical not in df.columns:
            # Create zero column if not available (e.g., no B&M/Dotcom split)
            df[canonical] = 0.0

    # 445 Month column
    if mo_col and mo_col in df.columns:
        rename_map[mo_col] = '445 Month'
    elif '445 Month' not in df.columns:
        # Derive month from Week End Date
        df['445 Month'] = pd.to_datetime(df[we_col], errors='coerce').dt.month.map(MONTH_ABBR)

    # Apply renames (skip identity renames)
    actual_renames = {k: v for k, v in rename_map.items() if k != v and k in df.columns}
    df = df.rename(columns=actual_renames)

    # Ensure numeric sales columns
    for canonical in sales_cols.values():
        if canonical in df.columns:
            df[canonical] = pd.to_numeric(df[canonical], errors='coerce').fillna(0.0)

    # If no channel split, derive total from B&M + Dotcom or vice versa
    if df['TY B&M Sales $'].sum() == 0 and df['TY Dotcom Sales $'].sum() == 0:
        # No channel split — put everything in total, zero out channels
        pass  # already zero
    elif 'TY Total Sales $' not in df.columns or df['TY Total Sales $'].sum() == 0:
        df['TY Total Sales $'] = df['TY B&M Sales $'] + df['TY Dotcom Sales $']

    # Compute meta from current year data
    cy_data = df[df['Year'] == current_year]
    if cy_data.empty:
        raise ValueError(f"No data found for year {current_year}")

    current_week = int(cy_data['Week'].max())
    latest_row = cy_data[cy_data['Week'] == current_week].iloc[0]
    current_week_end = pd.to_datetime(latest_row['Week End Date'])
    current_month_str = MONTH_ABBR.get(current_week_end.month, 'Jan')

    current_month_445 = latest_row.get('445 Month', current_month_str)
    if pd.isna(current_month_445) or str(current_month_445) == 'nan':
        current_month_445 = current_month_str

    all_weeks = sorted(cy_data['Week'].unique().tolist())
    l4w_weeks = all_weeks[-4:] if len(all_weeks) >= 4 else all_weeks

    # Build week → month and week → date maps
    week_month_map = {}
    week_date_map = {}
    for _, r in df[['Year', 'Week', '445 Month', 'Week End Date']].drop_duplicates().iterrows():
        yr = int(r['Year'])
        wk = int(r['Week'])
        mo = r['445 Month']
        we = pd.to_datetime(r['Week End Date'])
        if pd.isna(mo) or str(mo) == 'nan':
            mo = MONTH_ABBR.get(we.month, '')
        if (yr, wk) not in week_month_map or str(week_month_map.get((yr, wk))) == 'nan':
            week_month_map[(yr, wk)] = mo
        week_date_map[(yr, wk)] = we

    current_month_num = MONTH_NUM.get(current_month_445, current_week_end.month)

    print(f"  Current week: {current_week}, month: {current_month_445}, week end: {current_week_end.strftime('%m.%d.%Y')}")

    meta = {
        'current_year': current_year,
        'current_week': current_week,
        'current_month_445': current_month_445,
        'current_month_num': current_month_num,
        'current_week_end': current_week_end,
        'all_weeks': all_weeks,
        'l4w_weeks': l4w_weeks,
    }

    return df, week_date_map, week_month_map, meta


# ─────────────────────────────────────────────────────────────
# 2b. Integrate DTC data (Shopify, etc.)
# ─────────────────────────────────────────────────────────────
def read_dtc_data(config, shared_dir, week_date_map, week_month_map, sku_info):
    """Read DTC platform data and return rows in Sales Database format.
    Returns DataFrame or None.
    """
    dtc_cfg = config['sources'].get('dtc')
    if not dtc_cfg:
        return None

    current_year = config['calendar']['current_year']
    platform = dtc_cfg.get('platform', 'shopify')
    retailer_name = dtc_cfg.get('retailer_name', 'eCommerce')

    print(f"Reading DTC data ({platform})...")
    t = time.time()

    fp = find_file(dtc_cfg['file_pattern'], [shared_dir])
    if not fp:
        print(f"  WARNING: DTC file not found (pattern: {dtc_cfg['file_pattern']})")
        return None

    if platform == 'shopify':
        return _read_shopify(fp, dtc_cfg, shared_dir, week_date_map, week_month_map,
                            sku_info, retailer_name, current_year)
    else:
        print(f"  WARNING: Unsupported DTC platform: {platform}")
        return None


def _read_shopify(fp, dtc_cfg, shared_dir, week_date_map, week_month_map,
                  sku_info, retailer_name, current_year):
    """Read Shopify CSV files and return DataFrame in Sales DB format."""
    shopify_df = pd.read_csv(fp)
    print(f"  Shopify CSV: {len(shopify_df):,} daily rows")

    shopify_df['Day'] = pd.to_datetime(shopify_df['Day'], errors='coerce')
    shopify_df = shopify_df.dropna(subset=['Day'])

    # Parse SKU
    shopify_df['Item Code'] = pd.to_numeric(shopify_df['Product variant SKU'], errors='coerce')
    bundle_mask = shopify_df['Item Code'].isna()
    shopify_df.loc[bundle_mask, 'Item Code'] = 0
    shopify_df['Item Code'] = shopify_df['Item Code'].astype(int)

    shopify_df['Net items sold'] = pd.to_numeric(shopify_df['Net items sold'], errors='coerce').fillna(0)
    shopify_df['Gross sales'] = pd.to_numeric(shopify_df['Gross sales'], errors='coerce').fillna(0)

    # Map days to fiscal weeks
    _week_ends_sorted = sorted([(we, yr, wk) for (yr, wk), we in week_date_map.items()])

    def _day_to_fiscal_week(day):
        for we, yr, wk in _week_ends_sorted:
            if day <= we:
                return (yr, wk)
        return None

    shopify_df['_fiscal'] = shopify_df['Day'].apply(_day_to_fiscal_week)
    shopify_df = shopify_df.dropna(subset=['_fiscal'])
    shopify_df['Year'] = shopify_df['_fiscal'].apply(lambda x: x[0])
    shopify_df['Week'] = shopify_df['_fiscal'].apply(lambda x: x[1])

    # Aggregate to weekly
    shopify_weekly = shopify_df.groupby(['Item Code', 'Year', 'Week']).agg({
        'Net items sold': 'sum',
        'Gross sales': 'sum',
    }).reset_index()

    # Read LY file if available
    ly_lookup = {}
    ly_pattern = dtc_cfg.get('ly_file_pattern')
    if ly_pattern:
        ly_fp = find_file(ly_pattern, [shared_dir])
        if ly_fp:
            print(f"  Reading LY Shopify data...")
            ly_year = current_year - 1
            shopify_ly_df = pd.read_csv(ly_fp)
            shopify_ly_df['Day'] = pd.to_datetime(shopify_ly_df['Day'], errors='coerce')
            shopify_ly_df = shopify_ly_df.dropna(subset=['Day'])
            shopify_ly_df = shopify_ly_df[(shopify_ly_df['Day'] >= pd.Timestamp(ly_year, 1, 1)) &
                                           (shopify_ly_df['Day'] <= pd.Timestamp(ly_year, 12, 31))]

            shopify_ly_df['Item Code'] = pd.to_numeric(shopify_ly_df['Product variant SKU'], errors='coerce')
            ly_bundle_mask = shopify_ly_df['Item Code'].isna()
            shopify_ly_df.loc[ly_bundle_mask, 'Item Code'] = 0
            shopify_ly_df['Item Code'] = shopify_ly_df['Item Code'].astype(int)
            shopify_ly_df['Net items sold'] = pd.to_numeric(shopify_ly_df['Net items sold'], errors='coerce').fillna(0)
            shopify_ly_df['Gross sales'] = pd.to_numeric(shopify_ly_df['Gross sales'], errors='coerce').fillna(0)

            shopify_ly_df['_fiscal'] = shopify_ly_df['Day'].apply(_day_to_fiscal_week)
            shopify_ly_df = shopify_ly_df.dropna(subset=['_fiscal'])
            shopify_ly_df['Year'] = shopify_ly_df['_fiscal'].apply(lambda x: x[0])
            shopify_ly_df['Week'] = shopify_ly_df['_fiscal'].apply(lambda x: x[1])
            shopify_ly_df = shopify_ly_df[shopify_ly_df['Year'] == ly_year]

            shopify_ly_weekly = shopify_ly_df.groupby(['Item Code', 'Week']).agg({
                'Net items sold': 'sum',
                'Gross sales': 'sum',
            }).reset_index()

            for _, r in shopify_ly_weekly.iterrows():
                ly_lookup[(int(r['Item Code']), int(r['Week']))] = (
                    r['Net items sold'], r['Gross sales']
                )
            print(f"    Built LY lookup: {len(ly_lookup):,} (SKU, week) entries")

    # Build rows in Sales DB format
    rows = []
    for _, r in shopify_weekly.iterrows():
        ic = int(r['Item Code'])
        yr = int(r['Year'])
        wk = int(r['Week'])
        units = r['Net items sold']
        dollars = r['Gross sales']
        mo = week_month_map.get((yr, wk), '')
        we = week_date_map.get((yr, wk))

        ly_dollars = 0.0
        if yr == current_year and ly_lookup:
            ly_data = ly_lookup.get((ic, wk))
            if ly_data:
                _, ly_dollars = ly_data

        rows.append({
            'Item Code': ic, 'Year': yr, 'Week': wk,
            '445 Month': mo, 'Week End Date': we if we is not None else pd.NaT,
            'Retailer': retailer_name,
            'TY Total Sales $': dollars, 'TY Total Sales Units': units,
            'TY B&M Sales $': 0.0, 'TY Dotcom Sales $': dollars,
            'TY B&M Sales Units': 0.0, 'TY Dotcom Sales Units': units,
            'LY B&M Sales $': 0.0, 'LY Dotcom Sales $': ly_dollars,
            'LY Total Sales $': ly_dollars,
        })

    # LY-only rows (Year=LY_YEAR)
    ly_year = current_year - 1
    for (ic, wk), (lu, ld) in ly_lookup.items():
        mo = week_month_map.get((ly_year, wk), '')
        we = week_date_map.get((ly_year, wk))
        rows.append({
            'Item Code': ic, 'Year': ly_year, 'Week': wk,
            '445 Month': mo, 'Week End Date': we if we is not None else pd.NaT,
            'Retailer': retailer_name,
            'TY Total Sales $': ld, 'TY Total Sales Units': lu,
            'TY B&M Sales $': 0.0, 'TY Dotcom Sales $': ld,
            'TY B&M Sales Units': 0.0, 'TY Dotcom Sales Units': lu,
            'LY B&M Sales $': 0.0, 'LY Dotcom Sales $': 0.0,
            'LY Total Sales $': 0.0,
        })

    if rows:
        result = pd.DataFrame(rows)
        for c in ['TY B&M Sales $', 'TY Dotcom Sales $', 'TY Total Sales $',
                   'TY B&M Sales Units', 'TY Dotcom Sales Units', 'TY Total Sales Units',
                   'LY B&M Sales $', 'LY Dotcom Sales $', 'LY Total Sales $']:
            if c in result.columns:
                result[c] = pd.to_numeric(result[c], errors='coerce').fillna(0.0)
        print(f"  {len(rows):,} DTC weekly rows generated")
        return result

    return None


def merge_dtc(sales_df, dtc_df, config):
    """Merge DTC data into sales DataFrame, replacing existing DTC retailer rows."""
    retailer_name = config['sources']['dtc']['retailer_name']
    before = len(sales_df[sales_df['Retailer'] == retailer_name])
    sales_df = sales_df[sales_df['Retailer'] != retailer_name]
    print(f"  Removed {before:,} existing {retailer_name} rows, appending {len(dtc_df):,} DTC rows")
    return pd.concat([sales_df, dtc_df], ignore_index=True)


# ─────────────────────────────────────────────────────────────
# 3. Read Demand Plan Forecasts
# ─────────────────────────────────────────────────────────────
def read_demand_plan(config, shared_dir, week_date_map, sku_info):
    """Read forecast sheets from demand plan.
    Returns: (forecast_data, forecast_is_dollars, forecast_data_bm, forecast_data_dc, srp_map)
    """
    dp_cfg = config['sources'].get('demand_plan')
    if not dp_cfg:
        print("  No demand plan configured — skipping forecasts")
        return {}, {}, {}, {}, {}

    current_year = config['calendar']['current_year']
    retailer_sheets = dp_cfg.get('retailer_sheets', {})
    # Channel split config — which retailer has B&M/Dotcom split in forecast
    channel_split_retailer = dp_cfg.get('channel_split_retailer')  # e.g., 'Sephora'
    # Sheets without channel column
    no_channel_sheets = dp_cfg.get('no_channel_sheets', [])

    print("Reading demand plan forecasts...")
    t = time.time()

    fp = find_file(dp_cfg['file_pattern'], [shared_dir])
    if not fp:
        print(f"  WARNING: Demand plan not found (pattern: {dp_cfg['file_pattern']})")
        return {}, {}, {}, {}, {}

    # Build date → week number map
    date_to_week = {}
    for (yr, wk), we in week_date_map.items():
        if yr == current_year:
            date_to_week[we.strftime('%Y-%m-%d')] = wk

    forecast_data = {}
    forecast_is_dollars = {}
    forecast_data_bm = {}
    forecast_data_dc = {}
    srp_map = {sku: info['srp'] for sku, info in sku_info.items()}

    for sheet_name, retailer_name in retailer_sheets.items():
        print(f"  Reading {sheet_name} sheet...")
        try:
            raw = pd.read_excel(fp, sheet_name=sheet_name, header=None)
        except Exception as e:
            print(f"    WARNING: Could not read {sheet_name}: {e}")
            continue

        # Auto-detect header row (find "SKU")
        header_row = None
        sku_col = None
        for i in range(min(20, len(raw))):
            for j in range(min(10, raw.shape[1])):
                val = raw.iloc[i, j]
                if isinstance(val, str) and val.strip().upper() == 'SKU':
                    header_row = i
                    sku_col = j
                    break
            if header_row is not None:
                break

        if header_row is None:
            print(f"    WARNING: Could not find SKU header in {sheet_name}")
            continue

        header = raw.iloc[header_row]

        # Find date column blocks
        all_blocks = []
        cur_block = []
        for i in range(raw.shape[1]):
            val = header.iloc[i]
            yr_str = str(current_year)[-2:]  # e.g., '26'
            if isinstance(val, str) and 'TTL' in val.upper() and yr_str in val:
                if cur_block:
                    all_blocks.append((list(cur_block), val.strip()))
                    cur_block = []
            elif hasattr(val, 'year') and val.year == current_year:
                date_str = val.strftime('%Y-%m-%d')
                wk_num = date_to_week.get(date_str, len(cur_block) + 1)
                cur_block.append((i, val, wk_num))
        if cur_block:
            all_blocks.append((list(cur_block), '(end)'))

        # Prefer dollar block
        dollar_block = None
        units_block = None
        for block_cols, ttl_label in all_blocks:
            if '$' in ttl_label:
                dollar_block = block_cols
            else:
                units_block = block_cols

        is_dollar_forecast = False
        if dollar_block:
            date_cols = dollar_block
            is_dollar_forecast = True
        elif units_block:
            date_cols = units_block
        elif all_blocks:
            date_cols = all_blocks[0][0]
        else:
            date_cols = []
            print(f"    WARNING: No {current_year} date columns found in {sheet_name}")

        if date_cols:
            print(f"    {len(date_cols)} week columns ({'dollars' if is_dollar_forecast else 'units'})")

        # Read data
        data_start = header_row + 2
        fc = {}
        fc_bm = {}
        fc_dc = {}

        has_channel = sheet_name not in no_channel_sheets
        channel_col = sku_col - 1 if has_channel else None

        for r in range(data_start, len(raw)):
            sku_val = raw.iloc[r, sku_col]
            if pd.isna(sku_val):
                continue
            try:
                sku = int(float(sku_val))
            except (ValueError, TypeError):
                continue

            ch = ''
            if has_channel and channel_col is not None:
                ch_val = raw.iloc[r, channel_col]
                ch = str(ch_val).strip().upper() if not pd.isna(ch_val) else ''
            if ch == 'X':
                continue

            for col_idx, dt, wk_num in date_cols:
                val = raw.iloc[r, col_idx]
                v = num(val)
                if v != 0:
                    key = (sku, wk_num)
                    fc[key] = fc.get(key, 0) + v
                    if retailer_name == channel_split_retailer:
                        if ch == 'BM':
                            fc_bm[key] = fc_bm.get(key, 0) + v
                        elif ch == 'COM':
                            fc_dc[key] = fc_dc.get(key, 0) + v

        forecast_data[retailer_name] = fc
        forecast_is_dollars[retailer_name] = is_dollar_forecast
        if retailer_name == channel_split_retailer:
            forecast_data_bm[retailer_name] = fc_bm
            forecast_data_dc[retailer_name] = fc_dc
            print(f"    Channel split: {len(fc_bm)} B&M, {len(fc_dc)} Dotcom")
        print(f"    {len(fc)} SKU-week combinations")

    print(f"  Forecasts loaded ({time.time()-t:.1f}s)")
    return forecast_data, forecast_is_dollars, forecast_data_bm, forecast_data_dc, srp_map


# ─────────────────────────────────────────────────────────────
# 4. Read Location / Door Data
# ─────────────────────────────────────────────────────────────
def read_location_data(config, shared_dir):
    """Read location/door-level data. Returns DataFrame or None."""
    loc_cfg = config['sources'].get('location')
    if not loc_cfg:
        return None

    print("Reading location data...")
    t = time.time()

    fp = find_file(loc_cfg['file_pattern'], [shared_dir])
    if not fp:
        print(f"  WARNING: Location file not found (pattern: {loc_cfg['file_pattern']})")
        return None

    sheet = loc_cfg.get('sheet_name')
    df = pd.read_excel(fp, sheet_name=sheet) if sheet else pd.read_excel(fp)
    print(f"  {len(df):,} rows loaded ({time.time()-t:.1f}s)")

    # Rename columns to canonical names using config mapping
    cols = loc_cfg.get('columns', {})
    rename_map = {}
    canonical_cols = {
        'location_number': 'Location Number',
        'location_desc': 'Location Desc',
        'territory': 'Territory',
        'region': 'Region',
        'state': 'State',
        'city': 'City',
        'fixture': 'Fixture',
        'volume': 'Store Volume',
        'week_sales_dollars': 'Week End Sales Net $',
        'week_sales_dollars_ly': 'Week End Sales Net $ LY',
        'week_sales_units': 'Week End Sales Units',
        'week_inv_retail': 'Week End Inv Retail',
        'week_afs_units': 'Week End AFS Units',
        'ytd_dollars': 'Week End YTD Sales Net $',
        'ytd_dollars_ly': 'Week End YTD Sales Net $ LY',
    }

    for cfg_key, canonical in canonical_cols.items():
        src_col = cols.get(cfg_key)
        if src_col and src_col in df.columns and src_col != canonical:
            rename_map[src_col] = canonical

    if rename_map:
        df = df.rename(columns=rename_map)

    # Ensure numeric columns
    numeric_cols = ['Week End Sales Units', 'Week End Sales Net $', 'Week End Sales Net $ LY',
                    'Week End Inv Retail', 'Week End AFS Units',
                    'Week End YTD Sales Net $', 'Week End YTD Sales Net $ LY']
    for c in numeric_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0.0)

    if 'Year' in df.columns:
        df['Year'] = pd.to_numeric(df['Year'], errors='coerce')
    if 'Week' in df.columns:
        df['Week'] = pd.to_numeric(df['Week'], errors='coerce')

    return df


# ─────────────────────────────────────────────────────────────
# 5. Backfill LY data
# ─────────────────────────────────────────────────────────────
def backfill_ly(sales_df, config, week_month_map):
    """Backfill LY columns from Year=LY TY data for retailers missing LY.
    Returns: (sales_df, retailers_needing_ly, ly_retailer_week_lookup, ly_retailer_month_lookup)
    """
    current_year = config['calendar']['current_year']
    ly_year = current_year - 1

    print("Backfilling LY data from prior year rows...")
    ly_data = sales_df[sales_df['Year'] == ly_year]
    cy_rows = sales_df[sales_df['Year'] == current_year]

    retailers_needing_ly = []
    for ret in cy_rows['Retailer'].unique():
        ret_ly_sum = cy_rows.loc[cy_rows['Retailer'] == ret, 'LY Total Sales $'].sum()
        if abs(ret_ly_sum) < 1.0:
            retailers_needing_ly.append(ret)

    ly_retailer_week_lookup = {}
    ly_retailer_month_lookup = {}

    if retailers_needing_ly and not ly_data.empty:
        ly_sku_lookup = ly_data.groupby(['Retailer', 'Item Code', 'Week']).agg({
            'TY Total Sales $': 'sum',
            'TY B&M Sales $': 'sum',
            'TY Dotcom Sales $': 'sum',
        }).to_dict('index')

        mask = (sales_df['Year'] == current_year) & (sales_df['Retailer'].isin(retailers_needing_ly))
        idx = sales_df.index[mask]
        ly_total = []
        ly_bm = []
        ly_dc = []
        for i in idx:
            row = sales_df.loc[i]
            key = (row['Retailer'], row['Item Code'], row['Week'])
            ly_row = ly_sku_lookup.get(key, {})
            ly_total.append(ly_row.get('TY Total Sales $', 0.0))
            ly_bm.append(ly_row.get('TY B&M Sales $', 0.0))
            ly_dc.append(ly_row.get('TY Dotcom Sales $', 0.0))

        sales_df.loc[idx, 'LY Total Sales $'] = ly_total
        sales_df.loc[idx, 'LY B&M Sales $'] = ly_bm
        sales_df.loc[idx, 'LY Dotcom Sales $'] = ly_dc

        # Retailer+week level totals
        ly_ret_week_totals = ly_data[ly_data['Retailer'].isin(retailers_needing_ly)].groupby(
            ['Retailer', 'Week']
        ).agg({
            'TY Total Sales $': 'sum',
            'TY B&M Sales $': 'sum',
            'TY Dotcom Sales $': 'sum',
        }).reset_index()

        for _, r in ly_ret_week_totals.iterrows():
            ly_retailer_week_lookup[(r['Retailer'], int(r['Week']))] = {
                'LY Total Sales $': r['TY Total Sales $'],
                'LY B&M Sales $': r['TY B&M Sales $'],
                'LY Dotcom Sales $': r['TY Dotcom Sales $'],
            }

        # Monthly lookup
        _ly_for_month = ly_data[ly_data['Retailer'].isin(retailers_needing_ly)].copy()
        _ly_for_month['mo'] = _ly_for_month['Week'].apply(lambda w: week_month_map.get((ly_year, w), ''))
        _ly_mo_agg = _ly_for_month.groupby(['Retailer', 'mo']).agg({
            'TY Total Sales $': 'sum',
        }).reset_index()
        for _, r in _ly_mo_agg.iterrows():
            ly_retailer_month_lookup[(r['Retailer'], r['mo'])] = r['TY Total Sales $']

        for ret in retailers_needing_ly:
            sku_ly = sales_df.loc[(sales_df['Year'] == current_year) & (sales_df['Retailer'] == ret), 'LY Total Sales $'].sum()
            print(f"  {ret}: SKU-level LY = ${sku_ly:,.0f}")
        print(f"  Retailers backfilled: {len(retailers_needing_ly)}")
    else:
        print("  All retailers have LY data — no backfill needed")

    return sales_df, retailers_needing_ly, ly_retailer_week_lookup, ly_retailer_month_lookup


# ─────────────────────────────────────────────────────────────
# 7. Read Inventory Data (optional)
# ─────────────────────────────────────────────────────────────
def read_inventory(config, shared_dirs):
    """Read 3PL/warehouse inventory file. Returns DataFrame or None."""
    inv_cfg = config['sources'].get('inventory')
    if not inv_cfg:
        return None

    pattern = inv_cfg.get('file_pattern', 'Inventory')
    fp = find_file(pattern, shared_dirs)
    if not fp:
        print(f"  Inventory file not found (pattern: '{pattern}')")
        return None

    print(f"  Reading inventory: {os.path.basename(fp)}")
    sheet = inv_cfg.get('sheet_name')
    cols = inv_cfg.get('columns', {})

    # Handle both .xls (OLE2 binary via xlrd) and .xlsx
    ext = os.path.splitext(fp)[1].lower()
    try:
        if ext == '.xls':
            import xlrd
            df = pd.read_excel(fp, sheet_name=sheet, engine='xlrd')
        else:
            df = pd.read_excel(fp, sheet_name=sheet)
    except Exception as e:
        print(f"  Error reading inventory file: {e}")
        return None

    # Rename columns to canonical names
    rename = {}
    for canonical, actual in cols.items():
        if actual and actual in df.columns:
            rename[actual] = canonical.replace('_', ' ').title().replace(' ', ' ')
    # Map to standard names used by aggregate
    col_map = {}
    if cols.get('sku') and cols['sku'] in df.columns:
        col_map[cols['sku']] = 'SKU'
    if cols.get('on_hand_units') and cols['on_hand_units'] in df.columns:
        col_map[cols['on_hand_units']] = 'On Hand Units'
    if cols.get('cost_per_unit') and cols['cost_per_unit'] in df.columns:
        col_map[cols['cost_per_unit']] = 'FOB/COG'
    if cols.get('location') and cols['location'] in df.columns:
        col_map[cols['location']] = 'Location'
    df = df.rename(columns=col_map)

    print(f"  Inventory: {len(df)} rows")
    return df


# ─────────────────────────────────────────────────────────────
# 8. Read Purchase Orders (optional)
# ─────────────────────────────────────────────────────────────
def read_purchase_orders(config, shared_dirs):
    """Read open purchase orders file. Returns DataFrame or None."""
    po_cfg = config['sources'].get('purchase_orders')
    if not po_cfg:
        return None

    pattern = po_cfg.get('file_pattern', 'Open POs')
    fp = find_file(pattern, shared_dirs)
    if not fp:
        print(f"  PO file not found (pattern: '{pattern}')")
        return None

    print(f"  Reading POs: {os.path.basename(fp)}")
    sheet = po_cfg.get('sheet_name')
    cols = po_cfg.get('columns', {})

    ext = os.path.splitext(fp)[1].lower()
    try:
        if ext == '.xls':
            # Try XML SpreadsheetML first, fall back to xlrd
            try:
                with open(fp, 'rb') as f:
                    header = f.read(100)
                if b'<?xml' in header or b'Workbook' in header:
                    # XML SpreadsheetML format
                    import xml.etree.ElementTree as ET
                    ns = {'ss': 'urn:schemas-microsoft-com:office:spreadsheet'}
                    tree = ET.parse(fp)
                    root = tree.getroot()
                    worksheets = root.findall('.//ss:Worksheet', ns)
                    ws = worksheets[0]
                    if sheet:
                        for w in worksheets:
                            if w.get('{urn:schemas-microsoft-com:office:spreadsheet}Name') == sheet:
                                ws = w
                                break
                    rows_el = ws.findall('.//ss:Row', ns)
                    data_rows = []
                    for row_el in rows_el:
                        cells = row_el.findall('ss:Cell', ns)
                        row_data = []
                        for cell in cells:
                            data_el = cell.find('ss:Data', ns)
                            row_data.append(data_el.text if data_el is not None else '')
                        data_rows.append(row_data)
                    if data_rows:
                        headers = data_rows[0]
                        df = pd.DataFrame(data_rows[1:], columns=headers)
                    else:
                        df = pd.DataFrame()
                else:
                    import xlrd
                    df = pd.read_excel(fp, sheet_name=sheet, engine='xlrd')
            except Exception:
                import xlrd
                df = pd.read_excel(fp, sheet_name=sheet, engine='xlrd')
        else:
            df = pd.read_excel(fp, sheet_name=sheet)
    except Exception as e:
        print(f"  Error reading PO file: {e}")
        return None

    # Rename columns
    col_map = {}
    if cols.get('sku') and cols['sku'] in df.columns:
        col_map[cols['sku']] = 'SKU'
    if cols.get('po_number') and cols['po_number'] in df.columns:
        col_map[cols['po_number']] = 'PO Number'
    if cols.get('ordered_units') and cols['ordered_units'] in df.columns:
        col_map[cols['ordered_units']] = 'Ordered Units'
    if cols.get('expected_date') and cols['expected_date'] in df.columns:
        col_map[cols['expected_date']] = 'Expected Date'
    df = df.rename(columns=col_map)

    print(f"  POs: {len(df)} rows")
    return df


# ─────────────────────────────────────────────────────────────
# 9. Read DTC Performance CSVs (optional)
# ─────────────────────────────────────────────────────────────
def read_dtc_performance(config, shared_dirs):
    """Read DTC daily metrics and cohort CSVs for the DTC Performance tab.

    Returns dict with 'daily_df' and 'cohort_df' (DataFrames or None).
    """
    dtc_cfg = config.get('sources', {}).get('dtc_performance')
    if not dtc_cfg:
        return None

    result = {}

    # Daily metrics CSV
    daily_pattern = dtc_cfg.get('daily_file_pattern', 'DTC Daily')
    fp = find_file(daily_pattern, shared_dirs)
    if fp:
        print(f"  Reading DTC daily: {os.path.basename(fp)}")
        try:
            df = pd.read_csv(fp)
            print(f"  DTC daily: {len(df)} rows")
            result['daily_df'] = df
        except Exception as e:
            print(f"  Error reading DTC daily: {e}")
            result['daily_df'] = None
    else:
        print(f"  DTC daily file not found (pattern: '{daily_pattern}')")
        result['daily_df'] = None

    # Cohort CSV
    cohort_pattern = dtc_cfg.get('cohort_file_pattern', 'DTC Cohorts')
    fp = find_file(cohort_pattern, shared_dirs)
    if fp:
        print(f"  Reading DTC cohorts: {os.path.basename(fp)}")
        try:
            df = pd.read_csv(fp)
            print(f"  DTC cohorts: {len(df)} rows")
            result['cohort_df'] = df
        except Exception as e:
            print(f"  Error reading DTC cohorts: {e}")
            result['cohort_df'] = None
    else:
        result['cohort_df'] = None

    return result
