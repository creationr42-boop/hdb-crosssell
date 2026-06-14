"""
HDB CrossSell — Web Eligibility Checker
Uses Playwright (installs its own Chromium — no system Chrome needed)
"""

import os, uuid, time, threading, datetime, traceback
from flask import Flask, request, jsonify, send_file, render_template

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

jobs = {}  # job_id -> dict


# ═══════════════════════════════════════════════
#  LOGGING HELPERS
# ═══════════════════════════════════════════════

def _log(job_id, msg):
    print(msg)
    jobs[job_id]["logs"].append(str(msg))
    if len(jobs[job_id]["logs"]) > 600:
        jobs[job_id]["logs"] = jobs[job_id]["logs"][-500:]

def _set_status(job_id, s):
    jobs[job_id]["status"] = s


# ═══════════════════════════════════════════════
#  PDF GENERATION
# ═══════════════════════════════════════════════

def generate_pdf(job_id):
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.platypus import (
            SimpleDocTemplate, Table, TableStyle,
            Paragraph, Spacer, HRFlowable
        )

        job  = jobs[job_id]
        rows = job["results"]
        path = f"/tmp/hdb_report_{job_id}.pdf"

        doc = SimpleDocTemplate(
            path, pagesize=A4,
            leftMargin=1.5*cm, rightMargin=1.5*cm,
            topMargin=1.8*cm, bottomMargin=1.8*cm
        )
        story = []

        T  = lambda txt, **kw: Paragraph(txt, ParagraphStyle("x", **kw))
        TS = lambda d, cw, style: (lambda t: (t.setStyle(TableStyle(style)), t)[1])(Table(d, colWidths=cw))

        story.append(T("HDB CrossSell — Eligibility Report",
            fontSize=16, fontName="Helvetica-Bold",
            textColor=colors.HexColor("#0E2A47"), spaceAfter=4))
        story.append(T(
            f"Generated: {datetime.datetime.now().strftime('%d %b %Y, %I:%M %p')}  |  Total: {len(rows)}",
            fontSize=9, textColor=colors.HexColor("#6B7785"), spaceAfter=10))
        story.append(HRFlowable(width="100%", thickness=1,
            color=colors.HexColor("#0E2A47"), spaceAfter=14))

        eligible     = [r for r in rows if r["status"] == "ELIGIBLE"]
        not_eligible = [r for r in rows if r["status"] == "NOT ELIGIBLE"]
        errors       = [r for r in rows if r["status"] not in ("ELIGIBLE", "NOT ELIGIBLE")]

        # Summary box
        t = Table(
            [["✅ Eligible", "❌ Not Eligible", "⚠ Errors / Unknown"],
             [str(len(eligible)), str(len(not_eligible)), str(len(errors))]],
            colWidths=[5.5*cm, 5.5*cm, 5.5*cm]
        )
        t.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#0E2A47")),
            ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
            ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE",   (0,0), (-1,-1), 11),
            ("ALIGN",      (0,0), (-1,-1), "CENTER"),
            ("BACKGROUND", (0,1), (0,1), colors.HexColor("#E5F5EE")),
            ("BACKGROUND", (1,1), (1,1), colors.HexColor("#FBEAEA")),
            ("BACKGROUND", (2,1), (2,1), colors.HexColor("#FCF3E2")),
            ("GRID",       (0,0), (-1,-1), 0.4, colors.HexColor("#DCE3E9")),
            ("TOPPADDING", (0,0), (-1,-1), 8),
            ("BOTTOMPADDING", (0,0), (-1,-1), 8),
        ]))
        story.append(t)
        story.append(Spacer(1, 14))

        def section(title, bg_hex, data_rows):
            if not data_rows:
                return
            story.append(Spacer(1, 10))
            story.append(T(title, fontSize=11, fontName="Helvetica-Bold",
                textColor=colors.HexColor("#0E2A47"), spaceAfter=6))
            hdr = ["#", "Loan No", "Customer Name", "Amount ₹", "Details"]
            tdata = [hdr]
            for i, r in enumerate(data_rows, 1):
                tdata.append([
                    str(i),
                    r.get("loan_no", ""),
                    r.get("name", ""),
                    r.get("amount", "—"),
                    Paragraph((r.get("details") or "")[:200],
                        ParagraphStyle("d", fontSize=7, fontName="Helvetica"))
                ])
            tb = Table(tdata, colWidths=[0.7*cm, 3.0*cm, 3.8*cm, 2.5*cm, 7.2*cm], repeatRows=1)
            tb.setStyle(TableStyle([
                ("BACKGROUND",    (0,0), (-1,0), colors.HexColor("#0E2A47")),
                ("TEXTCOLOR",     (0,0), (-1,0), colors.white),
                ("FONTNAME",      (0,0), (-1,0), "Helvetica-Bold"),
                ("FONTSIZE",      (0,0), (-1,-1), 8),
                ("ALIGN",         (0,0), (-1,0), "CENTER"),
                ("ROWBACKGROUNDS",(0,1), (-1,-1), [colors.HexColor(bg_hex), colors.white]),
                ("GRID",          (0,0), (-1,-1), 0.3, colors.HexColor("#DCE3E9")),
                ("VALIGN",        (0,0), (-1,-1), "TOP"),
                ("TOPPADDING",    (0,0), (-1,-1), 4),
                ("BOTTOMPADDING", (0,0), (-1,-1), 4),
                ("LEFTPADDING",   (0,0), (-1,-1), 4),
            ]))
            story.append(tb)

        section("✅ ELIGIBLE Customers",     "#E5F5EE", eligible)
        section("❌ NOT ELIGIBLE Customers", "#FBEAEA", not_eligible)
        section("⚠ Errors / Unknown",        "#FCF3E2", errors)

        doc.build(story)
        jobs[job_id]["pdf_path"] = path
        _log(job_id, f"📄 PDF ready — {len(eligible)} eligible, {len(not_eligible)} not eligible.")
        return path

    except Exception as e:
        _log(job_id, f"[PDF ERROR] {e}")
        traceback.print_exc()
        return None


# ═══════════════════════════════════════════════
#  AUTOMATION — PLAYWRIGHT
# ═══════════════════════════════════════════════

LOGIN_URL     = "https://usm.hdbfs.com"
CROSSSELL_URL = "https://xsellportal.hdbfssupport.com/CrossSellV2/Shared/Welcome/frmWelcome.aspx"
WAIT_MS       = 25000   # 25 seconds in ms
DELAY_SEC     = 2


def run_automation(job_id, username, password, loans):
    job = jobs[job_id]

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

        with sync_playwright() as pw:

            # ── Launch Chromium (Playwright bundles its own) ──
            browser = pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-setuid-sandbox",
                    "--disable-blink-features=AutomationControlled",
                ]
            )
            ctx  = browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/124.0.0.0 Safari/537.36"
            )
            page = ctx.new_page()
            _log(job_id, "[BROWSER] ✓ Playwright Chromium started")

            # ── Helpers ──────────────────────────────────────
            def slow_fill(selector, text, by_label=False):
                if by_label:
                    el = page.get_by_label(selector)
                else:
                    el = page.locator(selector)
                el.click()
                el.fill("")
                for ch in str(text):
                    page.keyboard.type(ch, delay=60)
                time.sleep(0.3)

            def try_fill(selectors, text):
                for sel in selectors:
                    try:
                        page.locator(sel).first.click(timeout=4000)
                        page.locator(sel).first.fill("")
                        for ch in str(text):
                            page.keyboard.type(ch, delay=60)
                        return True
                    except Exception:
                        continue
                return False

            def try_click(selectors):
                for sel in selectors:
                    try:
                        page.locator(sel).first.click(timeout=4000)
                        return True
                    except Exception:
                        continue
                return False

            def select_by_keywords(sel_id, keywords):
                try:
                    opts = page.locator(f"#{sel_id} option").all()
                    for opt in opts:
                        txt = opt.inner_text().upper()
                        if all(k.upper() in txt for k in keywords):
                            page.select_option(f"#{sel_id}", value=opt.get_attribute("value"))
                            return True
                    # fallback: pick index 1
                    if len(opts) > 1:
                        page.select_option(f"#{sel_id}", index=1)
                        return True
                except Exception:
                    pass
                return False

            # ── LOGIN ────────────────────────────────────────
            _log(job_id, "[LOGIN] Opening HDB login page...")
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
            time.sleep(4)

            # Username
            filled = try_fill([
                "input[name='username']",
                "input[placeholder*='Username' i]",
                "input[placeholder*='User' i]",
                "input[type='text']:not([disabled])",
            ], username)
            if not filled:
                _log(job_id, "[LOGIN] ✗ Username field not found")
                _set_status(job_id, "error")
                browser.close()
                return
            _log(job_id, f"[LOGIN] ✓ Username entered")
            time.sleep(0.5)

            # Click Next
            try_click([
                "button:has-text('Next')",
                "button:has-text('Continue')",
                "button[type='submit']",
                "input[type='submit']",
            ])
            _log(job_id, "[LOGIN] ✓ Clicked Next")
            time.sleep(3)

            # Password
            filled = try_fill([
                "input[name='password']",
                "input[type='password']",
                "input[placeholder*='Password' i]",
            ], password)
            if not filled:
                _log(job_id, "[LOGIN] ✗ Password field not found")
                _set_status(job_id, "error")
                browser.close()
                return
            _log(job_id, "[LOGIN] ✓ Password entered")
            time.sleep(0.5)

            # Click Login
            try_click([
                "button:has-text('Login')",
                "button:has-text('Sign In')",
                "button[type='submit']",
            ])
            _log(job_id, "[LOGIN] ✓ Clicked Login")
            time.sleep(4)

            # ── OTP ──────────────────────────────────────────
            src = page.content().lower()
            if any(kw in src for kw in ["otp","totp","authenticat","verification","mfa","cymmetri"]):
                _log(job_id, "[LOGIN] OTP page detected — waiting for OTP input on website...")
                _set_status(job_id, "waiting_otp")

                deadline = time.time() + 180
                otp_val  = None
                while time.time() < deadline:
                    otp_val = job.get("otp_value")
                    if otp_val:
                        break
                    time.sleep(1)

                if not otp_val:
                    _log(job_id, "[LOGIN] ✗ OTP timeout. Stopping.")
                    _set_status(job_id, "error")
                    browser.close()
                    return

                _set_status(job_id, "running")
                _log(job_id, f"[LOGIN] OTP received: {otp_val}")

                # Try box-by-box first (Cymmetri style)
                try:
                    boxes = page.locator("div.otp-container input, div[class*='otp'] input").all()
                    if boxes and len(boxes) >= 6:
                        for i, digit in enumerate(otp_val):
                            if i >= len(boxes): break
                            boxes[i].click()
                            page.keyboard.type(digit, delay=100)
                            time.sleep(0.2)
                        _log(job_id, "[LOGIN] OTP entered box by box")
                    else:
                        raise Exception("no boxes")
                except Exception:
                    # Single field fallback
                    try_fill([
                        "input[placeholder*='OTP' i]",
                        "input[placeholder*='code' i]",
                        "input[type='number']",
                        "input[inputmode='numeric']",
                    ], otp_val)

                time.sleep(0.8)
                try_click([
                    "button[data-label='OTP-VERIFY']",
                    "button:has-text('Verify')",
                    "button:has-text('Submit')",
                    "button[type='submit']",
                ])
                _log(job_id, "[LOGIN] ✓ OTP submitted")
                time.sleep(4)

            # Wait for dashboard
            try:
                page.wait_for_url(
                    lambda url: any(kw in url.lower() for kw in ["dashboard","workspace","xsellportal"]),
                    timeout=15000
                )
                _log(job_id, f"[LOGIN] ✓ Logged in: {page.url}")
            except PWTimeout:
                if "dashboard" in page.content().lower():
                    _log(job_id, "[LOGIN] ✓ Dashboard content found")
                else:
                    _log(job_id, f"[LOGIN] ⚠ Current URL: {page.url} — continuing anyway")
            time.sleep(2)

            # ── Open CrossSell portal ─────────────────────────
            _log(job_id, "[CROSSSELL] Looking for CrossSell tile...")
            if "xsellportal" not in page.url.lower():
                clicked = try_click([
                    "text=CrossSell",
                    "[title='CrossSell']",
                    "img[alt='CrossSell']",
                    "[class*='app']:has-text('CrossSell')",
                ])
                time.sleep(3)

                # Check if new tab opened
                pages = ctx.pages
                if len(pages) > 1:
                    page = pages[-1]
                    _log(job_id, f"[CROSSSELL] Switched to new tab: {page.url}")

                if "xsellportal" not in page.url.lower():
                    _log(job_id, "[CROSSSELL] Navigating directly to CrossSell portal...")
                    page.goto(CROSSSELL_URL, wait_until="domcontentloaded", timeout=30000)
                    time.sleep(5)

            _log(job_id, f"[CROSSSELL] Ready: {page.url}")

            # ── Navigate to Transactions → Search ─────────────
            def nav_to_search():
                try:
                    page.locator(
                        "ul.sidebar-menu a:has-text('Transactions'), "
                        "ul.nav a:has-text('Transactions'), "
                        "a:has(span:text-is('Transactions'))"
                    ).first.click(timeout=WAIT_MS)
                    time.sleep(1)
                    page.locator(
                        "ul.treeview-menu a:has-text('Search'), "
                        "ul.nav a:has-text('Search')"
                    ).first.click(timeout=WAIT_MS)
                    page.wait_for_selector("#Content_ddlProduct", timeout=WAIT_MS)
                    time.sleep(1.5)
                    return True
                except Exception as e:
                    _log(job_id, f"[NAV] ⚠ {e}")
                    return False

            _log(job_id, "[NAV] Going to Transactions → Search...")
            nav_to_search()
            _log(job_id, "[NAV] ✓ Search page loaded")

            # ── Process each loan ──────────────────────────────
            total = len(loans)
            job["total"] = total

            for idx, (loan_no, name) in enumerate(loans, start=1):
                job["current"] = idx - 1
                _log(job_id, f"\n[{idx}/{total}] Loan: {loan_no} | {name}")
                result = {"loan_no": loan_no, "name": name,
                          "status": "UNKNOWN", "amount": "", "details": ""}

                try:
                    # Go to search page
                    try:
                        nav_to_search()
                    except Exception:
                        pass

                    page.wait_for_selector("select", timeout=WAIT_MS)
                    time.sleep(0.8)

                    # Dropdowns
                    select_by_keywords("Content_ddlProduct",   ["RELATIONSHIP", "PERSONAL"])
                    time.sleep(1.5)
                    select_by_keywords("Content_ddlSearchType", ["PENDING"])
                    time.sleep(1)
                    select_by_keywords("Content_dllSearchBy",   ["CD", "LOAN"])
                    time.sleep(0.5)

                    # Enter loan number
                    inp_filled = False
                    for sel in [
                        "#Content_txtInputValue",
                        "input[id*='InputValue']",
                        "input[type='text']:not([disabled]):not([readonly])",
                    ]:
                        try:
                            page.locator(sel).first.click(timeout=5000)
                            page.locator(sel).first.fill("")
                            for ch in loan_no:
                                page.keyboard.type(ch, delay=50)
                            inp_filled = True
                            break
                        except Exception:
                            continue

                    if not inp_filled:
                        result["status"]  = "ERROR"
                        result["details"] = "Input field not found"
                        job["results"].append(result)
                        job["counts"][result["status"]] = job["counts"].get(result["status"], 0) + 1
                        continue

                    time.sleep(0.5)

                    # Click Search
                    clicked = try_click([
                        "#Content_btnSearch",
                        "input[type='submit'][value*='Search' i]",
                        "button:has-text('Search')",
                    ])
                    if not clicked:
                        page.keyboard.press("Enter")

                    time.sleep(3)

                    # Handle alert popup
                    try:
                        page.on("dialog", lambda d: d.accept())
                    except Exception:
                        pass
                    time.sleep(2)

                    # Wait for result page
                    try:
                        page.wait_for_url(
                            lambda u: "walkin.aspx" in u.lower(),
                            timeout=10000
                        )
                    except PWTimeout:
                        pass
                    time.sleep(2)

                    _log(job_id, f"  URL: {page.url}")

                    # Check records not found
                    try:
                        warn = page.locator("#Content_divWarning")
                        if warn.is_visible() and "records not found" in warn.inner_text().lower():
                            result["status"]  = "NOT ELIGIBLE"
                            result["details"] = "Records not found"
                            _log(job_id, "  ✗ NOT FOUND")
                            job["results"].append(result)
                            job["counts"][result["status"]] = job["counts"].get(result["status"], 0) + 1
                            continue
                    except Exception:
                        pass

                    # Check ELIGIBLE
                    eligible = False
                    try:
                        det = page.locator("#Content_customerDetails")
                        style = det.get_attribute("style") or ""
                        if "display:none" not in style.replace(" ", "").lower():
                            lv = page.locator("#Content_txtLoanNo").input_value()
                            if lv and lv.strip():
                                eligible = True
                    except Exception:
                        pass

                    def gv(fid):
                        try:
                            return page.locator(f"#{fid}").input_value() or ""
                        except Exception:
                            return ""

                    if eligible:
                        existing = gv("Content_txtExistingProduct")
                        offer    = gv("Content_txtOfferProduct")
                        branch   = gv("Content_txtBranchName")
                        alt_ph   = gv("Content_txtAltPhoneNo")
                        amount   = gv("Content_txtRequiredLoanAmt")
                        tenor    = gv("Content_txtLoanTenor")
                        roi      = gv("Content_txtROI")
                        result["status"]  = "ELIGIBLE"
                        result["amount"]  = amount
                        result["details"] = (f"Existing: {existing} | Offer: {offer} | "
                                             f"Branch: {branch} | Alt: {alt_ph} | "
                                             f"Tenor: {tenor}m | ROI: {roi}%")
                        _log(job_id, f"  ✅ ELIGIBLE | ₹{amount} | {branch}")
                    else:
                        err = ""
                        try:
                            err = page.locator("#Content_lblErrorMessage").inner_text().strip()
                        except Exception:
                            pass
                        if not err:
                            content = page.content().lower()
                            if "not eligible" in content:
                                err = "Not eligible for Personal Loan"
                        result["status"]  = "NOT ELIGIBLE" if err else "UNKNOWN"
                        result["details"] = err
                        _log(job_id, f"  ❌ {result['status']}: {err}")

                except Exception as e:
                    result["status"]  = "ERROR"
                    result["details"] = str(e)[:200]
                    _log(job_id, f"  [ERROR] {e}")

                job["results"].append(result)
                job["counts"][result["status"]] = job["counts"].get(result["status"], 0) + 1
                job["current"] = idx
                time.sleep(DELAY_SEC)

            browser.close()
            _log(job_id, "\n✓ All done. Generating PDF...")
            generate_pdf(job_id)
            _set_status(job_id, "done")

    except Exception as e:
        _log(job_id, f"[FATAL] {e}")
        traceback.print_exc()
        _set_status(job_id, "error")
        jobs[job_id]["error_message"] = str(e)


# ═══════════════════════════════════════════════
#  FLASK ROUTES
# ═══════════════════════════════════════════════

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/start", methods=["POST"])
def api_start():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()

    if not username or not password:
        return jsonify({"error": "Username and password required."}), 400

    f = request.files.get("customer_file")
    if not f:
        return jsonify({"error": "No file uploaded."}), 400

    loans = []
    for line in f.read().decode("utf-8-sig").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(",", 1)
        loan_no = parts[0].strip()
        name    = parts[1].strip() if len(parts) > 1 else ""
        if loan_no:
            loans.append((loan_no, name))

    if not loans:
        return jsonify({"error": "No valid loan numbers in file."}), 400

    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "status":        "running",
        "current":       0,
        "total":         len(loans),
        "results":       [],
        "counts":        {},
        "logs":          [f"[INFO] {len(loans)} loans loaded. Starting..."],
        "pdf_path":      None,
        "otp_value":     None,
        "error_message": None,
    }

    threading.Thread(
        target=run_automation,
        args=(job_id, username, password, loans),
        daemon=True
    ).start()

    return jsonify({"job_id": job_id, "total": len(loans)})


@app.route("/api/status/<job_id>")
def api_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({
        "status":        job["status"],
        "current":       job["current"],
        "total":         job["total"],
        "counts":        job["counts"],
        "logs":          job["logs"][-100:],
        "error_message": job.get("error_message"),
    })


@app.route("/api/submit_otp/<job_id>", methods=["POST"])
def api_submit_otp(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    otp = (request.get_json() or {}).get("otp", "").strip()
    if not otp or len(otp) != 6 or not otp.isdigit():
        return jsonify({"error": "Enter 6 digit OTP"}), 400
    job["otp_value"] = otp
    return jsonify({"ok": True})


@app.route("/download/<job_id>")
def download_pdf(job_id):
    job = jobs.get(job_id)
    if not job or not job.get("pdf_path") or not os.path.exists(job["pdf_path"]):
        return "PDF not ready", 404
    return send_file(
        job["pdf_path"],
        as_attachment=True,
        download_name=f"HDB_Report_{datetime.date.today()}.pdf",
        mimetype="application/pdf"
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
