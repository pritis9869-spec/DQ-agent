import json
import os
import streamlit as st
from databricks import sql

# =========================
# CONFIG: Tables
# =========================
DB = "workspace.dq_agent_poc"
ALERTS_TBL = f"{DB}.dq_alerts"
RUN_INSIGHTS_TBL = f"{DB}.dq_agent_run_insights"
ISSUE_INSIGHTS_TBL = f"{DB}.dq_agent_issue_insights"
FEEDBACK_TBL = f"{DB}.dq_agent_feedback"
# =========================
# SECRET SCOPE NAME
# =========================
APP_SECRET_SCOPE = os.getenv("APP_SECRET_SCOPE", "dq_app_scope")
# =========================
# Databricks dbutils access (works in Databricks Apps)
# We try multiple imports because environments differ slightly.
# =========================
_DBUTILS = None
def _load_dbutils():
    global _DBUTILS
    if _DBUTILS is not None:
        return _DBUTILS
    # Option 1 (most common in Databricks Apps runtime)
    try:
        from databricks.sdk.runtime import dbutils  # type: ignore
        _DBUTILS = dbutils
        return _DBUTILS
    except Exception:
        pass
    # Option 2 (sometimes available)
    try:
        from pyspark.dbutils import DBUtils  # noqa: F401
        # If this import works, dbutils might already be injected
        if "dbutils" in globals():
            _DBUTILS = globals()["dbutils"]
            return _DBUTILS
    except Exception:
        pass
    _DBUTILS = None
    return None
def _get_required(name: str) -> str:
    """
    Read required config in priority order:
    1) Databricks Secret Scope (preferred for Databricks Apps)
    2) Environment variable fallback
    3) Streamlit secrets fallback (local/dev only)
    """
    key_lower = name.lower()
    key_upper = name
    # 1) Secret scope (Databricks Apps preferred)
    dbutils_obj = _load_dbutils()
    if dbutils_obj is not None:
        # Try lowercase key
        try:
            v = dbutils_obj.secrets.get(APP_SECRET_SCOPE, key_lower)
            if v:
                return str(v).strip()
        except Exception:
            pass
        # Try uppercase key if user stored it that way
        try:
            v = dbutils_obj.secrets.get(APP_SECRET_SCOPE, key_upper)
            if v:
                return str(v).strip()
        except Exception:
            pass
    # 2) Env var fallback
    v = os.getenv(name)
    if v:
        return v.strip()
    # 3) Streamlit secrets fallback (local/dev)
    try:
        v2 = st.secrets.get(key_lower) or st.secrets.get(key_upper)
        if v2:
            return str(v2).strip()
    except Exception:
        pass
    st.error(
        f"Missing required config: {name}\n\n"
        f"Expected in secret scope '{APP_SECRET_SCOPE}' as key '{key_lower}'.\n"
        f"Make sure you attached that secret scope in App → Add resources."
    )
    st.stop()
# =========================
# Required connection values
# =========================
SERVER_HOSTNAME = _get_required("SERVER_HOSTNAME")          # e.g. dbc-xxxxxxx.cloud.databricks.com
WAREHOUSE_HTTP_PATH = _get_required("WAREHOUSE_HTTP_PATH")  # e.g. /sql/1.0/warehouses/xxxx
ACCESS_TOKEN = _get_required("ACCESS_TOKEN")                # PAT token (demo)
# =========================
# DB Helpers
# =========================
def db_query(query: str, params=None):
    """Run a SELECT query and return (cols, rows)."""
    with sql.connect(
        server_hostname=SERVER_HOSTNAME,
        http_path=WAREHOUSE_HTTP_PATH,
        access_token=ACCESS_TOKEN,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(query, params or [])
            cols = [c[0] for c in cur.description] if cur.description else []
            rows = cur.fetchall()
    return cols, rows
def db_execute(query: str, params=None):
    """Run an INSERT/UPDATE/DELETE query."""
    with sql.connect(
        server_hostname=SERVER_HOSTNAME,
        http_path=WAREHOUSE_HTTP_PATH,
        access_token=ACCESS_TOKEN,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(query, params or [])
# =========================
# UI
# =========================
st.set_page_config(page_title="DQ Review Portal", layout="wide")
st.title("DQ Review Portal")
# Deep-link support: ?alert_id=<id>
alert_id = st.query_params.get("alert_id")
# ---- Load recent alerts
cols, rows = db_query(f"""
SELECT
  alert_id,
  created_ts,
  table_name,
  run_ts,
  alert_type,
  alert_severity,
  message,
  details_json
FROM {ALERTS_TBL}
ORDER BY created_ts DESC
LIMIT 50
""")
alerts = [dict(zip(cols, r)) for r in rows]
if not alerts:
    st.warning("No alerts found yet. Run the agent to populate dq_alerts.")
    st.stop()
alert_ids = [a["alert_id"] for a in alerts]
# ---- Pick selected alert
if alert_id and alert_id in alert_ids:
    selected = next(a for a in alerts if a["alert_id"] == alert_id)
else:
    st.sidebar.header("Recent Alerts")
    pick = st.sidebar.selectbox("Pick an alert", options=alert_ids)
    selected = next(a for a in alerts if a["alert_id"] == pick)
# ---- Header
st.subheader(f"{selected['alert_type']} ({selected['alert_severity']})")
st.caption(
    f"Alert ID: {selected['alert_id']} | "
    f"Table: {selected['table_name']} | "
    f"Run: {selected['run_ts']} | "
    f"Created: {selected['created_ts']}"
)
st.write(selected["message"])
# ---- Details JSON
st.markdown("### Alert Details")
try:
    details = json.loads(selected.get("details_json") or "{}")
except Exception:
    details = {"raw": selected.get("details_json")}
st.json(details)
# ---- Run summary
st.markdown("### AI Run Summary")
c2, r2 = db_query(f"""
SELECT summary, key_issues_json
FROM {RUN_INSIGHTS_TBL}
WHERE table_name = ? AND run_ts = ?
ORDER BY created_ts DESC
LIMIT 1
""", [selected["table_name"], selected["run_ts"]])
if r2:
    summary = r2[0][0]
    key_issues_json = r2[0][1]
    st.write(summary)
    try:
        st.json(json.loads(key_issues_json or "[]"))
    except Exception:
        st.text(key_issues_json)
else:
    st.info("No run insights found for this run. Run the monitoring agent first.")
# ---- Issue insights table
st.markdown("### Issue Insights (from Agent)")
c3, r3 = db_query(f"""
SELECT
  issue_type,
  `column`,
  failed_rows,
  total_rows,
  failure_rate,
  trend,
  severity,
  meaning,
  likely_cause,
  recommended_action,
  confidence
FROM {ISSUE_INSIGHTS_TBL}
WHERE table_name = ? AND run_ts = ?
ORDER BY severity DESC, failure_rate DESC
""", [selected["table_name"], selected["run_ts"]])
issues = [dict(zip(c3, r)) for r in r3]
st.dataframe(issues, use_container_width=True)
# ---- Feedback form
st.markdown("### Give Feedback (Business Review)")
decision = st.radio(
    "Decision",
    ["ACKNOWLEDGE", "APPROVE_RCA", "REJECT_RCA", "EDIT_ACTION", "FALSE_POSITIVE", "ESCALATE"],
    horizontal=True,
)
comments = st.text_area("Comments (context / correction / reasoning)")
edited_action = st.text_area("Edited action (only if EDIT_ACTION)", value="")
submitted_by = st.text_input("Your name/email (demo)", value="business_user@company.com")
if st.button("Submit Feedback"):
    db_execute(f"""
    INSERT INTO {FEEDBACK_TBL}
      (action_id, table_name, run_ts, decision, edited_action_text, comments, submitted_by, submitted_ts)
    VALUES
      (?, ?, ?, ?, ?, ?, ?, current_timestamp())
    """, [
        selected["alert_id"],
        selected["table_name"],
        selected["run_ts"],
        decision,
        edited_action if decision == "EDIT_ACTION" else None,
        comments,
        submitted_by,
    ])
    st.success("Feedback submitted (saved to dq_agent_feedback). Refresh the page to see updates.")
 
