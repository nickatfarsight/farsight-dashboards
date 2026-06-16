"""Slack message and email snapshot generation for sell-through dashboard."""

import os
from datetime import datetime

from .core import fmtK, pct_or_na


def build_outputs(config, data, output_dir):
    """Generate Slack message and email HTML snapshot.

    Args:
        config: Client config dict
        data: The DATA dict from aggregate.build_all()
        output_dir: Directory to write slack_message.txt and email_snapshot.html
    """
    print("Generating Slack message and email snapshot...")

    client_name = config['client_name']
    dashboard_url = config.get('dashboard_url', '')
    current_year = data['meta']['current_year']
    current_week = data['meta']['current_week']
    current_month = data['meta']['current_month']
    current_week_end = data['meta']['current_week_end']
    ret_summary = data['ret_summary']
    skus = data['skus']
    new_launches = data['new_launches']

    now = datetime.now()

    # Get "All" retailer summary
    _all_ret = next((r for r in ret_summary if r['ret'] == 'All'), None)
    if not _all_ret:
        print("  WARNING: No 'All' retailer summary found — skipping outputs")
        return

    week_total = _all_ret['wk']
    week_ly = _all_ret['wk_ly']
    ytd_total = _all_ret['ytd']
    ytd_ly = _all_ret['ytd_ly']
    ytd_fc = _all_ret['fc_d_ytd']
    mtd_total = _all_ret['mtd']
    mtd_ly = _all_ret['mtd_ly']

    week_vs_ly = ((week_total - week_ly) / abs(week_ly) * 100) if week_ly != 0 else 0
    ytd_vs_ly = ((ytd_total - ytd_ly) / abs(ytd_ly) * 100) if ytd_ly != 0 else 0
    ytd_vs_fc = ((ytd_total - ytd_fc) / abs(ytd_fc) * 100) if ytd_fc != 0 else 0

    # Per-retailer lines
    _ret_lines_slack = []
    _ret_rows_email = []
    for rs in ret_summary:
        if rs['ret'] == 'All':
            continue
        rname = rs['ret']
        r_wk = rs['wk']
        r_wk_ly = rs['wk_ly']
        r_ytd = rs['ytd']
        r_ytd_ly = rs['ytd_ly']
        r_wk_vs_ly = ((r_wk - r_wk_ly) / abs(r_wk_ly) * 100) if r_wk_ly != 0 else 0
        r_ytd_vs_ly = ((r_ytd - r_ytd_ly) / abs(r_ytd_ly) * 100) if r_ytd_ly != 0 else 0
        _ret_lines_slack.append(
            f"  {rname}: *${r_wk:,.0f}* wk ({r_wk_vs_ly:+.1f}% vs LY) · *${r_ytd:,.0f}* YTD ({r_ytd_vs_ly:+.1f}% vs LY)"
        )
        _ret_rows_email.append({
            'name': rname,
            'wk': r_wk, 'wk_ly': r_wk_ly, 'wk_vs_ly': r_wk_vs_ly,
            'mtd': rs['mtd'], 'mtd_ly': rs['mtd_ly'],
            'ytd': r_ytd, 'ytd_ly': r_ytd_ly, 'ytd_vs_ly': r_ytd_vs_ly,
            'fc_wk': rs.get('fc_d_wk', 0), 'fc_ytd': rs.get('fc_d_ytd', 0),
        })

    # Top 3 SKUs
    _top3 = sorted(skus, key=lambda x: x['wk'], reverse=True)[:3]
    _top3_lines = [f"{s['desc']} (${s['wk']:,.0f})" for s in _top3]

    # ── Slack message ──
    _slack_msg = f"""*{client_name} — Performance Update* | Week {current_week}, {current_month} FY{current_year}

*Snapshot*
• Week Sales: *${week_total:,.0f}* (vs LY {week_vs_ly:+.1f}%)
• YTD Sales: *${ytd_total:,.0f}* (vs LY {ytd_vs_ly:+.1f}% · vs Fcst {ytd_vs_fc:+.1f}%)

*By Customer*
{chr(10).join(_ret_lines_slack)}

*Top Sellers LW*
{"".join("• " + l + chr(10) for l in _top3_lines)}
"""
    if dashboard_url:
        _slack_msg += f":bar_chart: *Full Dashboard →* {dashboard_url}\n"
    _slack_msg += f"\n_Updated {now.strftime('%B %d, %Y')} at {now.strftime('%I:%M %p')} — verify key figures before sharing._"

    slack_path = os.path.join(output_dir, 'slack_message.txt')
    with open(slack_path, 'w', encoding='utf-8') as f:
        f.write(_slack_msg.strip() + '\n')
    print(f"  Slack message: {slack_path}")

    # ── Email snapshot ──
    _build_email(config, data, _ret_rows_email, _all_ret, _top3_lines, now, output_dir)


def _build_email(config, data, _ret_rows_email, _all_ret, _top3_lines, now, output_dir):
    """Build email HTML snapshot."""
    client_name = config['client_name']
    primary_color = config['branding'].get('primary_color', '#4A90D9')
    dashboard_url = config.get('dashboard_url', '')
    current_year = data['meta']['current_year']
    current_week = data['meta']['current_week']
    current_month = data['meta']['current_month']
    current_week_end = data['meta']['current_week_end']

    week_total = _all_ret['wk']
    week_ly = _all_ret['wk_ly']
    ytd_total = _all_ret['ytd']
    ytd_ly = _all_ret['ytd_ly']
    ytd_fc = _all_ret['fc_d_ytd']

    week_vs_ly = ((week_total - week_ly) / abs(week_ly) * 100) if week_ly != 0 else 0
    ytd_vs_ly = ((ytd_total - ytd_ly) / abs(ytd_ly) * 100) if ytd_ly != 0 else 0
    ytd_vs_fc = ((ytd_total - ytd_fc) / abs(ytd_fc) * 100) if ytd_fc != 0 else 0

    # Build table rows
    _td = 'style="padding:8px 10px;border-bottom:1px solid #eee;text-align:right;font-size:13px;"'
    _td_name = 'style="padding:8px 10px;border-bottom:1px solid #eee;font-weight:500;font-size:13px;white-space:nowrap;"'
    _td_pct = 'padding:8px 10px;border-bottom:1px solid #eee;text-align:right;font-size:13px;color:'

    _rows_html = ""
    for rr in _ret_rows_email:
        wk_pct, wk_clr = pct_or_na(rr['wk'], rr['wk_ly'])
        wk_fc_pct, wk_fc_clr = pct_or_na(rr['wk'], rr['fc_wk'])
        ytd_pct, ytd_clr = pct_or_na(rr['ytd'], rr['ytd_ly'])
        ytd_fc_pct, ytd_fc_clr = pct_or_na(rr['ytd'], rr['fc_ytd'])
        _rows_html += f"""<tr>
  <td {_td_name}>{rr['name']}</td>
  <td {_td}>{fmtK(rr['wk'])}</td><td {_td}>{fmtK(rr['wk_ly'])}</td><td {_td}>{fmtK(rr['fc_wk'])}</td>
  <td style="{_td_pct}{wk_clr};">{wk_pct}</td>
  <td style="{_td_pct}{wk_fc_clr};border-right:2px solid #e0d6cf;">{wk_fc_pct}</td>
  <td {_td}>{fmtK(rr['ytd'])}</td><td {_td}>{fmtK(rr['ytd_ly'])}</td><td {_td}>{fmtK(rr['fc_ytd'])}</td>
  <td style="{_td_pct}{ytd_clr};">{ytd_pct}</td>
  <td style="{_td_pct}{ytd_fc_clr};">{ytd_fc_pct}</td>
</tr>"""

    # Total row
    _all_fc_wk = _all_ret.get('fc_d_wk', 0)
    _tot_wk_pct, _tot_wk_clr = pct_or_na(week_total, week_ly)
    _tot_wk_fc_pct, _tot_wk_fc_clr = pct_or_na(week_total, _all_fc_wk)
    _tot_ytd_pct, _tot_ytd_clr = pct_or_na(ytd_total, ytd_ly)
    _tot_ytd_fc_pct, _tot_ytd_fc_clr = pct_or_na(ytd_total, ytd_fc)
    _tdt = 'style="padding:8px 10px;border-top:2px solid #333;text-align:right;font-size:13px;"'
    _tdt_pct = 'padding:8px 10px;border-top:2px solid #333;text-align:right;font-size:13px;color:'

    _total_row = f"""<tr style="background:#f8f9fa;font-weight:700;">
  <td style="padding:8px 10px;border-top:2px solid #333;font-size:13px;font-weight:700;">TOTAL</td>
  <td {_tdt}>{fmtK(week_total)}</td><td {_tdt}>{fmtK(week_ly)}</td><td {_tdt}>{fmtK(_all_fc_wk)}</td>
  <td style="{_tdt_pct}{_tot_wk_clr};">{_tot_wk_pct}</td>
  <td style="{_tdt_pct}{_tot_wk_fc_clr};border-right:2px solid #e0d6cf;">{_tot_wk_fc_pct}</td>
  <td {_tdt}>{fmtK(ytd_total)}</td><td {_tdt}>{fmtK(ytd_ly)}</td><td {_tdt}>{fmtK(ytd_fc)}</td>
  <td style="{_tdt_pct}{_tot_ytd_clr};">{_tot_ytd_pct}</td>
  <td style="{_tdt_pct}{_tot_ytd_fc_clr};">{_tot_ytd_fc_pct}</td>
</tr>"""

    # Key takeaways
    _takeaways = []
    _takeaways.append(f"Total sell-through was {fmtK(week_total)} last week, {'up' if week_vs_ly >= 0 else 'down'} {abs(week_vs_ly):.1f}% vs LY ({fmtK(week_ly)})")
    _fc_diff = ytd_total - ytd_fc
    _takeaways.append(f"YTD is {fmtK(abs(_fc_diff))} {'ahead of' if _fc_diff >= 0 else 'behind'} forecast ({ytd_vs_fc:+.1f}%)")
    _takeaways.append(f"Top sellers last week: {', '.join(_top3_lines)}")
    _takeaway_html = "\n".join(f'    <li style="margin:6px 0;line-height:1.6;">{t}</li>' for t in _takeaways)

    # Headers
    _hdr1 = 'style="padding:8px 10px;text-align:center;font-weight:700;color:#1A1A1A;font-size:13px;border-bottom:1px solid #d5cdc6;"'
    _hdr2 = 'style="padding:6px 10px;text-align:right;font-weight:600;color:#888;font-size:11px;text-transform:uppercase;"'

    green = '#5CB85C'
    red = '#D9534F'

    _email_html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;padding:0;font-family:Helvetica,Arial,sans-serif;background:#f5f5f5;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f5f5f5;padding:24px 0;">
<tr><td align="center">
<table role="presentation" width="960" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.08);">

<!-- Header -->
<tr><td style="padding:28px 36px 20px;border-bottom:1px solid #eee;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
  <tr>
    <td style="vertical-align:top;">
      <h1 style="margin:0;color:#1A1A1A;font-size:26px;font-weight:500;letter-spacing:6px;">{client_name}</h1>
      <p style="margin:6px 0 0;color:#666;font-size:14px;">Performance Report &mdash; Week {current_week}, {current_month} FY{current_year}</p>
    </td>
    <td style="text-align:right;vertical-align:top;">
      <span style="display:inline-block;background:{primary_color};color:#fff;font-size:12px;padding:5px 14px;border-radius:4px;font-weight:600;">Data through {current_week_end}</span>
      <br><span style="font-size:12px;color:#999;margin-top:6px;display:inline-block;">Built by Farsight</span>
    </td>
  </tr>
  </table>
</td></tr>

<!-- KPI Tiles -->
<tr><td style="padding:20px 36px 8px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
  <tr>
    <td style="width:24%;text-align:center;padding:16px 12px;border:1px solid #eee;border-radius:8px;">
      <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:1px;font-weight:600;">Week {current_week} Sales</div>
      <div style="font-size:28px;font-weight:700;color:#1A1A1A;margin:6px 0 2px;">{fmtK(week_total)}</div>
    </td>
    <td style="width:2%;"></td>
    <td style="width:24%;text-align:center;padding:16px 12px;border:1px solid #eee;border-radius:8px;">
      <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:1px;font-weight:600;">YTD Sales</div>
      <div style="font-size:28px;font-weight:700;color:#1A1A1A;margin:6px 0 2px;">{fmtK(ytd_total)}</div>
    </td>
    <td style="width:2%;"></td>
    <td style="width:24%;text-align:center;padding:16px 12px;border:1px solid #eee;border-radius:8px;">
      <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:1px;font-weight:600;">YTD vs LY</div>
      <div style="font-size:28px;font-weight:700;margin:6px 0 2px;color:{green if ytd_vs_ly >= 0 else red};">{ytd_vs_ly:+.1f}%</div>
    </td>
    <td style="width:2%;"></td>
    <td style="width:24%;text-align:center;padding:16px 12px;border:1px solid #eee;border-radius:8px;">
      <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:1px;font-weight:600;">YTD vs Forecast</div>
      <div style="font-size:28px;font-weight:700;margin:6px 0 2px;color:{green if ytd_vs_fc >= 0 else red};">{ytd_vs_fc:+.1f}%</div>
    </td>
  </tr>
  </table>
</td></tr>

<!-- Key Takeaways -->
<tr><td style="padding:20px 36px 8px;">
  <h2 style="margin:0 0 10px;font-size:16px;font-weight:700;color:#1A1A1A;">Key Takeaways</h2>
  <ul style="margin:0;padding-left:20px;font-size:14px;color:#333;line-height:1.6;">
{_takeaway_html}
  </ul>
</td></tr>

<!-- Sell Through by Customer -->
<tr><td style="padding:20px 36px 4px;">
  <h2 style="margin:0 0 12px;font-size:16px;font-weight:700;color:#1A1A1A;">Sell Through by Customer</h2>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="font-size:12px;">
  <tr style="background:#f5f0ec;">
    <th style="padding:8px 10px;text-align:left;font-size:11px;color:#888;text-transform:uppercase;font-weight:600;" rowspan="2">Customer</th>
    <th {_hdr1} colspan="5" style="padding:8px 10px;text-align:center;font-weight:700;color:#1A1A1A;font-size:11px;border-bottom:1px solid #d5cdc6;border-right:2px solid #e0d6cf;">Week</th>
    <th {_hdr1} colspan="5">YTD</th>
  </tr>
  <tr style="background:#f5f0ec;">
    <th {_hdr2}>TY</th><th {_hdr2}>LY</th><th {_hdr2}>Fcst</th><th {_hdr2}>vs LY%</th>
    <th style="padding:6px 10px;text-align:right;font-weight:600;color:#888;font-size:11px;text-transform:uppercase;border-right:2px solid #e0d6cf;">vs Fcst%</th>
    <th {_hdr2}>TY</th><th {_hdr2}>LY</th><th {_hdr2}>Fcst</th><th {_hdr2}>vs LY%</th><th {_hdr2}>vs Fcst%</th>
  </tr>
  {_rows_html}
  {_total_row}
  </table>
</td></tr>

<!-- CTA -->
{"" if not dashboard_url else f'''<tr><td style="padding:28px 36px;text-align:center;">
  <a href="{dashboard_url}" style="display:inline-block;background:{primary_color};color:#ffffff;text-decoration:none;padding:14px 32px;border-radius:8px;font-size:14px;font-weight:600;letter-spacing:0.5px;">View Full Dashboard &rarr;</a>
</td></tr>'''}

<!-- Footer -->
<tr><td style="padding:16px 36px;background:#f5f5f5;border-top:1px solid #eee;">
  <p style="margin:0 0 6px;font-size:11px;color:#999;text-align:center;font-style:italic;">Please verify key figures before sharing or making decisions.</p>
  <p style="margin:0;font-size:10px;color:#bbb;text-align:center;">Auto-generated &bull; {now.strftime('%Y-%m-%d %H:%M')}</p>
</td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""

    email_path = os.path.join(output_dir, 'email_snapshot.html')
    with open(email_path, 'w', encoding='utf-8') as f:
        f.write(_email_html)
    print(f"  Email snapshot: {email_path}")
