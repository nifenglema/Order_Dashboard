from fastapi import FastAPI, Query, Form, Request, Response, status, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse
import pyodbc
import pandas as pd
from typing import Optional, List, Dict, Any
from datetime import datetime
import os
import sys
import re
from apscheduler.schedulers.background import BackgroundScheduler

app = FastAPI(title="Order Overview API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- BASE DIRECTORY RESOLVER ---
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# --- USER AUTHENTICATION CREDENTIALS ---
AUTH_USERS = {
    "maxim": "maxim123"
}
SESSION_TOKEN = "erp_auth_active_token"

def is_authenticated(request: Request) -> bool:
    return request.cookies.get("erp_session") == SESSION_TOKEN

# --- DYNAMIC SQL SERVER CONNECTION ---
SERVER = "ERP-server"
DATABASE = "dye_bm"
USERNAME = "ERP2025"
PASSWORD = "ERP2025"

def get_db_connection():
    available_drivers = pyodbc.drivers()
    preferred_driver = None
    driver_candidates = [
        "ODBC Driver 18 for SQL Server",
        "ODBC Driver 17 for SQL Server",
        "SQL Server Native Client 11.0",
        "ODBC Driver 13 for SQL Server",
        "ODBC Driver 11 for SQL Server",
        "SQL Server"
    ]
    for candidate in driver_candidates:
        if candidate in available_drivers:
            preferred_driver = candidate
            break

    if not preferred_driver:
        raise RuntimeError(
            f"No suitable SQL Server ODBC Driver found. Installed drivers: {available_drivers}. "
            "Please install 'ODBC Driver 17 for SQL Server' on this machine."
        )

    conn_str = (
        f"DRIVER={{{preferred_driver}}};"
        f"SERVER={SERVER};"
        f"DATABASE={DATABASE};"
        f"UID={USERNAME};"
        f"PWD={PASSWORD};"
        f"TrustServerCertificate=yes;"
    )
    return pyodbc.connect(conn_str)

PROCESS_MAP = {
    1: "Greige Fabric",
    2: "Over Turn",
    3: "Dyeing",
    4: "Hydroextraction",
    6: "Drying 1",
    7: "Dyeing",
    8: "Sueding",
    9: "Brushing",
    10: "Printing",
    12: "QA Packing",
    13: "Setting",
    14: "Pre-Set",
    15: "Sewing",
    16: "Slitting",
    17: "Fabric Wash",
    18: "Compact",
    19: "Shearing",
    20: "Singeing",
    23: "Drying 2",
    24: "Slitting 1",
    25: "SlitSprayWater",
    26: "Dyeing 1"
}

def format_lead_time(minutes: Optional[float]) -> str:
    if pd.isna(minutes) or minutes is None or minutes < 0:
        return ""
    total_mins = int(minutes)
    days = total_mins // 1440
    hours = (total_mins % 1440) // 60
    mins = total_mins % 60
    if days > 0:
        return f"{days:02d}d {hours:02d}h {mins:02d}m"
    return f"{hours:02d}h {mins:02d}m"

def format_dt(val) -> str:
    if pd.isna(val) or val is None:
        return ""
    if isinstance(val, (datetime, pd.Timestamp)):
        return val.strftime("%d/%m/%Y %H:%M")
    s = str(val).strip()
    if s.lower() in ["nat", "none", "nan", ""]:
        return ""
    try:
        dt = pd.to_datetime(s)
        return dt.strftime("%d/%m/%Y %H:%M")
    except Exception:
        if len(s) >= 16:
            return s[:16]
        return s

def format_date(val) -> str:
    if pd.isna(val) or val is None:
        return ""
    if isinstance(val, (datetime, pd.Timestamp)):
        return val.strftime("%d/%m/%Y")
    s = str(val).strip()
    if s.lower() in ["nat", "none", "nan", ""]:
        return ""
    try:
        dt = pd.to_datetime(s)
        return dt.strftime("%d/%m/%Y")
    except Exception:
        return s[:10]

def get_base_cist(cist_no):
    m = re.match(r'^([0-9]{2}[A-Z][0-9]{4})', str(cist_no).strip().upper())
    if m:
        return m.group(1)
    return str(cist_no).strip().upper()

def background_sync():
    print("Background check running on server...")

scheduler = BackgroundScheduler()
scheduler.add_job(background_sync, "interval", minutes=10)
scheduler.start()

# --- AUTHENTICATION ROUTES ---

@app.get("/login")
@app.get("/login.html")
def login_page():
    return FileResponse(os.path.join(BASE_DIR, "login.html"))

@app.post("/api/login")
def login_api(username: str = Form(...), password: str = Form(...)):
    if username in AUTH_USERS and AUTH_USERS[username] == password:
        response = JSONResponse(content={"success": True, "message": "Authenticated"})
        response.set_cookie(key="erp_session", value=SESSION_TOKEN, httponly=True, max_age=86400)
        return response
    return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"success": False, "detail": "Incorrect username or password"})

@app.get("/api/logout")
def logout_api():
    response = RedirectResponse(url="/login")
    response.delete_cookie("erp_session")
    return response

# --- DASHBOARD PAGE ROUTES ---

@app.get("/")
@app.get("/index.html")
def serve_home(request: Request):
    if not is_authenticated(request):
        return RedirectResponse(url="/login")
    return FileResponse(os.path.join(BASE_DIR, "index.html"))

@app.get("/summary")
@app.get("/summary.html")
def serve_summary(request: Request):
    if not is_authenticated(request):
        return RedirectResponse(url="/login")
    return FileResponse(os.path.join(BASE_DIR, "summary.html"))

# --- DATA API ROUTE ---

@app.get("/api/orders")
def get_orders(
    request: Request,
    start_date: Optional[str] = Query("2026-06-01"),
    end_date: Optional[str] = Query("2026-10-30"),
    date_type: Optional[str] = Query("od_date"),
    po_type: Optional[str] = Query(None),
    search: Optional[str] = Query(None)
):
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        conn = get_db_connection()
        date_col = "dev_date" if str(date_type).strip() == "dev_date" else "od_date"

        if search and search.strip():
            s_clean = search.strip().replace("'", "''")
            sql_od = f"""
                SELECT DISTINCT o.od_no, o.od_date, o.dev_date, o.od_type, o.cust_odno, o.cust_id 
                FROM od o
                LEFT JOIN pcist p ON o.od_no = p.od_no
                WHERE o.od_no LIKE '%{s_clean}%' OR p.cist_no LIKE '%{s_clean}%'
            """
        else:
            sql_od = f"""
                SELECT od_no, od_date, dev_date, od_type, cust_odno, cust_id 
                FROM od 
                WHERE CONVERT(DATE, od_date) <= '{end_date}' 
                  AND (CONVERT(DATE, dev_date) >= '{start_date}' OR dev_date IS NULL)
            """
        df_od = pd.read_sql(sql_od, conn)

        if df_od.empty:
            conn.close()
            return []

        od_list = tuple(df_od["od_no"].dropna().unique())
        in_clause = f"({repr(od_list[0])})" if len(od_list) == 1 else str(od_list)

        df_odb = pd.DataFrame()
        try:
            sql_odb = f"SELECT od_no, prod_name FROM od_b WHERE od_no IN {in_clause}"
            df_odb = pd.read_sql(sql_odb, conn)
        except Exception as e:
            print("Notice: od_b query error:", str(e))

        sql_pcist = f"""
            SELECT od_no, cist_no, ci_date, note, sp_qty 
            FROM pcist 
            WHERE od_no IN {in_clause}
        """
        df_pcist = pd.read_sql(sql_pcist, conn)

        if df_pcist.empty:
            conn.close()
            return []

        jc_list = tuple(df_pcist["cist_no"].dropna().unique())
        jc_in_clause = f"({repr(jc_list[0])})" if len(jc_list) == 1 else str(jc_list)

        sql_eng = f"""
            SELECT cist_no, eng_seq, eng_id, mach_id, pr_date, ok_date, ok_qty 
            FROM pcist_eng 
            WHERE cist_no IN {jc_in_clause}
        """
        df_eng = pd.read_sql(sql_eng, conn)
        conn.close()

    except Exception as e:
        print("DATABASE ERROR:", str(e))
        return {"error": f"Database read failure: {str(e)}"}

    print_orders = set()
    if not df_odb.empty and "prod_name" in df_odb.columns:
        print_mask = df_odb["prod_name"].astype(str).str.upper().str.contains("PRINT", na=False)
        print_orders = set(df_odb.loc[print_mask, "od_no"].dropna().unique())

    df_eng["eng_id_clean"] = pd.to_numeric(df_eng["eng_id"].astype(str).str.strip(), errors="coerce")
    df_eng["Process_Name"] = df_eng["eng_id_clean"].map(PROCESS_MAP).fillna("Other Process")
    df_eng["pr_date"] = pd.to_datetime(df_eng["pr_date"], errors="coerce")
    df_eng["ok_date"] = pd.to_datetime(df_eng["ok_date"], errors="coerce")
    df_eng["ok_qty"] = pd.to_numeric(df_eng["ok_qty"], errors="coerce")
    df_eng["mach_id"] = df_eng["mach_id"].fillna("").astype(str).str.strip().replace("nan", "")
    df_pcist["sp_qty"] = pd.to_numeric(df_pcist["sp_qty"], errors="coerce")

    milestone_names = ["Greige Fabric", "Dyeing", "Setting", "Printing", "QA Packing"]
    
    # Map each cist_no to its base CIST group for rework/suffix consolidation
    df_pcist["base_cist"] = df_pcist["cist_no"].apply(get_base_cist)
    df_eng["base_cist"] = df_eng["cist_no"].apply(get_base_cist)

    base_cist_timings = {}
    cist_quantities = {}

    for _, row in df_eng.iterrows():
        base_cist = str(row["base_cist"]).strip()
        pname = str(row["Process_Name"]).strip()
        
        cist = str(row["cist_no"]).strip()
        if pd.notna(row["ok_qty"]) and row["ok_qty"] > 0:
            cist_quantities[cist] = round(float(row["ok_qty"], 2) if hasattr(float, 'round') else float(row["ok_qty"]), 2)

        if base_cist not in base_cist_timings:
            base_cist_timings[base_cist] = {}

        if pname in milestone_names:
            if f"{pname}_start" not in base_cist_timings[base_cist] or pd.isna(base_cist_timings[base_cist][f"{pname}_start"]):
                base_cist_timings[base_cist][f"{pname}_start"] = row["pr_date"]
            else:
                if pd.notna(row["pr_date"]):
                    base_cist_timings[base_cist][f"{pname}_start"] = min(base_cist_timings[base_cist][f"{pname}_start"], row["pr_date"])

            if f"{pname}_end" not in base_cist_timings[base_cist] or pd.isna(base_cist_timings[base_cist][f"{pname}_end"]):
                base_cist_timings[base_cist][f"{pname}_end"] = row["ok_date"]
            else:
                if pd.notna(row["ok_date"]):
                    base_cist_timings[base_cist][f"{pname}_end"] = max(base_cist_timings[base_cist][f"{pname}_end"], row["ok_date"])

    last_proc = df_eng.dropna(subset=["pr_date"]).sort_values(["cist_no", "pr_date"]).groupby("cist_no").tail(1).reset_index(drop=True)
    last_proc = last_proc.rename(columns={
        "Process_Name": "Last_Process", 
        "mach_id": "Current_Machine_ID",
        "pr_date": "Last_Process_Time",
        "ok_date": "Last_Process_End_Time"
    })

    def classify_po_type(row):
        od_no = str(row["od_no"]).strip().upper()
        if od_no.startswith("MXS"):
            return "Sample"
        if od_no.startswith("MXD"):
            return "Bulk Order"
        if od_no.startswith("YFT"):
            return "Fabric Testing"
        try:
            if int(row["od_type"]) == 4:
                return "Sample"
        except (ValueError, TypeError):
            pass
        return "Bulk Order"

    df_od["po_type_desc"] = df_od.apply(classify_po_type, axis=1)
    df_od["order_type_spec"] = df_od["od_no"].apply(lambda x: "Print" if x in print_orders else "Solid")

    df = pd.merge(df_pcist, df_od, on="od_no", how="left")
    
    df["od_date_raw"] = df["od_date"]
    df["dev_date_raw"] = df["dev_date"]
    df["ci_date_raw"] = df["ci_date"]

    df["od_date"] = pd.to_datetime(df["od_date"], errors="coerce")
    df["dev_date"] = pd.to_datetime(df["dev_date"], errors="coerce")
    df["ci_date"] = pd.to_datetime(df["ci_date"], errors="coerce")

    df = pd.merge(df, last_proc[["cist_no", "Last_Process", "Current_Machine_ID", "Last_Process_Time", "Last_Process_End_Time"]], on="cist_no", how="left")

    if po_type and po_type != "All":
        df = df[df["po_type_desc"] == po_type]
    if search:
        s = search.strip().lower()
        df = df[
            df["od_no"].astype(str).str.lower().str.contains(s, na=False) |
            df["cist_no"].astype(str).str.lower().str.contains(s, na=False)
        ]

    sort_date_col = "dev_date" if date_col == "dev_date" else "od_date"
    df = df.sort_values(by=[sort_date_col, "od_no", "cist_no"], ascending=[False, True, True])

    result = []
    for po_no, po_group in df.groupby("od_no", sort=False):
        first_row = po_group.iloc[0]
        po_od_date = format_date(first_row["od_date"]) or str(first_row.get("od_date_raw", ""))[:10]
        po_dev_date = format_date(first_row["dev_date"]) or str(first_row.get("dev_date_raw", ""))[:10]
        po_ci_date = format_dt(first_row["ci_date"]) or str(first_row.get("ci_date_raw", ""))[:16]

        job_cards = []
        total_plan_qty = 0.0
        total_actual_qty = 0.0

        po_greige_starts = []
        po_greige_ends = []
        po_dyeing_starts = []
        po_dyeing_ends = []
        po_setting_starts = []
        po_setting_ends = []
        po_qa_starts = []
        po_qa_ends = []
        po_has_any_note = False

        for _, r in po_group.iterrows():
            has_note = pd.notna(r["note"]) and str(r["note"]).strip() != ""
            if has_note:
                po_has_any_note = True

            jc_od_date = format_date(r["od_date"]) or str(r.get("od_date_raw", ""))[:10]
            jc_dev_date = format_date(r["dev_date"]) or str(r.get("dev_date_raw", ""))[:10]
            jc_ci_date = format_dt(r["ci_date"]) or str(r.get("ci_date_raw", ""))[:16]

            lp = str(r["Last_Process"]).strip() if pd.notna(r["Last_Process"]) else ""
            mach = str(r["Current_Machine_ID"]).strip() if pd.notna(r["Current_Machine_ID"]) else ""
            is_proc_done = pd.notna(r.get("Last_Process_End_Time"))

            if lp == "QA Packing":
                status_flag = "Completed"
            elif lp != "":
                status_flag = "In Progress"
            else:
                status_flag = "Pending"

            base_cist_key = str(r["base_cist"]).strip()
            times = base_cist_timings.get(base_cist_key, {})
            
            jc_plan_qty = round(float(r["sp_qty"]), 2) if pd.notna(r.get("sp_qty")) and r["sp_qty"] > 0 else None
            if jc_plan_qty is not None:
                total_plan_qty += jc_plan_qty

            cist_key = str(r["cist_no"]).strip()
            jc_actual_qty = cist_quantities.get(cist_key, None)
            if jc_actual_qty is not None:
                total_actual_qty += jc_actual_qty

            t_greige_start = times.get("Greige Fabric_start", pd.NaT)
            t_dyeing_start = times.get("Dyeing_start", pd.NaT)
            t_setting_start = times.get("Setting_start", pd.NaT)
            t_qa_start = times.get("QA Packing_start", pd.NaT)

            t_greige_end = times.get("Greige Fabric_end", pd.NaT)
            t_dyeing_end = times.get("Dyeing_end", pd.NaT)
            t_setting_end = times.get("Setting_end", pd.NaT)
            t_qa_end = times.get("QA Packing_end", pd.NaT)

            if pd.notna(t_greige_start): po_greige_starts.append(t_greige_start)
            if pd.notna(t_greige_end): po_greige_ends.append(t_greige_end)
            if pd.notna(t_dyeing_start): po_dyeing_starts.append(t_dyeing_start)
            if pd.notna(t_dyeing_end): po_dyeing_ends.append(t_dyeing_end)
            if pd.notna(t_setting_start): po_setting_starts.append(t_setting_start)
            if pd.notna(t_setting_end): po_setting_ends.append(t_setting_end)
            if pd.notna(t_qa_start): po_qa_starts.append(t_qa_start)
            if pd.notna(t_qa_end): po_qa_ends.append(t_qa_end)

            t_printing_start = times.get("Printing_start", pd.NaT)
            t_printing_end = times.get("Printing_end", pd.NaT)

            gd_mins = (t_dyeing_start - t_greige_start).total_seconds() / 60.0 if pd.notna(t_dyeing_start) and pd.notna(t_greige_start) else None
            ds_mins = (t_setting_start - t_dyeing_start).total_seconds() / 60.0 if pd.notna(t_setting_start) and pd.notna(t_dyeing_start) else None
            sp_mins = (t_printing_start - t_setting_start).total_seconds() / 60.0 if pd.notna(t_printing_start) and pd.notna(t_setting_start) else None
            pq_mins_stage = (t_qa_start - t_printing_start).total_seconds() / 60.0 if pd.notna(t_qa_start) and pd.notna(t_printing_start) else None
            sq_mins = (t_qa_start - t_setting_start).total_seconds() / 60.0 if pd.notna(t_qa_start) and pd.notna(t_setting_start) else None
            gq_mins = (t_qa_start - t_greige_start).total_seconds() / 60.0 if pd.notna(t_qa_start) and pd.notna(t_greige_start) else None
            jq_mins = (t_qa_start - r["ci_date"]).total_seconds() / 60.0 if pd.notna(t_qa_start) and pd.notna(r["ci_date"]) else None
            pq_mins = (t_qa_start - r["od_date"]).total_seconds() / 60.0 if pd.notna(t_qa_start) and pd.notna(r["od_date"]) else None

            job_cards.append({
                "cist_no": str(r["cist_no"]),
                "sp_qty": jc_plan_qty,
                "ok_qty": jc_actual_qty,
                "qty": jc_actual_qty or jc_plan_qty,
                "od_date": jc_od_date,
                "dev_date": jc_dev_date,
                "ci_date": jc_ci_date,
                "has_note": has_note,
                "note": str(r["note"]) if has_note else "",
                "status_flag": status_flag,
                "last_process": lp,
                "mach_id": mach,
                "last_process_completed": is_proc_done,
                "last_process_time": format_dt(r["Last_Process_Time"]),
                "greige_start": format_dt(t_greige_start),
                "greige_end": format_dt(t_greige_end),
                "greige_dyeing_lt": format_lead_time(gd_mins),
                "dyeing_start": format_dt(t_dyeing_start),
                "dyeing_end": format_dt(t_dyeing_end),
                "dyeing_setting_lt": format_lead_time(ds_mins),
                "setting_start": format_dt(t_setting_start),
                "setting_end": format_dt(t_setting_end),
                "setting_printing_lt": format_lead_time(sp_mins),
                "printing_start": format_dt(t_printing_start),
                "printing_end": format_dt(t_printing_end),
                "printing_qa_lt": format_lead_time(pq_mins_stage),
                "setting_qa_lt": format_lead_time(sq_mins),
                "qa_start": format_dt(t_qa_start),
                "qa_end": format_dt(t_qa_end),
                "greige_qa_lt": format_lead_time(gq_mins),
                "jobcard_qa_lt": format_lead_time(jq_mins),
                "po_qa_lt": format_lead_time(pq_mins),
            })

        lp_po = str(first_row["Last_Process"]).strip() if pd.notna(first_row["Last_Process"]) else ""
        po_mach = str(first_row["Current_Machine_ID"]).strip() if pd.notna(first_row["Current_Machine_ID"]) else ""
        if lp_po == "QA Packing":
            po_status = "Completed"
        elif lp_po != "":
            po_status = "In Progress"
        else:
            po_status = "Pending"

        po_g_start = min(po_greige_starts) if po_greige_starts else pd.NaT
        po_g_end = max(po_greige_ends) if po_greige_ends else pd.NaT
        po_d_start = min(po_dyeing_starts) if po_dyeing_starts else pd.NaT
        po_d_end = max(po_dyeing_ends) if po_dyeing_ends else pd.NaT
        po_s_start = min(po_setting_starts) if po_setting_starts else pd.NaT
        po_s_end = max(po_setting_ends) if po_setting_ends else pd.NaT
        po_q_start = min(po_qa_starts) if po_qa_starts else pd.NaT
        po_q_end = max(po_qa_ends) if po_qa_ends else pd.NaT

        po_gd_mins = (po_d_start - po_g_start).total_seconds() / 60.0 if pd.notna(po_d_start) and pd.notna(po_g_start) else None
        po_ds_mins = (po_s_start - po_d_start).total_seconds() / 60.0 if pd.notna(po_s_start) and pd.notna(po_d_start) else None
        po_sq_mins = (po_q_start - po_s_start).total_seconds() / 60.0 if pd.notna(po_q_start) and pd.notna(po_s_start) else None
        po_gq_mins = (po_q_end - po_g_start).total_seconds() / 60.0 if pd.notna(po_q_end) and pd.notna(po_g_start) else None
        po_jq_mins = (po_q_end - first_row["ci_date"]).total_seconds() / 60.0 if pd.notna(po_q_end) and pd.notna(first_row["ci_date"]) else None
        po_pq_mins = (po_q_end - first_row["od_date"]).total_seconds() / 60.0 if pd.notna(po_q_end) and pd.notna(first_row["od_date"]) else None

        result.append({
            "po_no": str(po_no),
            "cust_id": str(first_row["cust_id"]) if pd.notna(first_row["cust_id"]) else "",
            "order_type_spec": str(first_row.get("order_type_spec", "Solid")),
            "total_plan_qty": round(total_plan_qty, 2) if total_plan_qty > 0 else None,
            "total_actual_qty": round(total_actual_qty, 2) if total_actual_qty > 0 else None,
            "total_qty": round(total_actual_qty or total_plan_qty, 2) if (total_actual_qty > 0 or total_plan_qty > 0) else None,
            "od_date": po_od_date,
            "dev_date": po_dev_date,
            "ci_date": po_ci_date,
            "status_flag": po_status,
            "last_process": lp_po,
            "mach_id": po_mach,
            "last_process_time": format_dt(first_row["Last_Process_Time"]),
            "greige_start": format_dt(po_g_start),
            "greige_end": format_dt(po_g_end),
            "greige_dyeing_lt": format_lead_time(po_gd_mins),
            "dyeing_start": format_dt(po_d_start),
            "dyeing_end": format_dt(po_d_end),
            "dyeing_setting_lt": format_lead_time(po_ds_mins),
            "setting_start": format_dt(po_s_start),
            "setting_end": format_dt(po_s_end),
            "setting_qa_lt": format_lead_time(po_sq_mins),
            "qa_start": format_dt(po_q_start),
            "qa_end": format_dt(po_q_end),
            "greige_qa_lt": format_lead_time(po_gq_mins),
            "jobcard_qa_lt": format_lead_time(po_jq_mins),
            "po_qa_lt": format_lead_time(po_pq_mins),
            "has_note": po_has_any_note,
            "job_cards": job_cards
        })

    return result

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)