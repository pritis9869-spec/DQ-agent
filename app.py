import json,os
import streamlit as st
from databricks import sql
# =========================
# CONFIG
# =========================
DB = "workspace.dq_agent_poc"
ALERTS_TBL = f"{DB}.dq_alerts"
RUN_INSIGHTS_TBL = f"{DB}.dq_agent_run_insights"
ISSUE_INSIGHTS_TBL = f"{DB}.dq_agent_issue_insights"
FEEDBACK_TBL = f"{DB}.dq_agent_feedback"

def _get_required(name: str) -> str:
   """
   Reads a required config from environment variables.
   Falls back to st.secrets ONLY for local/dev usage.
   In Databricks Apps (Option 2), env vars should be used.
   """
   v = os.getenv(name)
   if v:
       return v.strip()
   # optional local fallback
   try:
       v2 = st.secrets.get(name.lower()) or st.secrets.get(name)
       if v2:
           return str(v2).strip()
   except Exception:
       pass
   st.error(
       f"Missing required config: {name}. "
       f"Add it in Databricks App Secrets/Resources as an environment variable."
   )
   st.stop()
# =========================
# CONFIG
# =========================
SERVER_HOSTNAME      = _get_required("SERVER_HOSTNAME")        # e.g. dbc-xxxx.cloud.databricks.com
WAREHOUSE_HTTP_PATH  = _get_required("WAREHOUSE_HTTP_PATH")    # SQL warehouse http path
ACCESS_TOKEN         = _get_required("ACCESS_TOKEN")           # PAT for demo (later replace with OAuth)

# =========================
# DB HELPERS
# =========================
def db_query(query: str, params=None):
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
# Support deep-link: ?alert_id=<id>
alert_id = st.query_params.get("alert_id")
# Load recent alerts
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
# Pick selected alert
selected = None
if alert_id and alert_id in alert_ids:
   selected = next(a for a in alerts if a["alert_id"] == alert_id)
else:
   st.sidebar.header("Recent Alerts")
   pick = st.sidebar.selectbox("Pick an alert", options=alert_ids)
   selected = next(a for a in alerts if a["alert_id"] == pick)
# Header
st.subheader(f"{selected['alert_type']} ({selected['alert_severity']})")
st.caption(f"Alert ID: {selected['alert_id']}  |  Table: {selected['table_name']}  |  Run: {selected['run_ts']}  |  Created: {selected['created_ts']}")
st.write(selected["message"])
# Details
st.markdown("### Alert Details")
details = {}
try:
   details = json.loads(selected.get("details_json") or "{}")
except Exception:
   details = {"raw": selected.get("details_json")}
st.json(details)
# Run summary
st.markdown("### AI Run Summary")
c2, r2 = db_query(f"""
SELECT summary, key_issues_json
FROM {RUN_INSIGHTS_TBL}
WHERE table_name = ? AND run_ts = ?
ORDER BY created_ts DESC
LIMIT 1
""", [selected["table_name"], selected["run_ts"]])
if r2:
   st.write(r2[0][0])
   try:
       st.json(json.loads(r2[0][1] or "[]"))
   except Exception:
       st.text(r2[0][1])
else:
   st.info("No run insights found yet for this run. Run the monitoring agent first.")
# Issue insights
st.markdown("### Issue Insights (from Agent)")
c3, r3 = db_query(f"""
SELECT issue_type, `column`, failed_rows, total_rows, failure_rate, trend,
      severity, meaning, likely_cause, recommended_action, confidence
FROM {ISSUE_INSIGHTS_TBL}
WHERE table_name = ? AND run_ts = ?
ORDER BY severity DESC, failure_rate DESC
""", [selected["table_name"], selected["run_ts"]])
issues = [dict(zip(c3, r)) for r in r3]
st.dataframe(issues, use_container_width=True)
# Feedback form
st.markdown("### Give Feedback (Business Review)")
decision = st.radio(
   "Decision",
   ["ACKNOWLEDGE", "APPROVE_RCA", "REJECT_RCA", "EDIT_ACTION", "FALSE_POSITIVE", "ESCALATE"],
   horizontal=True
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
       submitted_by
   ])
   st.success("Feedback submitted  (saved to dq_agent_feedback)")
