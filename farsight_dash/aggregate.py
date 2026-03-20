"""Aggregation functions for sell-through dashboard data structures."""

import time
from collections import defaultdict

import pandas as pd
import numpy as np

from .core import num, safe_str, MONTH_ABBR, MONTH_NUM


def _fc_to_dollars(forecast_is_dollars, srp_map, retailer, sku, value):
    """Convert forecast value to dollars."""
    if forecast_is_dollars.get(retailer, False):
        return value
    return value * srp_map.get(sku, 0)


def build_all(config, sales_df, sku_info, forecast_data, forecast_is_dollars,
              forecast_data_bm, forecast_data_dc, srp_map,
              loc_df, week_date_map, week_month_map, meta,
              retailers_needing_ly, ly_retailer_week_lookup, ly_retailer_month_lookup):
    """Build all data structures for the dashboard.
    Returns the full DATA dict ready for JSON serialization.
    """
    print("Building data structures...")
    t = time.time()

    current_year = meta['current_year']
    current_week = meta['current_week']
    current_month_445 = meta['current_month_445']
    current_week_end = meta['current_week_end']
    all_weeks_current = meta['all_weeks']
    l4w_weeks = meta['l4w_weeks']

    # Channel split retailer (for B&M/Dotcom forecast breakdown)
    dp_cfg = config['sources'].get('demand_plan') or {}
    channel_split_retailer = dp_cfg.get('channel_split_retailer')

    all_rets = sales_df['Retailer'].dropna().unique().tolist()
    ret_order = config.get('retailer_order', [])
    if ret_order:
        retailers = [r for r in ret_order if r in all_rets] + sorted(r for r in all_rets if r not in ret_order)
    else:
        retailers = sorted(all_rets)
    categories = sorted([c for c in sales_df.get('Category', pd.Series()).dropna().unique().tolist()
                         if c and str(c) != 'nan'])
    collections = sorted([c for c in sales_df.get('Collection', pd.Series()).dropna().unique().tolist()
                          if c and str(c) != 'nan'])
    years = sorted(sales_df['Year'].unique().tolist())
    forecast_retailers = sorted(forecast_data.keys())

    def fc2d(retailer, sku, value):
        return _fc_to_dollars(forecast_is_dollars, srp_map, retailer, sku, value)

    # ── 6a. Weekly aggregation by retailer ──
    print("  Building weekly data...")
    weekly = _build_weekly(sales_df, forecast_data, fc2d, week_month_map, week_date_map,
                           current_year, current_week, retailers_needing_ly, ly_retailer_week_lookup)

    # ── 6b. Monthly aggregation by retailer ──
    print("  Building monthly data...")
    monthly = _build_monthly(sales_df, forecast_data, fc2d, week_month_map,
                              current_year, retailers_needing_ly, ly_retailer_month_lookup)

    # ── 6c. Category monthly ──
    print("  Building category monthly data...")
    cat_monthly = _build_cat_monthly(sales_df, forecast_data, fc2d, sku_info,
                                      week_month_map, current_year)

    # ── 6d-f. SKU-level data ──
    print("  Building SKU-level data...")
    skus, sku_series, sku_weekly = _build_sku_data(
        sales_df, sku_info, forecast_data, fc2d, srp_map,
        current_year, current_week, current_month_445, l4w_weeks, all_weeks_current,
        week_month_map)

    # ── 6g. Retailer summary ──
    print("  Building retailer summary...")
    ret_summary = _build_retailer_summary(
        sales_df, forecast_data, forecast_data_bm, forecast_data_dc, fc2d,
        retailers, current_year, current_week, current_month_445, l4w_weeks,
        week_month_map, retailers_needing_ly, ly_retailer_week_lookup, ly_retailer_month_lookup,
        channel_split_retailer)

    # ── 6h. New launches ──
    print("  Building new launches data...")
    new_launches = _build_new_launches(sales_df, skus, forecast_data, fc2d, current_year)

    # ── 6i-j. Location data ──
    loc_sales = []
    loc_weekly_data = []
    loc_meta = {'territories': [], 'regions': [], 'states': [], 'fixtures': [], 'volumes': [], 'loc_current_week': current_week}
    if loc_df is not None and config['tabs'].get('doors', False):
        print("  Building location/door data...")
        loc_sales, loc_weekly_data, loc_meta = _build_location_data(loc_df, current_year, current_week)

    print(f"  Data structures built ({time.time()-t:.1f}s)")

    # Assemble final DATA
    from datetime import datetime
    build_timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')

    data_meta = {
        'current_year': current_year,
        'current_week': current_week,
        'current_month': current_month_445,
        'current_week_end': current_week_end.strftime('%m.%d.%Y'),
        'retailers': retailers,
        'categories': categories,
        'collections': collections,
        'years': years,
        'l4w_weeks': l4w_weeks,
        'build_timestamp': build_timestamp,
        'data_through_label': f"Data through Week {current_week} (ending {current_week_end.strftime('%m.%d.%Y')})",
        'forecast_retailers': forecast_retailers,
        'all_weeks': all_weeks_current,
        'client_name': config['client_name'],
        'tabs': config.get('tabs', {}),
        'notes_csv_url': config.get('notes_csv_url', ''),
        'notes_edit_url': config.get('notes_edit_url', ''),
        'notes_channels': config.get('notes_channels', []),
    }

    # ── 6k. Inventory data (optional) ──
    inv_data = {}
    has_inv = any(config['tabs'].get(k, False) for k in ('inv_overview', 'inv_detail'))
    if has_inv:
        print("  Building inventory data...")
        inv_data = _build_inventory_data(config, skus, sku_info, srp_map,
                                          current_year, current_week, l4w_weeks)

    # ── 6l. DTC Performance data (optional) ──
    dtc_perf_data = {}
    if config['tabs'].get('dtc', False) and config.get('_dtc_perf'):
        print("  Building DTC performance data...")
        dtc_perf_data = _build_dtc_performance(config)

    DATA = {
        'meta': data_meta,
        'weekly': weekly,
        'monthly': monthly,
        'cat_monthly': cat_monthly,
        'skus': skus,
        'sku_series': sku_series,
        'sku_weekly': sku_weekly,
        'ret_summary': ret_summary,
        'new_launches': new_launches,
        'loc_sales': loc_sales,
        'loc_weekly': loc_weekly_data,
        'loc_meta': loc_meta,
        'inventory': inv_data,
        'dtc_perf': dtc_perf_data,
    }

    return DATA


# ─────────────────────────────────────────────────────────────
# 6a. Weekly
# ─────────────────────────────────────────────────────────────
def _build_weekly(sales_df, forecast_data, fc2d, week_month_map, week_date_map,
                  current_year, current_week, retailers_needing_ly, ly_retailer_week_lookup):
    weekly_agg = sales_df.groupby(['Year', 'Week', 'Retailer']).agg({
        'TY Total Sales $': 'sum',
        'TY B&M Sales $': 'sum',
        'TY Dotcom Sales $': 'sum',
        'TY Total Sales Units': 'sum',
        'LY Total Sales $': 'sum',
        'LY B&M Sales $': 'sum',
        'LY Dotcom Sales $': 'sum',
    }).reset_index()

    weekly = []
    for _, r in weekly_agg.iterrows():
        yr = int(r['Year'])
        wk = int(r['Week'])
        ret = r['Retailer']
        mo = week_month_map.get((yr, wk), '')
        mo_n = MONTH_NUM.get(mo, 0)
        we = week_date_map.get((yr, wk))
        we_str = we.strftime('%Y-%m-%d') if we is not None and not pd.isna(we) else ''

        fc_u = 0
        fc_d = 0.0
        if yr == current_year and ret in forecast_data:
            fc_dict = forecast_data[ret]
            for (sku, fwk), units in fc_dict.items():
                if fwk == wk:
                    fc_u += units
                    fc_d += fc2d(ret, sku, units)

        ly_val = round(r['LY Total Sales $'], 2)
        ly_bm_val = round(r['LY B&M Sales $'], 2)
        ly_dc_val = round(r['LY Dotcom Sales $'], 2)
        if yr == current_year and ret in retailers_needing_ly and ly_retailer_week_lookup:
            rw = ly_retailer_week_lookup.get((ret, wk))
            if rw:
                ly_val = round(rw['LY Total Sales $'], 2)
                ly_bm_val = round(rw['LY B&M Sales $'], 2)
                ly_dc_val = round(rw['LY Dotcom Sales $'], 2)

        weekly.append({
            'yr': yr, 'wk': wk, 'mo': mo, 'mo_n': mo_n,
            'ret': ret, 'we': we_str,
            'cur': yr == current_year,
            'ty': round(r['TY Total Sales $'], 2),
            'ty_bm': round(r['TY B&M Sales $'], 2),
            'ty_dc': round(r['TY Dotcom Sales $'], 2),
            'ty_u': int(r['TY Total Sales Units']),
            'ly': ly_val, 'ly_bm': ly_bm_val, 'ly_dc': ly_dc_val,
            'fc_u': round(fc_u, 2), 'fc_d': round(fc_d, 2),
        })

    # "All" retailer rows
    all_agg = sales_df.groupby(['Year', 'Week']).agg({
        'TY Total Sales $': 'sum', 'TY B&M Sales $': 'sum', 'TY Dotcom Sales $': 'sum',
        'TY Total Sales Units': 'sum', 'LY Total Sales $': 'sum',
        'LY B&M Sales $': 'sum', 'LY Dotcom Sales $': 'sum',
    }).reset_index()

    for _, r in all_agg.iterrows():
        yr = int(r['Year'])
        wk = int(r['Week'])
        mo = week_month_map.get((yr, wk), '')
        mo_n = MONTH_NUM.get(mo, 0)
        we = week_date_map.get((yr, wk))
        we_str = we.strftime('%Y-%m-%d') if we is not None and not pd.isna(we) else ''

        fc_u = 0
        fc_d = 0.0
        if yr == current_year:
            for ret, fc_dict in forecast_data.items():
                for (sku, fwk), units in fc_dict.items():
                    if fwk == wk:
                        fc_u += units
                        fc_d += fc2d(ret, sku, units)

        ly_val = round(r['LY Total Sales $'], 2)
        ly_bm_val = round(r['LY B&M Sales $'], 2)
        ly_dc_val = round(r['LY Dotcom Sales $'], 2)
        if yr == current_year and ly_retailer_week_lookup:
            for bret in retailers_needing_ly:
                rw = ly_retailer_week_lookup.get((bret, wk))
                if rw:
                    sku_ly = sales_df.loc[
                        (sales_df['Year'] == current_year) & (sales_df['Retailer'] == bret) & (sales_df['Week'] == wk),
                        'LY Total Sales $'].sum()
                    sku_ly_bm = sales_df.loc[
                        (sales_df['Year'] == current_year) & (sales_df['Retailer'] == bret) & (sales_df['Week'] == wk),
                        'LY B&M Sales $'].sum()
                    sku_ly_dc = sales_df.loc[
                        (sales_df['Year'] == current_year) & (sales_df['Retailer'] == bret) & (sales_df['Week'] == wk),
                        'LY Dotcom Sales $'].sum()
                    ly_val += round(rw['LY Total Sales $'] - sku_ly, 2)
                    ly_bm_val += round(rw['LY B&M Sales $'] - sku_ly_bm, 2)
                    ly_dc_val += round(rw['LY Dotcom Sales $'] - sku_ly_dc, 2)

        weekly.append({
            'yr': yr, 'wk': wk, 'mo': mo, 'mo_n': mo_n,
            'ret': 'All', 'we': we_str,
            'cur': yr == current_year,
            'ty': round(r['TY Total Sales $'], 2),
            'ty_bm': round(r['TY B&M Sales $'], 2),
            'ty_dc': round(r['TY Dotcom Sales $'], 2),
            'ty_u': int(r['TY Total Sales Units']),
            'ly': ly_val, 'ly_bm': ly_bm_val, 'ly_dc': ly_dc_val,
            'fc_u': round(fc_u, 2), 'fc_d': round(fc_d, 2),
        })

    return weekly


# ─────────────────────────────────────────────────────────────
# 6b. Monthly
# ─────────────────────────────────────────────────────────────
def _build_monthly(sales_df, forecast_data, fc2d, week_month_map,
                    current_year, retailers_needing_ly, ly_retailer_month_lookup):
    sales_df = sales_df.copy()
    if 'mo' not in sales_df.columns:
        sales_df['mo'] = sales_df['445 Month']
    if 'mo_n' not in sales_df.columns:
        sales_df['mo_n'] = sales_df['mo'].map(MONTH_NUM).fillna(0).astype(int)

    monthly_agg = sales_df.groupby(['Year', 'mo', 'mo_n', 'Retailer']).agg({
        'TY Total Sales $': 'sum', 'TY B&M Sales $': 'sum', 'TY Dotcom Sales $': 'sum',
        'TY Total Sales Units': 'sum', 'LY Total Sales $': 'sum',
    }).reset_index()

    monthly = []
    for _, r in monthly_agg.iterrows():
        yr = int(r['Year'])
        mo = r['mo']
        mo_n = int(r['mo_n'])
        ret = r['Retailer']

        fc_u = 0
        fc_d = 0.0
        if yr == current_year and ret in forecast_data:
            for (sku, wk), units in forecast_data[ret].items():
                if week_month_map.get((yr, wk), '') == mo:
                    fc_u += units
                    fc_d += fc2d(ret, sku, units)

        ly_val = round(r['LY Total Sales $'], 2)
        if yr == current_year and ret in retailers_needing_ly and ly_retailer_month_lookup:
            full_ly = ly_retailer_month_lookup.get((ret, mo))
            if full_ly is not None:
                ly_val = round(full_ly, 2)

        monthly.append({
            'yr': yr, 'mo': mo, 'mo_n': mo_n, 'ret': ret,
            'ty': round(r['TY Total Sales $'], 2),
            'ty_bm': round(r['TY B&M Sales $'], 2),
            'ty_dc': round(r['TY Dotcom Sales $'], 2),
            'ty_u': int(r['TY Total Sales Units']),
            'ly': ly_val,
            'fc_u': round(fc_u, 2), 'fc_d': round(fc_d, 2),
        })

    # "All" rows
    monthly_all = sales_df.groupby(['Year', 'mo', 'mo_n']).agg({
        'TY Total Sales $': 'sum', 'TY B&M Sales $': 'sum', 'TY Dotcom Sales $': 'sum',
        'TY Total Sales Units': 'sum', 'LY Total Sales $': 'sum',
    }).reset_index()

    for _, r in monthly_all.iterrows():
        yr = int(r['Year'])
        mo = r['mo']
        mo_n = int(r['mo_n'])

        fc_u = 0
        fc_d = 0.0
        if yr == current_year:
            for ret, fc_dict in forecast_data.items():
                for (sku, wk), units in fc_dict.items():
                    if week_month_map.get((yr, wk), '') == mo:
                        fc_u += units
                        fc_d += fc2d(ret, sku, units)

        monthly.append({
            'yr': yr, 'mo': mo, 'mo_n': mo_n, 'ret': 'All',
            'ty': round(r['TY Total Sales $'], 2),
            'ty_bm': round(r['TY B&M Sales $'], 2),
            'ty_dc': round(r['TY Dotcom Sales $'], 2),
            'ty_u': int(r['TY Total Sales Units']),
            'ly': round(r['LY Total Sales $'], 2),
            'fc_u': round(fc_u, 2), 'fc_d': round(fc_d, 2),
        })

    return monthly


# ─────────────────────────────────────────────────────────────
# 6c. Category monthly
# ─────────────────────────────────────────────────────────────
def _build_cat_monthly(sales_df, forecast_data, fc2d, sku_info, week_month_map, current_year):
    if 'Category' not in sales_df.columns:
        return []

    sales_df = sales_df.copy()
    if 'mo' not in sales_df.columns:
        sales_df['mo'] = sales_df['445 Month']
    if 'mo_n' not in sales_df.columns:
        sales_df['mo_n'] = sales_df['mo'].map(MONTH_NUM).fillna(0).astype(int)

    agg = sales_df.groupby(['Year', 'mo', 'mo_n', 'Category']).agg({
        'TY Total Sales $': 'sum', 'TY Total Sales Units': 'sum', 'LY Total Sales $': 'sum',
    }).reset_index()

    result = []
    for _, r in agg.iterrows():
        yr = int(r['Year'])
        mo = r['mo']
        mo_n = int(r['mo_n'])
        cat = safe_str(r['Category'])

        fc_u = 0
        fc_d = 0.0
        if yr == current_year:
            for ret, fc_dict in forecast_data.items():
                for (sku, wk), units in fc_dict.items():
                    if week_month_map.get((yr, wk), '') == mo:
                        info = sku_info.get(sku, {})
                        if info.get('category', '') == cat:
                            fc_u += units
                            fc_d += fc2d(ret, sku, units)

        result.append({
            'yr': yr, 'mo': mo, 'mo_n': mo_n, 'cat': cat,
            'ty': round(r['TY Total Sales $'], 2),
            'ty_u': int(r['TY Total Sales Units']),
            'ly': round(r['LY Total Sales $'], 2),
            'fc_u': round(fc_u, 2), 'fc_d': round(fc_d, 2),
        })

    return result


# ─────────────────────────────────────────────────────────────
# 6d-f. SKU-level data
# ─────────────────────────────────────────────────────────────
def _build_sku_data(sales_df, sku_info, forecast_data, fc2d, srp_map,
                    current_year, current_week, current_month_445, l4w_weeks, all_weeks_current,
                    week_month_map):
    cy_data = sales_df[sales_df['Year'] == current_year]

    # Add mo column if missing
    if 'mo' not in sales_df.columns:
        sales_df = sales_df.copy()
        sales_df['mo'] = sales_df['445 Month']
    if 'mo' not in cy_data.columns:
        cy_data = cy_data.copy()
        cy_data['mo'] = cy_data['445 Month']

    cy_mtd = cy_data[cy_data['mo'] == current_month_445]
    cy_wk = cy_data[cy_data['Week'] == current_week]
    cy_l4w = cy_data[cy_data['Week'].isin(l4w_weeks)]
    num_weeks = len(all_weeks_current)

    # Aggregations
    ytd_by_sku = cy_data.groupby('Item Code').agg({
        'TY Total Sales $': 'sum', 'TY B&M Sales $': 'sum', 'TY Dotcom Sales $': 'sum',
        'TY Total Sales Units': 'sum', 'LY Total Sales $': 'sum',
    }).reset_index()
    ytd_by_sku.columns = ['ic', 'ytd', 'ytd_bm', 'ytd_dc', 'ytd_u', 'ytd_ly']

    mtd_by_sku = cy_mtd.groupby('Item Code').agg({
        'TY Total Sales $': 'sum', 'LY Total Sales $': 'sum',
    }).reset_index()
    mtd_by_sku.columns = ['ic', 'mtd', 'mtd_ly']

    wk_by_sku = cy_wk.groupby('Item Code').agg({
        'TY Total Sales $': 'sum', 'LY Total Sales $': 'sum',
    }).reset_index()
    wk_by_sku.columns = ['ic', 'wk', 'wk_ly']

    l4w_by_sku = cy_l4w.groupby('Item Code').agg({
        'TY Total Sales $': 'sum', 'LY Total Sales $': 'sum', 'TY Total Sales Units': 'sum',
    }).reset_index()
    l4w_by_sku.columns = ['ic', 'l4w', 'l4w_ly', 'l4w_u']

    # Retailer breakdown
    ret_by_sku = cy_data.groupby(['Item Code', 'Retailer']).agg({
        'TY Total Sales $': 'sum', 'LY Total Sales $': 'sum',
    }).reset_index()

    sku_rets = defaultdict(dict)
    for _, r in ret_by_sku.iterrows():
        ic = int(r['Item Code'])
        sku_rets[ic][r['Retailer']] = {
            'ty': round(r['TY Total Sales $'], 2),
            'ly': round(r['LY Total Sales $'], 2),
        }

    # SKU descriptions from sales data
    desc_cols = ['Item Code']
    for c in ['SKU Desc', 'Collection', 'Category', 'Sub Category', 'New v Core', 'Launch Status', 'Launch Year']:
        if c in sales_df.columns:
            desc_cols.append(c)

    sku_desc_from_sales = {}
    if len(desc_cols) > 1:
        for _, r in sales_df[desc_cols].drop_duplicates('Item Code').iterrows():
            ic = int(r['Item Code'])
            sku_desc_from_sales[ic] = {
                'desc': safe_str(r.get('SKU Desc', '')),
                'coll': safe_str(r.get('Collection', '')),
                'cat': safe_str(r.get('Category', '')),
                'sub_cat': safe_str(r.get('Sub Category', 'nan')),
                'nvc': safe_str(r.get('New v Core', '')),
                'ls': safe_str(r.get('Launch Status', '')),
                'lyr': int(r['Launch Year']) if 'Launch Year' in r and not pd.isna(r.get('Launch Year')) else 0,
            }

    all_sku_ids = set(ytd_by_sku['ic'].tolist()) | set(sku_info.keys()) | set(sku_desc_from_sales.keys())

    ytd_dict = dict(zip(ytd_by_sku['ic'], ytd_by_sku.to_dict('records')))
    mtd_dict = dict(zip(mtd_by_sku['ic'], mtd_by_sku.to_dict('records')))
    wk_dict = dict(zip(wk_by_sku['ic'], wk_by_sku.to_dict('records')))
    l4w_dict = dict(zip(l4w_by_sku['ic'], l4w_by_sku.to_dict('records')))

    skus = []
    for ic in all_sku_ids:
        sd = sku_desc_from_sales.get(ic, {})
        si = sku_info.get(ic, {})

        desc = sd.get('desc', si.get('product', ''))
        coll = sd.get('coll', si.get('franchise', ''))
        cat = sd.get('cat', si.get('category', ''))
        sub_cat = sd.get('sub_cat', si.get('sub_category', 'nan'))
        nvc = sd.get('nvc', '')
        ls = sd.get('ls', '')
        lyr = sd.get('lyr', 0)

        is_new = si.get('is_new', False)
        active = si.get('active', 'D') if si else 'D'
        status = si.get('status_current_year', '') if si else ''

        y = ytd_dict.get(ic, {})
        ytd_val = num(y.get('ytd', 0))
        ytd_bm = num(y.get('ytd_bm', 0))
        ytd_dc = num(y.get('ytd_dc', 0))
        ytd_u = int(num(y.get('ytd_u', 0)))
        ytd_ly = num(y.get('ytd_ly', 0))

        m = mtd_dict.get(ic, {})
        mtd_val = num(m.get('mtd', 0))
        mtd_ly = num(m.get('mtd_ly', 0))

        w = wk_dict.get(ic, {})
        wk_val = num(w.get('wk', 0))
        wk_ly = num(w.get('wk_ly', 0))

        l = l4w_dict.get(ic, {})
        l4w_val = num(l.get('l4w', 0))
        l4w_ly = num(l.get('l4w_ly', 0))
        l4w_u = int(num(l.get('l4w_u', 0)))

        avg_wk = round(ytd_val / num_weeks, 2) if num_weeks > 0 else 0

        # Forecast
        fc_u_ytd = fc_d_ytd = fc_u_mtd = fc_d_mtd = fc_d_wk = fc_d_l4w = 0
        for ret, fc_dict in forecast_data.items():
            for (fsku, fwk), units in fc_dict.items():
                if fsku != ic:
                    continue
                dollars = fc2d(ret, ic, units)
                if fwk <= current_week:
                    fc_u_ytd += units
                    fc_d_ytd += dollars
                wk_mo = week_month_map.get((current_year, fwk), '')
                if wk_mo == current_month_445:
                    fc_u_mtd += units
                    fc_d_mtd += dollars
                if fwk == current_week:
                    fc_d_wk += dollars
                if fwk in l4w_weeks:
                    fc_d_l4w += dollars

        risk_pct = round(ytd_val / fc_d_ytd * 100, 2) if fc_d_ytd > 0 else 0
        risk = 'on_track' if risk_pct >= 90 else ('at_risk' if risk_pct >= 75 else 'behind')
        if fc_d_ytd == 0:
            risk = 'on_track'
            risk_pct = 0

        if ytd_val == 0 and ytd_ly == 0 and ic not in sku_info:
            continue

        skus.append({
            'ic': ic, 'desc': desc, 'coll': coll, 'cat': cat, 'sub_cat': sub_cat,
            'nvc': nvc, 'ls': ls, 'lyr': lyr, 'is_new': is_new,
            'aos': active, 's26': status, 'risk': risk, 'risk_pct': round(risk_pct, 2),
            'ytd': round(ytd_val, 2), 'ytd_bm': round(ytd_bm, 2), 'ytd_dc': round(ytd_dc, 2),
            'ytd_u': ytd_u, 'ytd_ly': round(ytd_ly, 2),
            'mtd': round(mtd_val, 2), 'mtd_ly': round(mtd_ly, 2),
            'wk': round(wk_val, 2), 'wk_ly': round(wk_ly, 2),
            'l4w': round(l4w_val, 2), 'l4w_ly': round(l4w_ly, 2), 'l4w_u': l4w_u,
            'avg_wk': round(avg_wk, 2),
            'fc_u_ytd': round(fc_u_ytd, 2), 'fc_d_ytd': round(fc_d_ytd, 2),
            'fc_u_mtd': round(fc_u_mtd, 2), 'fc_d_mtd': round(fc_d_mtd, 2),
            'fc_d_wk': round(fc_d_wk, 2), 'fc_d_l4w': round(fc_d_l4w, 2),
            'rets': sku_rets.get(ic, {}),
            'rank': 0,
        })

    skus.sort(key=lambda x: x['ytd'], reverse=True)
    for i, s in enumerate(skus):
        s['rank'] = i + 1

    # SKU series (top 30)
    top30_ics = [s['ic'] for s in skus[:30]]
    sku_weekly_raw = cy_data[cy_data['Item Code'].isin(top30_ics)].groupby(['Item Code', 'Week']).agg({
        'TY Total Sales $': 'sum', 'LY Total Sales $': 'sum',
    }).reset_index()

    sku_series = []
    for ic in top30_ics:
        sub = sku_weekly_raw[sku_weekly_raw['Item Code'] == ic].sort_values('Week')
        desc = next((s['desc'] for s in skus if s['ic'] == ic), '')
        weeks_list = []
        for _, r in sub.iterrows():
            wk = int(r['Week'])
            fc_d = 0
            for ret, fc_dict in forecast_data.items():
                fc_u = fc_dict.get((ic, wk), 0)
                fc_d += fc2d(ret, ic, fc_u)
            weeks_list.append({
                'wk': wk, 'ty': round(r['TY Total Sales $'], 2),
                'ly': round(r['LY Total Sales $'], 2), 'fc_d': round(fc_d, 2),
            })
        sku_series.append({'ic': ic, 'desc': desc, 'weeks': weeks_list})

    # SKU weekly (all)
    sku_weekly_all = cy_data.groupby(['Item Code', 'Week']).agg({
        'TY Total Sales $': 'sum', 'LY Total Sales $': 'sum', 'TY Total Sales Units': 'sum',
    }).reset_index()

    sku_weekly = []
    for _, r in sku_weekly_all.iterrows():
        ic = int(r['Item Code'])
        wk = int(r['Week'])
        fc_d = 0
        for ret, fc_dict in forecast_data.items():
            fc_u = fc_dict.get((ic, wk), 0)
            fc_d += fc2d(ret, ic, fc_u)
        sku_weekly.append({
            'ic': ic, 'wk': wk,
            'ty': round(r['TY Total Sales $'], 2),
            'ly': round(r['LY Total Sales $'], 2),
            'ty_u': int(r['TY Total Sales Units']),
            'fc_d': round(fc_d, 2),
        })

    return skus, sku_series, sku_weekly


# ─────────────────────────────────────────────────────────────
# 6g. Retailer summary
# ─────────────────────────────────────────────────────────────
def _build_retailer_summary(sales_df, forecast_data, forecast_data_bm, forecast_data_dc, fc2d,
                             retailers, current_year, current_week, current_month_445, l4w_weeks,
                             week_month_map, retailers_needing_ly, ly_retailer_week_lookup,
                             ly_retailer_month_lookup, channel_split_retailer):
    cy_data = sales_df[sales_df['Year'] == current_year].copy()
    if 'mo' not in cy_data.columns:
        cy_data['mo'] = cy_data['445 Month']

    ret_summary = []
    for ret in retailers + ['All']:
        rd = cy_data if ret == 'All' else cy_data[cy_data['Retailer'] == ret]
        rd_mtd = rd[rd['mo'] == current_month_445]
        rd_wk = rd[rd['Week'] == current_week]
        rd_l4w = rd[rd['Week'].isin(l4w_weeks)]

        ytd_val = rd['TY Total Sales $'].sum()
        ytd_ly = rd['LY Total Sales $'].sum()
        ytd_bm = rd['TY B&M Sales $'].sum()
        ytd_dc = rd['TY Dotcom Sales $'].sum()
        ytd_bm_ly = rd['LY B&M Sales $'].sum()
        ytd_dc_ly = rd['LY Dotcom Sales $'].sum()
        ytd_u = int(rd['TY Total Sales Units'].sum())
        mtd_val = rd_mtd['TY Total Sales $'].sum()
        mtd_ly = rd_mtd['LY Total Sales $'].sum()
        mtd_bm = rd_mtd['TY B&M Sales $'].sum()
        mtd_dc = rd_mtd['TY Dotcom Sales $'].sum()
        wk_val = rd_wk['TY Total Sales $'].sum()
        wk_ly = rd_wk['LY Total Sales $'].sum()
        wk_bm = rd_wk['TY B&M Sales $'].sum()
        wk_dc = rd_wk['TY Dotcom Sales $'].sum()
        wk_bm_ly = rd_wk['LY B&M Sales $'].sum()
        wk_dc_ly = rd_wk['LY Dotcom Sales $'].sum()
        l4w_val = rd_l4w['TY Total Sales $'].sum()
        l4w_ly = rd_l4w['LY Total Sales $'].sum()

        # LY backfill corrections
        _rets_to_fix = []
        if ret == 'All':
            _rets_to_fix = retailers_needing_ly or []
        elif ret in (retailers_needing_ly or []):
            _rets_to_fix = [ret]

        for _bret in _rets_to_fix:
            _sku_ytd_ly = 0
            _full_ytd_ly = 0
            for _wk in range(1, current_week + 1):
                _sku_ly_wk = sales_df.loc[
                    (sales_df['Year'] == current_year) & (sales_df['Retailer'] == _bret) & (sales_df['Week'] == _wk),
                    'LY Total Sales $'].sum()
                _sku_ytd_ly += _sku_ly_wk
                _rw = ly_retailer_week_lookup.get((_bret, _wk))
                _full_ytd_ly += _rw['LY Total Sales $'] if _rw else _sku_ly_wk
            ytd_ly += (_full_ytd_ly - _sku_ytd_ly)

            _rw = ly_retailer_week_lookup.get((_bret, current_week))
            if _rw:
                _sku_wk_ly = sales_df.loc[
                    (sales_df['Year'] == current_year) & (sales_df['Retailer'] == _bret) & (sales_df['Week'] == current_week),
                    'LY Total Sales $'].sum()
                wk_ly += (_rw['LY Total Sales $'] - _sku_wk_ly)

            for _wk in l4w_weeks:
                _rw = ly_retailer_week_lookup.get((_bret, _wk))
                if _rw:
                    _sku_ly_wk = sales_df.loc[
                        (sales_df['Year'] == current_year) & (sales_df['Retailer'] == _bret) & (sales_df['Week'] == _wk),
                        'LY Total Sales $'].sum()
                    l4w_ly += (_rw['LY Total Sales $'] - _sku_ly_wk)

            if ly_retailer_month_lookup:
                _full_mtd_ly = ly_retailer_month_lookup.get((_bret, current_month_445), 0)
                _sku_mtd_ly = sales_df.loc[
                    (sales_df['Year'] == current_year) & (sales_df['Retailer'] == _bret) & (sales_df['mo'] == current_month_445),
                    'LY Total Sales $'].sum()
                mtd_ly += (_full_mtd_ly - _sku_mtd_ly)

        # Forecasts
        fc_u_ytd = fc_d_ytd = fc_d_mtd = fc_d_wk = fc_d_l4w = 0
        fc_d_ytd_bm = fc_d_wk_bm = fc_d_ytd_dc = fc_d_wk_dc = 0

        fc_rets = list(forecast_data.keys()) if ret == 'All' else ([ret] if ret in forecast_data else [])
        for fc_ret in fc_rets:
            for (sku, wk), units in forecast_data[fc_ret].items():
                d = fc2d(fc_ret, sku, units)
                if wk <= current_week:
                    fc_u_ytd += units
                    fc_d_ytd += d
                if week_month_map.get((current_year, wk), '') == current_month_445:
                    fc_d_mtd += d
                if wk == current_week:
                    fc_d_wk += d
                if wk in l4w_weeks:
                    fc_d_l4w += d

        # Channel forecasts
        if channel_split_retailer and (ret == channel_split_retailer or ret == 'All'):
            for ch_dict, is_bm in [(forecast_data_bm.get(channel_split_retailer, {}), True),
                                    (forecast_data_dc.get(channel_split_retailer, {}), False)]:
                for (sku, wk), units in ch_dict.items():
                    d = fc2d(channel_split_retailer, sku, units)
                    if is_bm:
                        if wk <= current_week:
                            fc_d_ytd_bm += d
                        if wk == current_week:
                            fc_d_wk_bm += d
                    else:
                        if wk <= current_week:
                            fc_d_ytd_dc += d
                        if wk == current_week:
                            fc_d_wk_dc += d

        ret_summary.append({
            'ret': ret,
            'ytd': round(ytd_val, 2), 'ytd_ly': round(ytd_ly, 2),
            'ytd_bm': round(ytd_bm, 2), 'ytd_dc': round(ytd_dc, 2),
            'ytd_bm_ly': round(ytd_bm_ly, 2), 'ytd_dc_ly': round(ytd_dc_ly, 2),
            'ytd_u': ytd_u,
            'mtd': round(mtd_val, 2), 'mtd_ly': round(mtd_ly, 2),
            'mtd_bm': round(mtd_bm, 2), 'mtd_dc': round(mtd_dc, 2),
            'wk': round(wk_val, 2), 'wk_ly': round(wk_ly, 2),
            'wk_bm': round(wk_bm, 2), 'wk_dc': round(wk_dc, 2),
            'wk_bm_ly': round(wk_bm_ly, 2), 'wk_dc_ly': round(wk_dc_ly, 2),
            'l4w': round(l4w_val, 2), 'l4w_ly': round(l4w_ly, 2),
            'fc_u_ytd': round(fc_u_ytd, 2), 'fc_d_ytd': round(fc_d_ytd, 2),
            'fc_d_mtd': round(fc_d_mtd, 2), 'fc_d_wk': round(fc_d_wk, 2),
            'fc_d_l4w': round(fc_d_l4w, 2),
            'fc_d_ytd_bm': round(fc_d_ytd_bm, 2), 'fc_d_wk_bm': round(fc_d_wk_bm, 2),
            'fc_d_ytd_dc': round(fc_d_ytd_dc, 2), 'fc_d_wk_dc': round(fc_d_wk_dc, 2),
        })

    return ret_summary


# ─────────────────────────────────────────────────────────────
# 6h. New launches
# ─────────────────────────────────────────────────────────────
def _build_new_launches(sales_df, skus, forecast_data, fc2d, current_year):
    new_skus = [s for s in skus if s['is_new']]
    cy_data = sales_df[sales_df['Year'] == current_year]

    new_sku_ics = set(s['ic'] for s in new_skus)
    new_weekly_data = cy_data[cy_data['Item Code'].isin(new_sku_ics)].groupby('Week').agg({
        'TY Total Sales $': 'sum', 'LY Total Sales $': 'sum', 'TY Total Sales Units': 'sum',
    }).reset_index()

    new_agg_weekly = []
    for _, r in new_weekly_data.iterrows():
        wk = int(r['Week'])
        fc_d = 0
        for ic in new_sku_ics:
            for ret, fc_dict in forecast_data.items():
                fc_u = fc_dict.get((ic, wk), 0)
                fc_d += fc2d(ret, ic, fc_u)
        new_agg_weekly.append({
            'wk': wk,
            'ty': round(r['TY Total Sales $'], 2),
            'ly': round(r['LY Total Sales $'], 2),
            'fc_d': round(fc_d, 2),
            'ty_u': int(r['TY Total Sales Units']),
        })

    return {
        'count': len(new_skus),
        'with_sales': sum(1 for s in new_skus if s['ytd'] > 0),
        'with_fcst': sum(1 for s in new_skus if s['fc_d_ytd'] > 0),
        'zero_sales': sum(1 for s in new_skus if s['ytd'] == 0),
        'agg_weekly': new_agg_weekly,
    }


# ─────────────────────────────────────────────────────────────
# 6i-j. Location / door data
# ─────────────────────────────────────────────────────────────
def _build_location_data(loc_df, current_year, current_week):
    loc_cy = loc_df[loc_df['Year'] == current_year].copy()
    if loc_cy.empty:
        return [], [], {'territories': [], 'regions': [], 'states': [], 'fixtures': [], 'volumes': [], 'loc_current_week': current_week}

    loc_max_week = int(loc_cy['Week'].max())
    loc_l5w_weeks = sorted(loc_cy['Week'].unique().tolist())[-5:]
    loc_latest = loc_cy[loc_cy['Week'] == loc_max_week]

    loc_ytd = loc_cy.groupby('Location Number').agg({
        'Week End Sales Net $': 'sum', 'Week End Sales Net $ LY': 'sum',
    }).reset_index()
    loc_ytd.columns = ['loc', 'ytd', 'ytd_ly']

    loc_l5w = loc_cy[loc_cy['Week'].isin(loc_l5w_weeks)].groupby('Location Number').agg({
        'Week End Sales Net $': 'sum',
    }).reset_index()
    loc_l5w.columns = ['loc', 'l5w_sum']
    loc_l5w['l5w_avg'] = loc_l5w['l5w_sum'] / len(loc_l5w_weeks) if len(loc_l5w_weeks) > 0 else 0

    loc_info = {}
    for _, r in loc_latest.iterrows():
        loc_num = int(r['Location Number'])
        loc_info[loc_num] = {
            'name': safe_str(r.get('Location Desc', '')),
            'terr': safe_str(r.get('Territory', '')),
            'reg': safe_str(r.get('Region', '')),
            'st': safe_str(r.get('State', '')),
            'city': safe_str(r.get('City', '')),
            'fix': safe_str(r.get('Fixture', '')),
            'vol': safe_str(r.get('Store Volume', '')),
            'wk': round(num(r.get('Week End Sales Net $', 0)), 2),
            'wk_ly': round(num(r.get('Week End Sales Net $ LY', 0)), 2),
            'wk_u': int(num(r.get('Week End Sales Units', 0))),
            'inv': round(num(r.get('Week End Inv Retail', 0)), 2),
            'afs': int(num(r.get('Week End AFS Units', 0))),
        }

    loc_ytd_dict = dict(zip(loc_ytd['loc'], loc_ytd.to_dict('records')))
    loc_l5w_dict = dict(zip(loc_l5w['loc'], loc_l5w.to_dict('records')))
    num_loc_weeks = len(loc_cy['Week'].unique())

    loc_sales = []
    for loc_num, info in loc_info.items():
        ytd_rec = loc_ytd_dict.get(loc_num, {})
        l5w_rec = loc_l5w_dict.get(loc_num, {})
        ytd_val = num(ytd_rec.get('ytd', 0))
        ytd_ly = num(ytd_rec.get('ytd_ly', 0))
        l5w_avg = num(l5w_rec.get('l5w_avg', 0))
        ytd_avg = round(ytd_val / num_loc_weeks, 2) if num_loc_weeks > 0 else 0

        wk_val = info['wk']
        wk_ly = info['wk_ly']
        chg_d = round(wk_val - wk_ly, 2)
        chg_p = round((wk_val - wk_ly) / abs(wk_ly), 4) if wk_ly != 0 else 0

        loc_sales.append({
            'loc': loc_num, 'name': info['name'],
            'terr': info['terr'], 'reg': info['reg'], 'st': info['st'],
            'city': info['city'], 'fix': info['fix'], 'vol': info['vol'],
            'wk': wk_val, 'wk_ly': wk_ly, 'wk_u': info['wk_u'],
            'ytd': round(ytd_val, 2), 'ytd_ly': round(ytd_ly, 2),
            'ytd_avg': round(ytd_avg, 2), 'l5w_avg': round(l5w_avg, 2),
            'chg_d': chg_d, 'chg_p': round(chg_p, 4),
            'inv': info['inv'], 'afs': info['afs'],
        })

    loc_sales.sort(key=lambda x: x['ytd'], reverse=True)

    # Weekly trends for top 400
    top_locs = [l['loc'] for l in loc_sales[:400]]
    loc_weekly_raw = loc_cy[loc_cy['Location Number'].isin(top_locs)].groupby(
        ['Location Number', 'Week']).agg({
        'Week End Sales Net $': 'sum', 'Week End Sales Net $ LY': 'sum', 'Week End Sales Units': 'sum',
    }).reset_index()

    loc_weekly = []
    for loc_num in top_locs:
        sub = loc_weekly_raw[loc_weekly_raw['Location Number'] == loc_num].sort_values('Week')
        weeks_list = [{'wk': int(r['Week']),
                       'ty': round(r['Week End Sales Net $'], 2),
                       'ly': round(r['Week End Sales Net $ LY'], 2),
                       'u': int(r['Week End Sales Units'])}
                      for _, r in sub.iterrows()]
        loc_weekly.append({'loc': loc_num, 'weeks': weeks_list})

    territories = sorted(set(l['terr'] for l in loc_sales if l['terr'] and l['terr'] != 'nan'))
    regions = sorted(set(l['reg'] for l in loc_sales if l['reg'] and l['reg'] != 'nan'))
    states = sorted(set(l['st'] for l in loc_sales if l['st'] and l['st'] != 'nan'))
    fixtures = sorted(set(l['fix'] for l in loc_sales if l['fix'] and l['fix'] != 'nan'))
    volumes = sorted(set(l['vol'] for l in loc_sales if l['vol'] and l['vol'] != 'nan'))

    loc_meta = {
        'territories': territories, 'regions': regions, 'states': states,
        'fixtures': fixtures, 'volumes': volumes, 'loc_current_week': loc_max_week,
    }

    return loc_sales, loc_weekly, loc_meta


# ─────────────────────────────────────────────────────────────
# 6k. Inventory
# ─────────────────────────────────────────────────────────────
def _build_inventory_data(config, skus, sku_info, srp_map,
                           current_year, current_week, l4w_weeks):
    """Build inventory data structure for the inventory tabs.

    Merges inventory/PO data (if available) with existing SKU-level sell-through
    data to produce a unified inventory view.

    Returns dict with 'sku_list' and 'channels'.
    """
    # Get inventory and PO dataframes from config (passed through via ingest)
    inv_df = config.get('_inv_df')
    po_df = config.get('_po_df')

    # Build the inventory SKU list from existing sell-through SKU data + inventory overlay
    inv_by_sku = {}
    if inv_df is not None:
        for _, r in inv_df.iterrows():
            sku = str(r.get('SKU', '')).strip()
            if sku:
                inv_by_sku[sku] = {
                    'on_hand': num(r.get('On Hand Units', 0)),
                    'cost_per_unit': num(r.get('FOB/COG', 0)),
                    'location': str(r.get('Location', '')).strip() if r.get('Location') else None,
                }

    po_by_sku = {}
    if po_df is not None:
        for _, r in po_df.iterrows():
            sku = str(r.get('SKU', '')).strip()
            if sku:
                po_by_sku[sku] = po_by_sku.get(sku, 0) + num(r.get('Ordered Units', 0))

    sku_list = []
    channels = set()
    for s in skus:
        # SKU list uses 'ic' (item code) for SKU, 'desc' for product, 'cat' for category
        sku = s.get('ic', s.get('sku', ''))
        sku_str = str(sku).strip()
        info = sku_info.get(sku, {})
        inv = inv_by_sku.get(sku_str, inv_by_sku.get(sku, {}))
        on_hand = inv.get('on_hand', 0)
        cost_unit = inv.get('cost_per_unit', 0) or num(info.get('cost', 0))
        srp = srp_map.get(sku, 0)
        margin = (srp - cost_unit) / srp if srp > 0 else 0

        # Get avg weekly from sell-through data (top-level SKU fields)
        # Use L4W units / 4 for avg weekly unit velocity (for WOS calc)
        l4w_units = num(s.get('l4w_u', 0))
        avg_weekly = l4w_units / 4.0 if l4w_units > 0 else (num(s.get('ytd_u', 0)) / max(current_week, 1))
        ytd_sales = num(s.get('ytd', 0))
        ytd_units = num(s.get('ytd_u', 0))
        channel_sales = []

        # Build per-retailer channel breakdown
        rets = s.get('rets', {})
        for ret_name, ret_data in rets.items():
            channels.add(ret_name)
            ret_ytd = num(ret_data.get('ty', 0))
            if ret_ytd > 0:
                channel_sales.append({
                    'channel': ret_name,
                    'avg_weekly': round(ret_ytd / max(current_week, 1), 1),
                })

        wos = on_hand / avg_weekly if avg_weekly > 0 else 0

        rec = {
            'sku': sku,
            'product': s.get('desc', s.get('product', '')),
            'category': s.get('cat', s.get('category', '')),
            'tier': info.get('tier', ''),
            'active': not s.get('disco', False),
            'is_new': s.get('is_new', False),
            'status': info.get('status', ''),
            'on_hand': round(on_hand),
            'inv_cost': round(on_hand * cost_unit, 2),
            'cost_per_unit': round(cost_unit, 2),
            'srp': round(srp, 2),
            'margin': round(margin, 4),
            'wos': round(wos, 1),
            'avg_weekly_sales': round(avg_weekly, 1),
            'ytd_sales': round(ytd_sales, 2),
            'ytd_units': round(ytd_units),
            'on_order': round(po_by_sku.get(sku_str, po_by_sku.get(sku, 0))),
            'channel_sales': channel_sales,
        }
        sku_list.append(rec)

    return {
        'sku_list': sku_list,
        'channels': sorted(channels),
    }


# ─────────────────────────────────────────────────────────────
# DTC Performance Data
# ─────────────────────────────────────────────────────────────
def _build_dtc_performance(config):
    """Build DTC performance data from daily metrics + cohort CSVs.

    Returns dict with 'daily' (list of day dicts) and 'cohorts' (list of cohort dicts).
    """
    dtc_raw = config.get('_dtc_perf', {})
    daily_df = dtc_raw.get('daily_df')
    cohort_df = dtc_raw.get('cohort_df')

    result = {'daily': [], 'cohorts': []}

    if daily_df is not None and len(daily_df) > 0:
        for _, r in daily_df.iterrows():
            day = {
                'date': str(r.get('Date', '')),
                'gross_sales': num(r.get('Gross Sales')),
                'gross_sales_target': num(r.get('Gross Sales Target')),
                'meta_spend': num(r.get('Meta Spend')),
                'google_spend': num(r.get('Google Spend')),
                'tiktok_spend': num(r.get('TikTok Spend')),
                'ad_spend_target': num(r.get('Ad Spend Target')),
                'orders': num(r.get('Orders')),
                'units': num(r.get('Units')),
                'sessions': num(r.get('Sessions')),
                'new_customers': num(r.get('New Customers')),
                'new_customers_target': num(r.get('New Customers Target')),
                'returning_customers': num(r.get('Returning Customers')),
                'ncr': num(r.get('NCR')),
                'ncr_target': num(r.get('NCR Target')),
            }
            # Compute derived metrics
            spend = (day['meta_spend'] or 0) + (day['google_spend'] or 0) + (day['tiktok_spend'] or 0)
            day['ad_spend'] = spend
            day['roas'] = round(day['gross_sales'] / spend, 2) if spend > 0 and day['gross_sales'] else None
            day['aov'] = round(day['gross_sales'] / day['orders'], 2) if day['orders'] and day['gross_sales'] else None
            day['upt'] = round(day['units'] / day['orders'], 2) if day['orders'] and day['units'] else None
            day['aur'] = round(day['gross_sales'] / day['units'], 2) if day['units'] and day['gross_sales'] else None
            day['conversion_rate'] = round(day['orders'] / day['sessions'] * 100, 2) if day['sessions'] and day['orders'] else None
            total_cust = (day['new_customers'] or 0) + (day['returning_customers'] or 0)
            day['total_customers'] = total_cust
            day['new_cust_pct'] = round(day['new_customers'] / total_cust * 100, 1) if total_cust > 0 and day['new_customers'] else None
            day['paid_cac'] = round(spend / day['new_customers'], 2) if day['new_customers'] and spend > 0 else None
            result['daily'].append(day)
        print(f"  DTC daily: {len(result['daily'])} days")

    if cohort_df is not None and len(cohort_df) > 0:
        for _, r in cohort_df.iterrows():
            cohort = {
                'month': str(r.get('Cohort Month', '')),
                'new_customers': num(r.get('New Customers')),
            }
            # Read M1..M5 returning columns (stop at first empty/NaN)
            retention = []
            for i in range(1, 7):
                col = f'M{i} Returning'
                raw = r.get(col)
                if raw is None or (isinstance(raw, float) and pd.isna(raw)) or str(raw).strip() == '':
                    break
                retention.append(num(raw))
            cohort['retention'] = retention
            result['cohorts'].append(cohort)
        print(f"  DTC cohorts: {len(result['cohorts'])} months")

    return result
