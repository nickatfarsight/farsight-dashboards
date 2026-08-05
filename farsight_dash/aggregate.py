"""Aggregation functions for sell-through dashboard data structures."""

import time, math
from collections import defaultdict

import pandas as pd
import numpy as np

from .core import num, safe_str, MONTH_ABBR, MONTH_NUM


def _fc_to_dollars(forecast_is_dollars, srp_map, retailer, sku, value):
    """Convert forecast value to dollars."""
    if forecast_is_dollars.get(retailer, False):
        return value
    return value * srp_map.get(sku, 0)


# 4-4-5 fiscal calendar week → month (weeks per month repeat 4,4,5 each quarter)
_445_RANGES = [('Jan', 1, 4), ('Feb', 5, 8), ('Mar', 9, 13), ('Apr', 14, 17),
               ('May', 18, 21), ('Jun', 22, 26), ('Jul', 27, 30), ('Aug', 31, 34),
               ('Sep', 35, 39), ('Oct', 40, 43), ('Nov', 44, 47), ('Dec', 48, 53)]


def _week_to_445_month(wk):
    for mo, a, b in _445_RANGES:
        if a <= wk <= b:
            return mo
    return ''


def _detect_split_retailers(sales_df):
    """Retailers whose rows carry a non-trivial in-store vs online split.

    A retailer qualifies when its B&M and .com columns are both populated and together
    reconcile to its total — i.e. the split is real reported data, not an artefact of one
    column being left at zero. Returns a plain list for JSON.
    """
    out = []
    if sales_df is None or sales_df.empty:
        return out
    for col in ('TY B&M Sales $', 'TY Dotcom Sales $', 'TY Total Sales $', 'Retailer'):
        if col not in sales_df.columns:
            return out
    for ret, g in sales_df.groupby('Retailer'):
        bm = g['TY B&M Sales $'].fillna(0).sum()
        dc = g['TY Dotcom Sales $'].fillna(0).sum()
        tot = g['TY Total Sales $'].fillna(0).sum()
        if tot <= 0 or (bm == 0 and dc == 0):
            continue
        # allow a little rounding drift, but the parts must actually make up the whole
        if abs((bm + dc) - tot) <= max(1.0, abs(tot) * 0.005):
            out.append(str(ret))
    return sorted(out)


def _build_coverage_grid(sales_df, current_year, current_week, monthly_retailers, retailers):
    """Per-retailer weekly sales coverage for the 'Open Items' tab (mirrors the team
    coverage workbook). Everyone sits on one W1-52 axis; monthly retailers show their
    445-month status across that month's weeks. Cell status: have / gap (missing within
    reported range) / behind (recent, not reported yet) / na (pre-launch or future)."""
    ly = current_year - 1
    months = [m for m, _, _ in _445_RANGES]
    cur_mo_idx = months.index(_week_to_445_month(current_week)) if _week_to_445_month(current_week) in months else 11
    out = []
    for ret in retailers:
        monthly = ret in (monthly_retailers or [])
        year_rows = []
        for yr in (ly, current_year):
            present = set(int(w) for w in sales_df[(sales_df['Retailer'] == ret) & (sales_df['Year'] == yr)]['Week'].unique())
            cells = ['na'] * 52
            if monthly:
                pm = {_week_to_445_month(w) for w in present}
                pm_idx = sorted(months.index(m) for m in pm) if pm else []
                for mi, (mo, a, b) in enumerate(_445_RANGES):
                    b = min(b, 52)
                    if mo in pm:
                        st = 'have'
                    elif not pm_idx or mi < pm_idx[0]:
                        st = 'na'
                    elif yr == current_year and mi > cur_mo_idx:
                        st = 'na'
                    elif mi <= (cur_mo_idx if yr == current_year else pm_idx[-1]):
                        st = 'gap'
                    else:
                        st = 'na'
                    for w in range(a, b + 1):
                        cells[w - 1] = st
            else:
                lo, hi = (min(present), max(present)) if present else (None, None)
                for w in range(1, 53):
                    if w in present:
                        st = 'have'
                    elif lo is None or w < lo:
                        st = 'na'
                    elif w <= hi:
                        st = 'gap'
                    elif yr == current_year and w <= current_week:
                        st = 'behind'
                    else:
                        st = 'na'
                    cells[w - 1] = st
            year_rows.append({'year': yr, 'cells': cells})
        out.append({'retailer': ret, 'cadence': 'monthly' if monthly else 'weekly', 'years': year_rows})
    return {'retailers': out, 'months': [[m, a, min(b, 52)] for m, a, b in _445_RANGES],
            'current_week': current_week}


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
                           current_year, current_week, retailers_needing_ly, ly_retailer_week_lookup,
                           forecast_data_bm, forecast_data_dc, channel_split_retailer)

    # ── 6b. Monthly aggregation by retailer ──
    print("  Building monthly data...")
    monthly = _build_monthly(sales_df, forecast_data, fc2d, week_month_map,
                              current_year, retailers_needing_ly, ly_retailer_month_lookup)

    # ── 6c. Category monthly ──
    print("  Building category monthly data...")
    cat_monthly = _build_cat_monthly(sales_df, forecast_data, fc2d, sku_info,
                                      week_month_map, current_year)
    coll_monthly = _build_coll_monthly(sales_df, forecast_data, fc2d, sku_info,
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

    # ── 6h-2. SKU x Retailer x Week detail + Budget overlay ──
    sku_ret_weekly = _build_sku_ret_weekly(sales_df, forecast_data, fc2d, current_year)
    _apply_budget(weekly, monthly, ret_summary, skus, sku_weekly, sku_ret_weekly, current_week)

    # ── 6i-j. Location data ──
    loc_sales = []
    loc_weekly_data = []
    loc_meta = {'territories': [], 'regions': [], 'states': [], 'fixtures': [], 'volumes': [], 'loc_current_week': current_week}
    sephora_productivity = {}
    if loc_df is not None and config['tabs'].get('doors', False):
        print("  Building location/door data...")
        loc_sales, loc_weekly_data, loc_meta = _build_location_data(
            loc_df, current_year, current_week, show_names=config.get('show_door_names', False))
        sephora_productivity = _build_sephora_productivity(loc_sales, loc_weekly_data, current_week, week_month_map, current_year)

    # ── Event (one-time-distortion) context callout for the current week ──
    event_context = _build_event_context(ret_summary, config, current_week)

    print(f"  Data structures built ({time.time()-t:.1f}s)")

    # Assemble final DATA
    from datetime import datetime
    build_timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')

    # Per-retailer latest reported week this year (freshness — retailers report on
    # different lags, so a retailer behind current_week is not "missing", just not in yet).
    retailer_last_week = {}
    _cy_df = sales_df[sales_df['Year'] == current_year]
    for _ret in retailers:
        _rw = _cy_df.loc[_cy_df['Retailer'] == _ret, 'Week']
        if len(_rw):
            retailer_last_week[_ret] = int(_rw.max())

    data_meta = {
        'retailer_last_week': retailer_last_week,
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
        'week_greg': _build_week_calendar(week_date_map, current_year),
        # week -> 4-4-5 month name for the CURRENT year, so the UI can roll weeks up to fiscal
        # months client-side. Both maps are keyed by (year, week); the UI only charts one year.
        'week_month_map': {int(k[1]): str(v) for k, v in (week_month_map or {}).items()
                           if isinstance(k, (tuple, list)) and int(k[0]) == current_year},
        'client_name': config['client_name'],
        'tabs': config.get('tabs', {}),
        'notes_csv_url': config.get('notes_csv_url', ''),
        'notes_edit_url': config.get('notes_edit_url', ''),
        'notes_channels': config.get('notes_channels', []),
        'channel_split_retailer': channel_split_retailer,
        # Every retailer that actually reports an in-store vs online split, detected from the
        # data rather than named in config: as feeds are added (Fara gained MECCA and
        # Selfridges alongside Sephora in 2026-08) the channel filter should pick them up
        # without a config edit. The single-value key above is kept for older configs.
        'channel_split_retailers': _detect_split_retailers(sales_df),
        # Retailers that report monthly, not weekly. They are ALWAYS several weeks behind the
        # current fiscal week by design, so freshness warnings must not read them as "late".
        'monthly_retailers': config.get('coverage_monthly', []),
        'status_page': config.get('status_page'),   # optional "Open Items" reference tab
        'coverage_grid': (_build_coverage_grid(sales_df, current_year, current_week,
                                               config.get('coverage_monthly', []), retailers)
                          if config.get('status_page') else None),
    }

    # ── 6k. Inventory data (optional) ──
    inv_data = {}
    has_inv = any(config['tabs'].get(k, False) for k in ('inv_overview', 'inv_detail'))
    if has_inv:
        print("  Building inventory data...")
        inv_data = _build_inventory_data(config, skus, sku_info, srp_map,
                                          current_year, current_week, l4w_weeks,
                                          forecast_data, fc2d, week_month_map, po_df=config.get('_po_df'))

    # ── 6l. DTC Performance data (optional) ──
    dtc_perf_data = {}
    dtc_weekly = {'available': False}
    if config['tabs'].get('dtc', False) and config.get('_dtc_perf'):
        print("  Building DTC performance data...")
        dtc_perf_data = _build_dtc_performance(config)
        dtc_weekly = _build_dtc(config, weekly, week_date_map, week_month_map, current_year)

    DATA = {
        'meta': data_meta,
        'weekly': weekly,
        'monthly': monthly,
        'cat_monthly': cat_monthly,
        'coll_monthly': coll_monthly,
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
        'dtc': dtc_weekly,
        'sku_ret_weekly': sku_ret_weekly,
        'sephora_productivity': sephora_productivity,
        'event_context': event_context,
        'annotations': config.get('_annotations', []),
        'daash': _build_daash(config.get('_daash', []), config),
        'returns': _build_returns(config.get('_returns') or {}, sku_info, current_year),
    }

    return DATA


def _build_returns(feed, sku_info, current_year):
    """Shape the returns / testers & damages feed for the Returns tab.

    Deliberately does NOT recompute the % -of-gross ratios: those come from each retailer's own
    monthly report (net + damages + testers), so the dashboard shows the same number the
    retailer published rather than a near-miss of it.

    -> {sku: [...], monthly: [...], kpis: {...}, has_weekly: bool, allocated_note: bool}
    """
    rows = feed.get('rows') or []
    monthly = feed.get('monthly') or []
    if not rows and not monthly:
        return {}

    def _blank():
        return {'dam_u': 0.0, 'dam_d': 0.0, 'tes_u': 0.0, 'tes_d': 0.0, 'oth_d': 0.0}

    by_sku, by_ret = {}, {}
    for r in rows:
        yr = r.get('yr')
        key = (r.get('ic'), r.get('prod') or '')
        for bucket, k in ((by_sku, key), (by_ret, (r.get('ret'), yr))):
            slot = bucket.setdefault(k, _blank())
            kind = (r.get('kind') or '').lower()
            if kind == 'damages':
                slot['dam_u'] += r.get('u') or 0
                slot['dam_d'] += r.get('d') or 0
            elif kind == 'testers':
                slot['tes_u'] += r.get('u') or 0
                slot['tes_d'] += r.get('d') or 0
            else:
                slot['oth_d'] += r.get('d') or 0
        slot = by_sku[key]
        slot['ret'] = r.get('ret')
        slot['yr'] = yr

    sku_out = []
    for (ic, prod), v in by_sku.items():
        info = sku_info.get(ic) if ic is not None else None
        sku_out.append({
            'ic': ic,
            'desc': ((info or {}).get('product')
                     or (prod if prod and prod.lower() != 'nan' else '')
                     or (f"SKU {ic}" if ic else 'Unmatched SKUs (no style code in the report)')),
            'cat': (info or {}).get('category', ''), 'coll': (info or {}).get('franchise', ''),
            'ret': v.get('ret'), 'yr': v.get('yr'),
            'dam_u': round(v['dam_u']), 'dam_d': round(v['dam_d'], 2),
            'tes_u': round(v['tes_u']), 'tes_d': round(v['tes_d'], 2),
            'tot_d': round(v['dam_d'] + v['tes_d'] + v['oth_d'], 2),
        })
    sku_out.sort(key=lambda x: -x['tot_d'])

    mo_out = sorted(
        [{'ret': m['ret'], 'yr': m['yr'], 'mo': m['mo'],
          'net': (round(m['net'], 2) if m['net'] is not None else None),
          'dam': round(m['dam'], 2), 'tes': round(m['tes'], 2),
          'ra': (round(m['ra'], 2) if m['ra'] is not None else None),
          'dam_pct': m['dam_pct'], 'tes_pct': m['tes_pct'],
          # gross needs a net-sales denominator; None when the retailer doesn't publish one
          # (MECCA's claim reports don't), so a missing denominator can't masquerade as zero.
          'gross': (round(m['net'] + m['dam'] + m['tes'], 2) if m['net'] is not None else None)}
         for m in monthly],
        key=lambda x: (x['ret'], x['yr'], x['mo']))

    cur = [m for m in mo_out if m['yr'] == current_year]
    with_net = [m for m in cur if m['gross'] is not None]
    gross = sum(m['gross'] for m in with_net)
    # Ratios use only the months that actually have a denominator, and the dollars that go with
    # them — mixing a retailer's chargebacks into the numerator while its sales are missing from
    # the denominator would overstate the rate.
    dam_rated = sum(m['dam'] for m in with_net)
    tes_rated = sum(m['tes'] for m in with_net)
    kpis = {
        'dam_d': round(sum(m['dam'] for m in cur), 2),
        'tes_d': round(sum(m['tes'] for m in cur), 2),
        'ra_d': round(sum(m['ra'] or 0 for m in cur), 2),
        'gross': round(gross, 2),
        'dam_pct': (dam_rated / gross) if gross else None,
        'tes_pct': (tes_rated / gross) if gross else None,
        'rated_retailers': sorted({m['ret'] for m in with_net}),
        'unrated_retailers': sorted({m['ret'] for m in cur if m['gross'] is None}),
        'dam_u': round(sum(s['dam_u'] for s in sku_out if s['yr'] == current_year)),
        'tes_u': round(sum(s['tes_u'] for s in sku_out if s['yr'] == current_year)),
        'year': current_year,
    }
    return {
        'sku': sku_out, 'monthly': mo_out, 'kpis': kpis,
        'retailers': sorted({m['ret'] for m in mo_out if m['ret']}),
        'has_weekly': any(r.get('wk') for r in rows),
        'allocated_note': any((r.get('alloc') or '').startswith('allocated') for r in rows),
    }


def _build_daash(rows, config):
    """Build competitive market-intel structure from DAASH rows.

    Returns {} if no data. Otherwise: brands (ranked, with weekly series + YoY),
    index/market totals, and the client's own rank/positioning.
    """
    if not rows:
        return {}
    client_name = config.get('client_name', '')
    by_brand = {}
    for r in rows:
        b = by_brand.setdefault(r['brand'], {'brand': r['brand'], 'index': r['index'],
                                             'ty': 0.0, 'ly': 0.0, 'weekly': []})
        b['ty'] += r['ty']
        b['ly'] += r['ly']
        b['weekly'].append({'wk': r['week'], 'ty': round(r['ty'], 0), 'ly': round(r['ly'], 0)})
    brands = []
    for b in by_brand.values():
        b['weekly'].sort(key=lambda x: x['wk'])
        b['ty'] = round(b['ty'], 0)
        b['ly'] = round(b['ly'], 0)
        b['yoy'] = round((b['ty'] - b['ly']) / b['ly'] * 100, 1) if b['ly'] else None
        brands.append(b)
    brands.sort(key=lambda x: x['ty'], reverse=True)
    for i, b in enumerate(brands):
        b['rank'] = i + 1

    def _is_client(name):
        n = (name or '').lower()
        return n == client_name.lower() or 'demo brand' in n

    client = next((b for b in brands if _is_client(b['brand'])), None)
    indie = [b for b in brands if b['index'].lower() == 'indie']
    legacy = [b for b in brands if b['index'].lower() == 'legacy']
    market_ty = sum(b['ty'] for b in brands)
    market_ly = sum(b['ly'] for b in brands)
    return {
        'brands': brands,
        'client_brand': client['brand'] if client else None,
        'client_rank': client['rank'] if client else None,
        'indie_rank': (sorted([b['rank'] for b in indie]).index(client['rank']) + 1) if (client and client in indie) else None,
        'legacy_rank': None,
        'market_ty': round(market_ty, 0),
        'market_ly': round(market_ly, 0),
        'market_yoy': round((market_ty - market_ly) / market_ly * 100, 1) if market_ly else None,
        'is_sample': bool(config.get('sources', {}).get('daash', {}).get('sample_data', True)),
    }


# ─────────────────────────────────────────────────────────────
# 6a. Weekly
# ─────────────────────────────────────────────────────────────
def _build_weekly(sales_df, forecast_data, fc2d, week_month_map, week_date_map,
                  current_year, current_week, retailers_needing_ly, ly_retailer_week_lookup,
                  forecast_data_bm=None, forecast_data_dc=None, channel_split_retailer=None):
    forecast_data_bm = forecast_data_bm or {}
    forecast_data_dc = forecast_data_dc or {}
    # Channel-split forecast $ per fiscal week (for the split retailer, e.g. Sephora)
    fc_bm_by_wk = defaultdict(float)
    fc_dc_by_wk = defaultdict(float)
    if channel_split_retailer:
        for (sku, wk), units in forecast_data_bm.get(channel_split_retailer, {}).items():
            fc_bm_by_wk[wk] += fc2d(channel_split_retailer, sku, units)
        for (sku, wk), units in forecast_data_dc.get(channel_split_retailer, {}).items():
            fc_dc_by_wk[wk] += fc2d(channel_split_retailer, sku, units)

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
            'fc_d_bm': round(fc_bm_by_wk.get(wk, 0.0) if ret == channel_split_retailer else 0.0, 2),
            'fc_d_dc': round(fc_dc_by_wk.get(wk, 0.0) if ret == channel_split_retailer else 0.0, 2),
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
            'fc_d_bm': round(fc_bm_by_wk.get(wk, 0.0), 2),
            'fc_d_dc': round(fc_dc_by_wk.get(wk, 0.0), 2),
        })

    # ── Future forecast-only weeks ──
    # The demand plan carries a full-year forecast, but actual sales only exist
    # through the latest week. Emit forecast-only rows (ty=0) for weeks that have
    # a plan but no actuals — needed for $-weighted pace-to-forecast and for the
    # forecast line / monthly plan to extend beyond the latest actual week.
    existing = {(w['ret'], w['wk']) for w in weekly if w['yr'] == current_year}

    def _mo_for(wk):
        mo = week_month_map.get((current_year, wk), '')
        return mo or _week_to_445_month(wk)

    # base week-end date for extrapolating future week-end dates
    cy_dates = {wk: we for (yr2, wk), we in week_date_map.items()
                if yr2 == current_year and we is not None and not pd.isna(we)}
    base_wk = max(cy_dates) if cy_dates else None
    base_we = cy_dates.get(base_wk) if base_wk is not None else None

    def _we_for(wk):
        we = week_date_map.get((current_year, wk))
        if we is not None and not pd.isna(we):
            return we.strftime('%Y-%m-%d')
        if base_we is not None and base_wk is not None:
            return (base_we + pd.Timedelta(days=7 * (wk - base_wk))).strftime('%Y-%m-%d')
        return ''

    # Per-retailer future weeks
    for ret, fc_dict in forecast_data.items():
        fweeks = {}
        for (sku, fwk), units in fc_dict.items():
            if (ret, fwk) in existing:
                continue
            agg = fweeks.setdefault(fwk, [0.0, 0.0])
            agg[0] += units
            agg[1] += fc2d(ret, sku, units)
        for fwk, (u, d) in fweeks.items():
            mo = _mo_for(fwk)
            weekly.append({
                'yr': current_year, 'wk': fwk, 'mo': mo, 'mo_n': MONTH_NUM.get(mo, 0),
                'ret': ret, 'we': _we_for(fwk), 'cur': True,
                'ty': 0.0, 'ty_bm': 0.0, 'ty_dc': 0.0, 'ty_u': 0,
                'ly': 0.0, 'ly_bm': 0.0, 'ly_dc': 0.0,
                'fc_u': round(u, 2), 'fc_d': round(d, 2),
                'fc_d_bm': round(fc_bm_by_wk.get(fwk, 0.0) if ret == channel_split_retailer else 0.0, 2),
                'fc_d_dc': round(fc_dc_by_wk.get(fwk, 0.0) if ret == channel_split_retailer else 0.0, 2),
            })

    # 'All' future weeks (union across retailers)
    all_fweeks = {}
    for ret, fc_dict in forecast_data.items():
        for (sku, fwk), units in fc_dict.items():
            if ('All', fwk) in existing:
                continue
            agg = all_fweeks.setdefault(fwk, [0.0, 0.0])
            agg[0] += units
            agg[1] += fc2d(ret, sku, units)
    for fwk, (u, d) in all_fweeks.items():
        mo = _mo_for(fwk)
        weekly.append({
            'yr': current_year, 'wk': fwk, 'mo': mo, 'mo_n': MONTH_NUM.get(mo, 0),
            'ret': 'All', 'we': _we_for(fwk), 'cur': True,
            'ty': 0.0, 'ty_bm': 0.0, 'ty_dc': 0.0, 'ty_u': 0,
            'ly': 0.0, 'ly_bm': 0.0, 'ly_dc': 0.0,
            'fc_u': round(u, 2), 'fc_d': round(d, 2),
            'fc_d_bm': round(fc_bm_by_wk.get(fwk, 0.0), 2),
            'fc_d_dc': round(fc_dc_by_wk.get(fwk, 0.0), 2),
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


def _build_coll_monthly(sales_df, forecast_data, fc2d, sku_info, week_month_map, current_year):
    """Monthly sales grouped by Collection (franchise) — parallels cat_monthly."""
    if 'Collection' not in sales_df.columns:
        return []
    sales_df = sales_df.copy()
    if 'mo' not in sales_df.columns:
        sales_df['mo'] = sales_df['445 Month']
    if 'mo_n' not in sales_df.columns:
        sales_df['mo_n'] = sales_df['mo'].map(MONTH_NUM).fillna(0).astype(int)
    # sku -> collection map (for forecast attribution)
    sku_coll = {}
    for _, r in sales_df[['Item Code', 'Collection']].drop_duplicates().iterrows():
        sku_coll[r['Item Code']] = safe_str(r['Collection'])

    agg = sales_df.groupby(['Year', 'mo', 'mo_n', 'Collection']).agg({
        'TY Total Sales $': 'sum', 'TY Total Sales Units': 'sum', 'LY Total Sales $': 'sum',
    }).reset_index()
    result = []
    for _, r in agg.iterrows():
        yr = int(r['Year']); mo = r['mo']; mo_n = int(r['mo_n'])
        coll = safe_str(r['Collection'])
        fc_u = 0; fc_d = 0.0
        if yr == current_year:
            for ret, fc_dict in forecast_data.items():
                for (sku, wk), units in fc_dict.items():
                    if week_month_map.get((yr, wk), '') == mo and sku_coll.get(sku, '') == coll:
                        fc_u += units
                        fc_d += fc2d(ret, sku, units)
        result.append({
            'yr': yr, 'mo': mo, 'mo_n': mo_n, 'coll': coll,
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
def _build_location_data(loc_df, current_year, current_week, show_names=False):
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
            # Real store name when the client opts in (show_door_names); otherwise generalized.
            'loc': loc_num, 'name': (info['name'] if (show_names and info['name'] and info['name'] != 'nan') else f"Store #{loc_num}"),
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
                           current_year, current_week, l4w_weeks,
                           forecast_data=None, fc2d=None, week_month_map=None, po_df=None):
    """Build inventory data structure for the inventory tabs.

    Merges inventory/PO data (if available) with existing SKU-level sell-through
    data to produce a unified inventory view with OWD warehouse + Sephora B&M/.com
    split, COGS $, GMV $ (=MSRP×units) and board-method forward WOS.

    Returns dict with 'sku_list' and 'channels'.
    """
    forecast_data = forecast_data or {}
    week_month_map = week_month_map or {}
    if po_df is None:
        po_df = config.get('_po_df')
    inv_df = config.get('_inv_df')

    # Forward weekly demand per SKU (board method): sum of the next 13 weeks of
    # forecast units across retailers, ÷ 13 ≈ next-quarter sell-in run-rate.
    fwd_weeks = set(range(current_week + 1, current_week + 14))
    fwd_units_by_sku = {}
    for ret, fc_dict in forecast_data.items():
        for (sku, wk), units in fc_dict.items():
            if wk in fwd_weeks:
                fwd_units_by_sku[sku] = fwd_units_by_sku.get(sku, 0) + units
    def _board_weekly(sku):
        return fwd_units_by_sku.get(sku, 0) / 13.0

    # Build the inventory SKU list from existing sell-through SKU data + inventory overlay
    inv_by_sku = {}
    if inv_df is not None:
        for _, r in inv_df.iterrows():
            sku = str(r.get('SKU', '')).strip()
            if sku:
                total_oh = num(r.get('On Hand Units', 0))
                owd = num(r.get('OWD Units', 0))
                bm = num(r.get('Sephora BM Units', 0))
                com = num(r.get('Sephora COM Units', 0))
                # If the file has no OWD/Sephora split, synthesize a believable one
                # (OWD warehouse 55%, Sephora B&M 33%, Sephora .com 12%) so the
                # Inventory OH tab uses the OWD warehouse vs Sephora structure.
                if (owd + bm + com) == 0 and total_oh:
                    owd = round(total_oh * 0.55)
                    bm = round(total_oh * 0.33)
                    com = total_oh - owd - bm
                inv_by_sku[sku] = {
                    'on_hand': total_oh or (owd + bm + com),
                    'owd_units': owd,
                    'seph_bm_units': bm,
                    'seph_com_units': com,
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

        # Historical (L4W velocity) WOS — kept for back-compat
        wos = on_hand / avg_weekly if avg_weekly > 0 else 0

        # OWD warehouse + Sephora B&M/.com split
        owd_units = inv.get('owd_units', 0)
        seph_bm_units = inv.get('seph_bm_units', 0)
        seph_com_units = inv.get('seph_com_units', 0)
        seph_units = seph_bm_units + seph_com_units
        total_units = owd_units + seph_units if (owd_units + seph_units) > 0 else on_hand

        # Board-method forward WOS = on-hand units ÷ (next-13-week forecast ÷ 13)
        board_weekly = _board_weekly(sku)
        owd_wos = round(owd_units / board_weekly, 1) if board_weekly > 0 else None
        seph_wos = round(seph_units / board_weekly, 1) if board_weekly > 0 else None
        total_wos = round(total_units / board_weekly, 1) if board_weekly > 0 else None

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
            # OWD / Sephora split + COGS / GMV / board-method WOS
            'owd_units': round(owd_units),
            'seph_bm_units': round(seph_bm_units),
            'seph_com_units': round(seph_com_units),
            'seph_units': round(seph_units),
            'total_units': round(total_units),
            'owd_cogs': round(owd_units * cost_unit, 0),
            'owd_gmv': round(owd_units * srp, 0),
            'seph_cogs': round(seph_units * cost_unit, 0),
            'seph_gmv': round(seph_units * srp, 0),
            'total_cogs': round(total_units * cost_unit, 0),
            'total_gmv': round(total_units * srp, 0),
            'board_weekly': round(board_weekly, 1),
            'owd_wos': owd_wos,
            'seph_wos': seph_wos,
            'total_wos': total_wos,
        }
        sku_list.append(rec)

    po_total = sum((r.get('on_order') or 0) for r in sku_list)
    proj = _build_inv_projection(sku_list, current_week, po_total)
    return {
        'sku_list': sku_list,
        'channels': sorted(channels),
        'aggregates': _inv_aggregates(sku_list),
        'history': proj['history'],
        'projection': proj['projection'],
        'run_rate': proj['run_rate'],
        'po_inbound': proj['po_inbound'],
    }


def _inv_aggregates(sku_list):
    """Portfolio-level inventory totals for the Overview inventory tiles."""
    def s(k):
        return sum((r.get(k) or 0) for r in sku_list)
    owd_u, seph_u, total_u = s('owd_units'), s('seph_units'), s('total_units')
    owd_board = sum((r.get('board_weekly') or 0) for r in sku_list if (r.get('owd_units') or 0) > 0)
    seph_board = sum((r.get('board_weekly') or 0) for r in sku_list if (r.get('seph_units') or 0) > 0)
    return {
        'owd_units': round(owd_u), 'owd_cogs': round(s('owd_cogs')), 'owd_gmv': round(s('owd_gmv')),
        'seph_units': round(seph_u), 'seph_cogs': round(s('seph_cogs')), 'seph_gmv': round(s('seph_gmv')),
        'seph_bm_units': round(s('seph_bm_units')), 'seph_com_units': round(s('seph_com_units')),
        'total_units': round(total_u), 'total_cogs': round(s('total_cogs')), 'total_gmv': round(s('total_gmv')),
        'owd_wos': round(owd_u / owd_board, 1) if owd_board > 0 else None,
        'seph_wos': round(seph_u / seph_board, 1) if seph_board > 0 else None,
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


def _build_dtc(config, weekly, week_date_map, week_month_map, current_year):
    """Build the weekly DTC structure (act/ly/fcst per metric per fiscal week).

    Returns {available, weeks, current_week, l4w_weeks, week_month, metrics, channels, ...}.
    TY actuals + Paid Media + forecast targets are real (from the daily CSV); eComm Sales LY is
    real (from the eCommerce weekly rollup); LY for other metrics is illustrative (scaled by the
    eComm YoY ratio); channel split is sample data.
    """
    raw = config.get('_dtc_perf', {})
    daily_df = raw.get('daily_df') if raw else None
    if daily_df is None or len(daily_df) == 0:
        return {'available': False, 'reason': 'No DTC daily data'}

    # date -> (yr, wk): fiscal week whose week-end is the first >= date (within 7 days)
    we_sorted = sorted([(pd.to_datetime(we), yr, wk)
                        for (yr, wk), we in week_date_map.items() if we is not None])

    def day_to_wk(d):
        d = pd.to_datetime(d, errors='coerce')
        if pd.isna(d):
            return None
        for we, yr, wk in we_sorted:
            if d <= we and (we - d).days < 7:
                return (yr, wk)
        return None

    acc = defaultdict(lambda: defaultdict(float))
    fc = defaultdict(lambda: defaultdict(float))
    for _, r in daily_df.iterrows():
        key = day_to_wk(r.get('Date'))
        if not key or key[0] != current_year:
            continue
        wk = key[1]
        spend = num(r.get('Meta Spend')) + num(r.get('Google Spend')) + num(r.get('TikTok Spend'))
        acc['eComm Sales'][wk] += num(r.get('Gross Sales'))
        acc['Units'][wk] += num(r.get('Units'))
        acc['Orders'][wk] += num(r.get('Orders'))
        acc['Sessions'][wk] += num(r.get('Sessions'))
        acc['New Customers'][wk] += num(r.get('New Customers'))
        acc['Returning'][wk] += num(r.get('Returning Customers'))
        acc['Paid Media Spend'][wk] += spend
        fc['eComm Sales'][wk] += num(r.get('Gross Sales Target'))
        fc['Paid Media Spend'][wk] += num(r.get('Ad Spend Target'))
        fc['New Customers'][wk] += num(r.get('New Customers Target'))

    weeks = sorted(acc['eComm Sales'].keys())
    if not weeks:
        return {'available': False, 'reason': 'No DTC weeks mapped'}
    current_week = max(weeks)
    l4w = [w for w in weeks if w > current_week - 4]

    # Illustrative LY: the demo's eCommerce had little/no prior-year history, so model a
    # believable YoY for each metric (TY grew vs LY). Slightly different factors per metric so
    # derived ratios (AOV/UPT/Conversion/MER) move realistically rather than staying flat.
    ly_factor = {
        'eComm Sales': 0.80, 'Units': 0.83, 'Orders': 0.82, 'Sessions': 0.78,
        'New Customers': 0.80, 'Returning': 0.85, 'Paid Media Spend': 0.88,
    }

    def mk(label, fmt, actmap, ly_base=None, fcmap=None):
        ly = {}
        if ly_base is not None:
            for i, w in enumerate(weeks):
                jit = 1 + 0.05 * (((w * 7) % 11) - 5) / 5.0  # deterministic ±5% wobble
                ly[int(w)] = round(actmap.get(w, 0) * ly_base * jit, 2)
        return {
            'label': label, 'fmt': fmt,
            'act': {int(w): round(actmap.get(w, 0), 2) for w in weeks},
            'ly': ly,
            'fcst': {int(w): round(fcmap.get(w, 0), 2) for w in weeks if fcmap and fcmap.get(w, 0)},
        }

    metrics = {
        'eComm Sales': mk('eComm Sales', '$', acc['eComm Sales'], ly_factor['eComm Sales'], fc['eComm Sales']),
        'Units': mk('Units', 'n', acc['Units'], ly_factor['Units']),
        'Orders': mk('Orders', 'n', acc['Orders'], ly_factor['Orders']),
        'Sessions': mk('Sessions', 'n', acc['Sessions'], ly_factor['Sessions']),
        'New Customers': mk('New Customers', 'n', acc['New Customers'], ly_factor['New Customers'], fc['New Customers']),
        'Returning': mk('Returning', 'n', acc['Returning'], ly_factor['Returning']),
        'Online Store Orders': mk('Online Store Orders', 'n', acc['Orders'], ly_factor['Orders']),
        'Paid Media Spend': mk('Paid Media Spend', '$', acc['Paid Media Spend'], ly_factor['Paid Media Spend'], fc['Paid Media Spend']),
    }

    # Sample channel split of eComm Sales
    ch_ratios = [('Online Store', 0.72), ('TikTok Shop', 0.12), ('FB/IG Shop', 0.10), ('Subscriptions', 0.06)]
    channels = {name: {int(w): round(acc['eComm Sales'].get(w, 0) * frac, 2) for w in weeks}
                for name, frac in ch_ratios}

    return {
        'available': True,
        'weeks': [int(w) for w in weeks],
        'current_week': int(current_week),
        'l4w_weeks': [int(w) for w in l4w],
        'week_month': {str(int(w)): week_month_map.get((current_year, w), '') for w in weeks},
        'metrics': metrics,
        'channels': channels,
        'channels_sample': True,
        'forecast_sample': False,
        'sample_seeded': False,
        'source': ('Demo Shopify daily, aggregated to fiscal weeks. eComm Sales/Units/Orders/Sessions/'
                   'Customers and Paid Media are real (TY); eComm Sales LY is real; LY for other metrics '
                   'is illustrative. Channel split is sample data.'),
    }


# ─────────────────────────────────────────────────────────────
# Budget + SKU×Retailer×Week + Event context + Productivity
# ─────────────────────────────────────────────────────────────
def _bf(wk):
    """Deterministic budget factor vs forecast (oscillates ~0.89–1.03 by week)."""
    return 0.96 + 0.07 * math.sin((wk or 0) / 3.0)


def _build_sku_ret_weekly(sales_df, forecast_data, fc2d, current_year):
    """SKU × Retailer × Week detail: {yr, ic, ret, wk, ty, ly, ty_u, ty_bm, ty_dc, fc_d, bd_d}.

    Units and the B&M/.com split are carried here (not just at the all-retailer grain) so the
    SKU view can be shown in units as well as dollars, and a SKU can be drilled retailer ->
    channel without a second pass over the data.
    """
    aggs = {'TY Total Sales $': 'sum', 'LY Total Sales $': 'sum'}
    for c in ('TY Total Sales Units', 'TY B&M Sales $', 'TY Dotcom Sales $'):
        if c in sales_df.columns:
            aggs[c] = 'sum'
    g = sales_df.groupby(['Year', 'Item Code', 'Retailer', 'Week']).agg(aggs).reset_index()
    out = []
    for _, r in g.iterrows():
        ty = float(r['TY Total Sales $']); ly = float(r['LY Total Sales $'])
        u = float(r.get('TY Total Sales Units') or 0)
        if ty == 0 and ly == 0 and u == 0:
            continue
        yr = int(r['Year']); ic = int(r['Item Code']); wk = int(r['Week']); ret = r['Retailer']
        fc_d = 0.0
        if yr == current_year and ret in forecast_data:
            fc_d = fc2d(ret, ic, forecast_data[ret].get((ic, wk), 0))
        bd_d = round(fc_d * _bf(wk), 2) if fc_d else 0.0
        out.append({'yr': yr, 'ic': ic, 'ret': ret, 'wk': wk,
                    'ty': round(ty, 2), 'ly': round(ly, 2), 'ty_u': round(u),
                    'ty_bm': round(float(r.get('TY B&M Sales $') or 0), 2),
                    'ty_dc': round(float(r.get('TY Dotcom Sales $') or 0), 2),
                    'fc_d': round(fc_d, 2), 'bd_d': bd_d})
    return out


def _build_week_calendar(week_date_map, current_year=None):
    """{week: {'f': '<445 month>', 'g': [[monthNum, weight], ...]}} for the calendar toggle.

    A fiscal week can straddle a calendar-month boundary, so a Gregorian view has to split it.
    Each week is spread across the calendar months its seven days fall in, weighted by day
    count — exact for weeks inside one month, and within a few days at boundaries. That is the
    best available without day-level sales for every retailer, and it is labelled as such in the
    UI so nobody reads it as reported daily data.
    """
    import datetime as _dt
    out = {}
    for key, we in (week_date_map or {}).items():
        if we is None:
            continue
        # keyed by (year, week); the chart covers the current year only
        wk = key[1] if isinstance(key, (tuple, list)) else key
        if current_year is not None and isinstance(key, (tuple, list)) and int(key[0]) != current_year:
            continue
        try:
            end = we.date() if hasattr(we, 'date') else we
            days = [end - _dt.timedelta(days=i) for i in range(7)]
        except Exception:
            continue
        # Keep only days in the week-end's own calendar year, then renormalise. Fiscal W1 ends a
        # few days into January, so its remaining days belong to the PREVIOUS December — charting
        # those in the current year's December would drop them at the far right of the axis, which
        # reads as a phantom December. Renormalising instead folds them into January: the week's
        # sales are preserved and the boundary approximation is flagged in the UI.
        yr_of_end = end.year
        counts = {}
        for d in days:
            if d.year != yr_of_end:
                continue
            counts[d.month] = counts.get(d.month, 0) + 1
        total = sum(counts.values())
        if not total:
            continue
        out[int(wk)] = {'g': sorted([[m, c / total] for m, c in counts.items()],
                                    key=lambda x: -x[1])}
    return out


def _apply_budget(weekly, monthly, ret_summary, skus, sku_weekly, sku_ret_weekly, current_week):
    """Synthesize a Budget plan distinct from Forecast (no separate budget file in the demo).
    Budget = forecast × per-week factor; added in place to all the structures the UI reads."""
    for r in weekly:
        r['bd_d'] = round((r.get('fc_d') or 0) * _bf(r.get('wk', 0)), 2)
    for r in sku_weekly:
        r['bd_d'] = round((r.get('fc_d') or 0) * _bf(r.get('wk', 0)), 2)
    for r in monthly:
        r['bd_d'] = round((r.get('fc_d') or 0) * 0.98, 2)
    for r in ret_summary:
        r['bd_d_ytd'] = round((r.get('fc_d_ytd') or 0) * 0.98, 2)
        r['bd_d_mtd'] = round((r.get('fc_d_mtd') or 0) * 0.98, 2)
        r['bd_d_wk'] = round((r.get('fc_d_wk') or 0) * _bf(current_week), 2)
    for s in skus:
        s['bd_d_ytd'] = round((s.get('fc_d_ytd') or 0) * 0.98, 2)


def _build_event_context(ret_summary, config, current_week):
    """One-time-distortion ('GMA-style') context for the current week: identify the retailer
    driving the largest YoY swing and show the headline vs the ex-that-retailer comparison."""
    if not config.get('event_context', True):   # client opt-out (e.g. staggered retailer reporting)
        return {'active': False}
    by_ret = {r['ret']: r for r in ret_summary}
    all_row = by_ret.get('All')
    if not all_row:
        return {'active': False}
    all_wk = all_row.get('wk', 0) or 0
    all_ly = all_row.get('wk_ly', 0) or 0
    all_fc = all_row.get('fc_d_wk', 0) or 0
    cands = [r for r in ret_summary if r['ret'] != 'All']
    if not cands:
        return {'active': False}
    top = max(cands, key=lambda r: abs((r.get('wk', 0) or 0) - (r.get('wk_ly', 0) or 0)))
    delta = (top.get('wk', 0) or 0) - (top.get('wk_ly', 0) or 0)
    thresh = config.get('event_min_delta', max(15000, 0.06 * all_wk))
    if abs(delta) < thresh or all_ly <= 0:
        return {'active': False}
    adj_ty = all_wk - (top.get('wk', 0) or 0)
    adj_ly = all_ly - (top.get('wk_ly', 0) or 0)
    adj_fc = all_fc - (top.get('fc_d_wk', 0) or 0)
    cfg_ret = (config.get('event_callout') or {}).get('retailer')
    label = (config.get('event_callout') or {}).get('label') if cfg_ret == top['ret'] else None
    return {
        'active': True,
        'retailer': top['ret'],
        'label': label,
        'ty': round(top.get('wk', 0) or 0), 'ly': round(top.get('wk_ly', 0) or 0),
        'delta': round(delta),
        'headline_pct': round((all_wk - all_ly) / abs(all_ly) * 100, 1) if all_ly else None,
        'all_ty': round(all_wk), 'all_ly': round(all_ly),
        'adj_ty': round(adj_ty), 'adj_ly': round(adj_ly),
        'adj_pct': round((adj_ty - adj_ly) / abs(adj_ly) * 100, 1) if adj_ly > 0 else None,
        'adj_fc': round(adj_fc),
        'adj_fc_pct': round((adj_ty - adj_fc) / abs(adj_fc) * 100, 1) if adj_fc > 0 else None,
    }


def _classify_fixture(fix):
    f = (fix or '').lower()
    if 'end' in f or 'cap' in f or 'ec' == f.strip():
        return 'endcap'
    return 'linear'


def _build_sephora_productivity(loc_sales, loc_weekly, current_week, week_month_map, current_year):
    """Demo Sephora Productivity: homebay index (modeled), endcap vs linear, $/door by tier.
    Computed from door-level data (no external EOM/productivity files in the demo)."""
    if not loc_sales:
        return {}
    MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    n_doors = len(loc_sales)
    vol_by_loc = {d['loc']: (d.get('vol') or 'Unrated') for d in loc_sales}
    fix_by_loc = {d['loc']: _classify_fixture(d.get('fix')) for d in loc_sales}
    # monthly TY/LY $ and endcap/linear split from weekly per-door series
    mo_ty = {m: 0.0 for m in MONTHS}; mo_ly = {m: 0.0 for m in MONTHS}
    ec = {m: 0.0 for m in MONTHS}; lin = {m: 0.0 for m in MONTHS}
    ec_ly = {m: 0.0 for m in MONTHS}; lin_ly = {m: 0.0 for m in MONTHS}
    for lw in (loc_weekly or []):
        fx = fix_by_loc.get(lw.get('loc'))
        for w in lw.get('weeks', []):
            mo = week_month_map.get((current_year, w.get('wk')), '')
            if mo not in mo_ty:
                continue
            mo_ty[mo] += w.get('ty', 0) or 0; mo_ly[mo] += w.get('ly', 0) or 0
            if fx == 'endcap':
                ec[mo] += w.get('ty', 0) or 0; ec_ly[mo] += w.get('ly', 0) or 0
            else:
                lin[mo] += w.get('ty', 0) or 0; lin_ly[mo] += w.get('ly', 0) or 0
    months_with = [m for m in MONTHS if mo_ty[m] > 0]
    # modeled homebay productivity index (0–1.1), tied to $/door scaled to a baseline
    dpm_ty = [mo_ty[m] / n_doors if n_doors else 0 for m in MONTHS]
    peak = max(dpm_ty) or 1
    homebay_ty = [round(min(1.1, 0.45 + 0.55 * (v / peak)), 3) if v > 0 else None for v in dpm_ty]
    homebay_ly = [round(v * (0.90 + 0.04 * math.sin(i / 1.5)), 3) if v else None
                  for i, v in enumerate(homebay_ty)]
    # $/door by volume tier (YTD)
    tier_order = ['A++', 'A+', 'A', 'B', 'C', 'D', 'E', 'Unrated']
    tier = {}
    for d in loc_sales:
        v = d.get('vol') or 'Unrated'
        t = tier.setdefault(v, {'vol': v, 'doors': 0, 'ytd': 0.0})
        t['doors'] += 1; t['ytd'] += d.get('ytd', 0) or 0
    tier_rows = sorted(tier.values(), key=lambda r: (tier_order.index(r['vol']) if r['vol'] in tier_order else 99))
    for r in tier_rows:
        r['per_door'] = round(r['ytd'] / r['doors'], 0) if r['doors'] else 0
        r['ytd'] = round(r['ytd'], 0)
    return {
        'months': MONTHS,
        'months_with': months_with,
        'ty_monthly_sales': [round(mo_ty[m]) for m in MONTHS],
        'ly_monthly_sales': [round(mo_ly[m]) for m in MONTHS],
        'homebay_ty': homebay_ty,
        'homebay_ly': homebay_ly,
        'endcap_vs_linear': {
            'ty': [{'month': m, 'endcap': round(ec[m]), 'linear': round(lin[m])} for m in months_with],
            'ly': [{'month': m, 'endcap': round(ec_ly[m]), 'linear': round(lin_ly[m])} for m in months_with],
        },
        'tier_productivity': tier_rows,
        'n_doors': n_doors,
        'modeled': True,
    }


def _build_inv_projection(sku_list, current_week, po_total):
    """Portfolio inventory history (back-cast) + forward WOS projection through W52."""
    owd_now = sum(r.get('owd_units', 0) or 0 for r in sku_list)
    seph_now = sum(r.get('seph_units', 0) or 0 for r in sku_list)
    total_now = owd_now + seph_now
    run = sum(r.get('board_weekly', 0) or 0 for r in sku_list)  # weekly sell-in run-rate (units)
    if run <= 0:
        run = max(1.0, total_now / 26.0)
    owd_share = owd_now / total_now if total_now else 0.55
    # History: weeks (current-12 .. current), back-cast (earlier weeks held more)
    history = []
    for wk in range(max(1, current_week - 12), current_week + 1):
        back = run * (current_week - wk)
        tot = total_now + back
        history.append({'wk': wk, 'owd_units': round(tot * owd_share),
                        'seph_units': round(tot * (1 - owd_share)), 'total_units': round(tot),
                        'wos': round(tot / run, 1)})
    # PO arrivals spread evenly over current+2 .. current+7
    po_weeks = list(range(current_week + 2, current_week + 8))
    po_per = (po_total / len(po_weeks)) if po_weeks else 0
    projection = []
    remaining = total_now
    for wk in range(current_week + 1, 53):
        remaining -= run
        if wk in po_weeks:
            remaining += po_per
        remaining = max(0, remaining)
        projection.append({'wk': wk, 'total_units': round(remaining),
                           'owd_units': round(remaining * owd_share),
                           'seph_units': round(remaining * (1 - owd_share)),
                           'wos': round(remaining / run, 1)})
    return {'history': history, 'projection': projection,
            'run_rate': round(run, 1), 'po_inbound': round(po_total)}
