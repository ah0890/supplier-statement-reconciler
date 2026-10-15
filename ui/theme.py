"""Visual theme: CSS tokens and base styles injected once per page render."""

from __future__ import annotations

import streamlit as st

_CSS = """
<style>
:root {
  --ssr-navy: #1F4E79;
  --ssr-ink: #1B2430;
  --ssr-muted: #5B6675;
  --ssr-border: #E2E7EF;
  --ssr-surface: #FFFFFF;
  --ssr-green: #1E7B4D;   --ssr-green-bg: #E6F4EC;
  --ssr-amber: #9A6200;   --ssr-amber-bg: #FFF4DB;
  --ssr-red: #B42318;     --ssr-red-bg: #FDECEA;
  --ssr-blue: #1F4E79;    --ssr-blue-bg: #E8F0F9;
  --ssr-grey: #4A5565;    --ssr-grey-bg: #EEF1F5;
}
.block-container { padding-top: 2rem; padding-bottom: 3rem; max-width: 1280px; }
h1, h2, h3 { color: var(--ssr-ink); letter-spacing: -0.01em; }
[data-testid="stSidebar"] { background: #10263D; }
[data-testid="stSidebar"] * { color: #E6ECF3 !important; }
[data-testid="stSidebarNav"] a[aria-current="page"] { background: rgba(255,255,255,0.10); }
[data-testid="stSidebar"] [data-testid="stSelectbox"] input,
[data-testid="stSidebar"] [data-testid="stSelectbox"] svg,
[data-testid="stSidebar"] [data-testid="stSelectbox"] path {
  color: var(--ssr-ink) !important; -webkit-text-fill-color: var(--ssr-ink) !important; }

.ssr-header { display:flex; justify-content:space-between; align-items:flex-end;
  border-bottom: 1px solid var(--ssr-border); padding-bottom: .75rem; margin-bottom: 1.25rem; }
.ssr-header h1 { font-size: 1.65rem; margin: 0; padding: 0; }
.ssr-header .ssr-sub { color: var(--ssr-muted); font-size: .92rem; margin-top: .25rem; }
.ssr-context { color: var(--ssr-muted); font-size: .85rem; text-align: right; }

.ssr-card { background: var(--ssr-surface); border: 1px solid var(--ssr-border);
  border-radius: 10px; padding: 1rem 1.1rem; height: 100%; min-height: 128px; }
.ssr-kpi-label { color: var(--ssr-muted); font-size: .78rem; text-transform: uppercase;
  letter-spacing: .04em; font-weight: 600; }
.ssr-kpi-value { color: var(--ssr-ink); font-size: 1.55rem; font-weight: 650;
  margin-top: .2rem; font-variant-numeric: tabular-nums; }
.ssr-kpi-note { color: var(--ssr-muted); font-size: .8rem; margin-top: .15rem; }
.ssr-kpi-accent-green { border-top: 3px solid var(--ssr-green); }
.ssr-kpi-accent-amber { border-top: 3px solid #D99A1E; }
.ssr-kpi-accent-red { border-top: 3px solid var(--ssr-red); }
.ssr-kpi-accent-blue { border-top: 3px solid var(--ssr-navy); }
.ssr-kpi-accent-grey { border-top: 3px solid #9AA5B4; }

.ssr-badge { display:inline-block; padding: .15rem .55rem; border-radius: 999px;
  font-size: .75rem; font-weight: 600; letter-spacing: .02em; white-space: nowrap; }
.ssr-badge-green { color: var(--ssr-green); background: var(--ssr-green-bg); }
.ssr-badge-amber { color: var(--ssr-amber); background: var(--ssr-amber-bg); }
.ssr-badge-red { color: var(--ssr-red); background: var(--ssr-red-bg); }
.ssr-badge-blue { color: var(--ssr-blue); background: var(--ssr-blue-bg); }
.ssr-badge-grey { color: var(--ssr-grey); background: var(--ssr-grey-bg); }

.ssr-empty { border: 1px dashed #C5CEDA; border-radius: 10px; padding: 2rem;
  text-align: center; color: var(--ssr-muted); background: #FBFCFE; }
.ssr-empty h4 { color: var(--ssr-ink); margin-bottom: .35rem; }

.ssr-steps { display:flex; gap:.4rem; flex-wrap: wrap; margin: .25rem 0 1rem 0; }
.ssr-step { font-size: .78rem; padding: .25rem .6rem; border-radius: 6px;
  background: var(--ssr-grey-bg); color: var(--ssr-grey); }
.ssr-step-done { background: var(--ssr-green-bg); color: var(--ssr-green); }
.ssr-step-current { background: var(--ssr-navy); color: #fff; }

.ssr-side { background: var(--ssr-surface); border:1px solid var(--ssr-border);
  border-radius: 10px; padding: 1rem 1.2rem; }
.ssr-side h5 { margin: 0 0 .6rem 0; color: var(--ssr-muted); font-size: .78rem;
  text-transform: uppercase; letter-spacing: .05em; }
.ssr-field { display:flex; justify-content:space-between; padding:.3rem 0;
  border-bottom: 1px solid #F0F2F6; font-size: .9rem; }
.ssr-field span:first-child { color: var(--ssr-muted); }
.ssr-field span:last-child { font-weight: 600; font-variant-numeric: tabular-nums; }
.ssr-diff { color: var(--ssr-red); }
.ssr-demo-banner { background: var(--ssr-amber-bg); color: var(--ssr-amber);
  border-radius: 8px; padding: .45rem .8rem; font-size: .85rem; font-weight: 600;
  margin-bottom: 1rem; }
</style>
"""


def apply_theme() -> None:
    """Inject the application stylesheet."""
    st.markdown(_CSS, unsafe_allow_html=True)
