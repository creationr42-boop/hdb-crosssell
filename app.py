"""
HDB CrossSell — Web Interface Backend
======================================
Flask server that:
  1. Accepts uploaded customer.txt
  2. Runs Selenium automation in background thread
  3. Streams live progress via /api/status/<job_id>
  4. Pauses automation for OTP and waits for browser input
  5. Generates PDF report and serves it for download

Deploy on Railway.app (supports headless Chrome via nixpacks).
"""

import os, uuid, time, threading, datetime, traceback, json
from flask import Flask, request, jsonify, send_file, render_template

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB upload limit

# ── In-memory job store (fine for single-user / small scale) ──────────────────
jobs = {}   # job_id -> dict


# ═════════════════════════════════════════════════════════════════════
#  PDF generation (uses reportlab — pure Python, no Chrome needed)
# ═════════════════════════════════════════════════════════════════════

def generate_pdf(job_id):
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.platypus import (
            SimpleDocTemplate, Table, TableStyle, Paragraph,
            Spacer, HRFlowable
        )

        job   = jobs[job_id]
        rows  = job["results"]
        path  = f"/tmp/hdb_report_{job_id}.pdf"

        doc = SimpleDocTemplate(
            path, pagesize=A4,
            leftMargin=1.5*cm, rightMargin=1.5*cm,
            topMargin=1.8*cm, bottomMargin=1.8*cm
        )
        styles = getSampleStyleSheet()
        story  = []

        # ── Title ──
        title_style = ParagraphStyle(
            "title", fontSize=16, fontName="Helvetica-Bold",
            textColor=colors.HexColor("#0E2A47"), spaceAfter=4
        )
        sub_style = ParagraphStyle(
            "sub", fontSize=9, fontName="Helvetica",
            textColor=colors.HexColor("#6B7785"), spaceAfter=10
        )
        story.append(Paragraph("HDB CrossSell — Eligibility Report", title_style))
        story.append(Paragraph(
            f"Generated: {datetime.datetime.now().strftime('%d %b %Y, %I:%M %p')}   |   "
            f"Total Checked: {len(rows)}", sub_style
        ))
        story.append(HRFlowable(width="100%", thickness=1,
                                color=colors.HexColor("#0E2A47"), spaceAfter=12))

        eligible     = [r for r in rows if r["status"] == "ELIGIBLE"]
        not_eligible = [r for r in rows if r["status"] == "NOT ELIGIBLE"]
        errors       = [r for r in rows if r["status"] not in ("ELIGIBLE", "NOT ELIGIBLE")]

        def summary_table():
            data = [
                ["✅ Eligible", "❌ Not Eligible", "⚠ Errors / Unknown"],
                [str(len(eligible)), str(len(not_eligible)), str(len(errors))]
            ]
            t = Table(data, colWidths=[5.5*cm, 5.5*cm, 5.5*cm])
            t.setStyle(TableStyle([
                ("BACKGROUND",  (0,0), (-1,0), colors.HexColor("#0E2A47")),
                ("TEXTCOLOR",   (0,0), (-1,0), colors.white),
                ("FONTNAME",    (0,0), (-1,0), "Helvetica-Bold"),
                ("FONTSIZE",    (0,0), (-1,-1), 11),
                ("ALIGN",       (0,0), (-1,-1), "CENTER"),
                ("ROWBACKGROUNDS", (0,1), (-1,-1),
                 [colors.HexColor("#E5F5EE"), colors.HexColor("#FBEAEA"),
                  colors.HexColor("#FCF3E2")]),
                ("GRID",        (0,0), (-1,-1), 0.4, colors.HexColor("#DCE3E9")),
                ("TOPPADDING",  (0,0), (-1,-1), 7),
                ("BOTTOMPADDING",(0,0), (-1,-1), 7),
            ]))
            return t

        story.append(summary_table())
        story.append(Spacer(1, 16))

        def section(title_txt, color_hex, data_rows):
            if not data_rows:
                return
            sec_style = ParagraphStyle(
                "sec", fontSize=11, fontName="Helvetica-Bold",
                textColor=colors.HexColor(color_hex), spaceAfter=6, spaceBefore=14
            )
            story.append(Paragraph(title_txt, sec_style))

            header = ["#", "Loan Number", "Customer Name", "Amount (₹)", "Details"]
            table_data = [header]
            for i, r in enumerate(data_rows, start=1):
                # Wrap long details text
                detail_para = Paragraph(
                    (r.get("details") or "")[:200],
                    ParagraphStyle("dp", fontSize=7, fontName="Helvetica",
                                   textColor=colors.HexColor("#333333"))
                )
                table_data.append([
                    str(i),
                    r.get("loan_no", ""),
                    r.get("name", ""),
                    r.get("amount", "—"),
                    detail_para,
                ])

            col_w = [0.7*cm, 3.2*cm, 4.0*cm, 2.5*cm, 6.8*cm]
            t = Table(table_data, colWidths=col_w, repeatRows=1)
            row_bg = colors.HexColor("#E5F5EE") if "Eligible" in title_txt and "Not" not in title_txt \
                     else colors.HexColor("#FBEAEA") if "Not" in title_txt \
                     else colors.HexColor("#FCF3E2")
            t.setStyle(TableStyle([
                ("BACKGROUND",   (0,0), (-1,0), colors.HexColor("#0E2A47")),
                ("TEXTCOLOR",    (0,0), (-1,0), colors.white),
                ("FONTNAME",     (0,0), (-1,0), "Helvetica-Bold"),
                ("FONTSIZE",     (0,0), (-1,0), 8),
                ("ALIGN",        (0,0), (-1,0), "CENTER"),
                ("FONTSIZE",     (0,1), (-1,-1), 8),
                ("ROWBACKGROUNDS",(0,1), (-1,-1), [row_bg, colors.white]),
                ("GRID",         (0,0), (-1,-1), 0.3, colors.HexColor("#DCE3E9")),
                ("VALIGN",       (0,0), (-1,-1), "TOP"),
                ("TOPPADDING",   (0,0), (-1,-1), 5),
                ("BOTTOMPADDING",(0,0), (-1,-1), 5),
                ("LEFTPADDING",  (0,0), (-1,-1), 4),
            ]))
            story.append(t)

        section("✅ ELIGIBLE Customers", "#1E8E5A", eligible)
        section("❌ NOT ELIGIBLE Customers", "#C24A4A", not_eligible)
        section("⚠ Errors / Unknown", "#C98A1F", errors)

        doc.build(story)
        jobs[job_id]["pdf_path"] = path
        _log(job_id, f"\n📄 PDF report ready — {len(eligible)} eligible, "
                     f"{len(not_eligible)} not eligible.")
        return path

    except Exception as e:
        _log(job_id, f"[PDF ERROR] {e}")
        traceback.print_exc()
        return None


# ═════════════════════════════════════════════════════════════════════
#  Selenium automation (runs in background thread)
# ═════════════════════════════════════════════════════════════════════

LOGIN_URL     = "https://usm.hdbfs.com"
CROSSSELL_URL = "https://xsellportal.hdbfssupport.com/CrossSellV2/Shared/Welcome/frmWelcome.aspx"
WAIT_TIMEOUT  = 25
DELAY_BETWEEN = 2


def _log(job_id, msg):
    jobs[job_id]["logs"].append(msg)
    if len(jobs[job_id]["logs"]) > 500:
        jobs[job_id]["logs"] = jobs[job_id]["logs"][-400:]


def _set_status(job_id, status):
    jobs[job_id]["status"] = status


def run_automation(job_id, username, password, loans, headless):
    job = jobs[job_id]

    try:
        from selenium import webdriver
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        from selenium.webdriver.support.ui import WebDriverWait, Select
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.chrome.options import Options
        from selenium.common.exceptions import (
            TimeoutException, NoSuchElementException, NoAlertPresentException
        )

        # ── Chrome options ──
        opts = Options()
        opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--disable-setuid-sandbox")
        opts.add_argument("--window-size=1280,900")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_argument("--disable-extensions")
        opts.add_argument("--disable-software-rasterizer")
        opts.add_argument("--remote-debugging-port=9222")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)

        # ── Find Chromium/Chrome binary (supports Nixpacks nix store paths) ──
        import shutil, glob

        def find_binary(names):
            for name in names:
                path = shutil.which(name)
                if path:
                    return path
            for name in names:
                matches = glob.glob(f"/nix/store/*/bin/{name}") + glob.glob(f"/nix/store/*/{name}")
                if matches:
                    return sorted(matches)[-1]
            return None

        chrome_bin = find_binary(["chromium", "chromium-browser", "chromium-headless-shell", "google-chrome", "google-chrome-stable"])
        chromedriver_bin = find_binary(["chromedriver"])

        _log(job_id, f"[BROWSER] chrome_bin={chrome_bin}")
        _log(job_id, f"[BROWSER] chromedriver={chromedriver_bin}")

        if not chrome_bin:
            _log(job_id, "[BROWSER] ✗ No Chrome/Chromium found!")
            _set_status(job_id, "error")
            jobs[job_id]["error_message"] = "Chromium not found on server"
            return

        if not chromedriver_bin:
            _log(job_id, "[BROWSER] ✗ chromedriver not found!")
            _set_status(job_id, "error")
            jobs[job_id]["error_message"] = "chromedriver not found on server"
            return

        opts.binary_location = chrome_bin

        driver = None
        try:
            service = Service(chromedriver_bin)
            driver  = webdriver.Chrome(service=service, options=opts)
            _log(job_id, "[BROWSER] ✓ Chrome started!")
        except Exception as e:
            _log(job_id, f"[BROWSER] ✗ Failed: {e}")
            _set_status(job_id, "error")
            jobs[job_id]["error_message"] = str(e)
            return

        driver.execute_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )
        wait = WebDriverWait(driver, WAIT_TIMEOUT)

        # ── helpers ──────────────────────────────────────────────────
        def slow_type(el, text, delay=0.06):
            el.click(); time.sleep(0.2); el.clear(); time.sleep(0.15)
            for ch in str(text):
                el.send_keys(ch); time.sleep(delay)
            time.sleep(0.2)

        def try_click(xpaths):
            for xp in xpaths:
                try:
                    b = driver.find_element(By.XPATH, xp)
                    if b.is_displayed() and b.is_enabled():
                        b.click(); return True
                except NoSuchElementException:
                    pass
            return False

        def select_opt(sel_id, keywords, label):
            try:
                el  = wait.until(EC.presence_of_element_located((By.ID, sel_id)))
                sel = Select(el)
                for opt in sel.options:
                    if all(k.upper() in opt.text.upper() for k in keywords):
                        sel.select_by_value(opt.get_attribute("value"))
                        _log(job_id, f"  ✓ {label}: {opt.text}")
                        return True
                if len(sel.options) > 1:
                    sel.select_by_index(1)
                    _log(job_id, f"  ✓ {label} (index 1): {sel.first_selected_option.text}")
                    return True
            except (TimeoutException, NoSuchElementException):
                pass
            return False

        def nav_to_search():
            trans_el = wait.until(EC.element_to_be_clickable((By.XPATH,
                "//ul[contains(@class,'sidebar-menu') or contains(@class,'nav')]"
                "//a[.//span[normalize-space()='Transactions'] or normalize-space()='Transactions']"
            )))
            driver.execute_script("arguments[0].click();", trans_el)
            wait.until(EC.any_of(
                EC.element_to_be_clickable((By.XPATH,
                    "//ul[contains(@class,'treeview-menu') or contains(@class,'nav')]"
                    "//a[contains(normalize-space(),'Search')]"
                )),
                EC.url_contains("Transactions/Search.aspx")
            ))
            search_el = wait.until(EC.element_to_be_clickable((By.XPATH,
                "//ul[contains(@class,'treeview-menu') or contains(@class,'nav')]"
                "//a[contains(normalize-space(),'Search')]"
            )))
            driver.execute_script("arguments[0].click();", search_el)
            wait.until(EC.presence_of_element_located((By.ID, "Content_ddlProduct")))
            time.sleep(1.5)

        # ── LOGIN ────────────────────────────────────────────────────
        _log(job_id, "[LOGIN] Opening HDB login page...")
        driver.get(LOGIN_URL)
        time.sleep(4)

        try:
            uf = wait.until(EC.element_to_be_clickable((By.XPATH,
                "//input[@name='username'] | "
                "//input[contains(@placeholder,'Username')] | "
                "//input[@type='text' and not(@disabled)]"
            )))
            slow_type(uf, username)
            _log(job_id, f"[LOGIN] Username entered: {username}")
        except TimeoutException:
            _log(job_id, "[LOGIN] ⚠ Username field not found — check site manually")
            _set_status(job_id, "error")
            driver.quit()
            return

        time.sleep(0.5)
        if not try_click([
            "//button[contains(text(),'Next')]",
            "//button[contains(text(),'Continue')]",
            "//button[@type='submit']",
            "//input[@type='submit']",
        ]):
            uf.send_keys(Keys.RETURN)
        _log(job_id, "[LOGIN] Clicked Next")
        time.sleep(3)

        try:
            pf = wait.until(EC.element_to_be_clickable((By.XPATH,
                "//input[@name='password'] | //input[@type='password']"
            )))
            slow_type(pf, password)
            _log(job_id, "[LOGIN] Password entered")
        except TimeoutException:
            _log(job_id, "[LOGIN] ⚠ Password field not found")
            _set_status(job_id, "error")
            driver.quit()
            return

        time.sleep(0.5)
        if not try_click([
            "//button[contains(text(),'Login')]",
            "//button[contains(text(),'Sign In')]",
            "//button[@type='submit']",
        ]):
            pf.send_keys(Keys.RETURN)
        _log(job_id, "[LOGIN] Clicked Login")
        time.sleep(4)

        # ── OTP ──────────────────────────────────────────────────────
        page_src = driver.page_source.lower()
        if any(kw in page_src for kw in ["otp","totp","authenticat","verification code","mfa","cymmetri"]):
            _log(job_id, "[LOGIN] OTP page detected — waiting for you to enter code on website...")
            _set_status(job_id, "waiting_otp")

            # Wait up to 3 minutes for OTP to be submitted via web UI
            deadline = time.time() + 180
            while time.time() < deadline:
                otp_val = job.get("otp_value")
                if otp_val:
                    break
                time.sleep(1)
            else:
                _log(job_id, "[LOGIN] ✗ OTP timeout (3 minutes). Stopping.")
                _set_status(job_id, "error")
                driver.quit()
                return

            _set_status(job_id, "running")
            _log(job_id, f"[LOGIN] OTP received: {otp_val}")

            # Enter OTP into browser
            try:
                otp_boxes = wait.until(EC.presence_of_all_elements_located(
                    (By.XPATH, "//div[contains(@class,'otp-container')]//input")
                ))
                for i, digit in enumerate(otp_val):
                    if i >= len(otp_boxes): break
                    b = otp_boxes[i]; b.click(); time.sleep(0.1); b.send_keys(digit)
                    driver.execute_script(
                        "arguments[0].dispatchEvent(new Event('input',{bubbles:true}));"
                        "arguments[0].dispatchEvent(new Event('change',{bubbles:true}));", b
                    )
                    time.sleep(0.2)
                _log(job_id, "[LOGIN] OTP digits entered")
            except TimeoutException:
                try:
                    otp_field = wait.until(EC.element_to_be_clickable((By.XPATH,
                        "//input[contains(@placeholder,'OTP') or @type='number']"
                    )))
                    slow_type(otp_field, otp_val)
                except TimeoutException:
                    _log(job_id, "[LOGIN] ✗ Could not find OTP input field")

            time.sleep(0.8)
            try:
                vb = wait.until(EC.element_to_be_clickable((By.XPATH,
                    "//button[@data-label='OTP-VERIFY'] | "
                    "//button[contains(.,'Verify') and not(@disabled)]"
                )))
                vb.click()
                _log(job_id, "[LOGIN] Clicked Verify")
            except TimeoutException:
                try:
                    driver.find_element(By.XPATH, "//input[@type='number']").send_keys(Keys.RETURN)
                except Exception:
                    pass
            time.sleep(4)

        # ── Wait for dashboard ────────────────────────────────────────
        try:
            wait.until(lambda d: any(kw in d.current_url.lower()
                                     for kw in ["dashboard","workspace","xsellportal"]))
            _log(job_id, f"[LOGIN] ✓ Logged in: {driver.current_url}")
        except TimeoutException:
            if "dashboard" in driver.page_source.lower():
                _log(job_id, "[LOGIN] ✓ Dashboard detected")
            else:
                _log(job_id, "[LOGIN] ⚠ Could not confirm login. Continuing anyway...")
        time.sleep(2)

        # ── Open CrossSell portal ─────────────────────────────────────
        _log(job_id, "[CROSSSELL] Looking for CrossSell tile...")
        original_tabs = driver.window_handles
        clicked = False
        for sel in [
            "//*[contains(text(),'CrossSell')]",
            "//*[@title='CrossSell']",
            "//img[@alt='CrossSell']",
        ]:
            try:
                el = wait.until(EC.element_to_be_clickable((By.XPATH, sel)))
                try: el.click()
                except Exception: driver.execute_script("arguments[0].click();", el)
                _log(job_id, "[CROSSSELL] ✓ Clicked CrossSell tile")
                clicked = True
                break
            except TimeoutException:
                continue

        new_tab = None
        for _ in range(20):
            tabs = driver.window_handles
            if len(tabs) > len(original_tabs):
                new_tabs = [t for t in tabs if t not in original_tabs]
                if new_tabs: new_tab = new_tabs[0]; break
            time.sleep(0.5)

        if new_tab:
            driver.switch_to.window(new_tab)
            _log(job_id, f"[CROSSSELL] Switched to new tab: {driver.current_url}")
        elif not clicked:
            driver.get(CROSSSELL_URL)
            time.sleep(5)

        _log(job_id, f"[CROSSSELL] Ready: {driver.current_url}")

        # ── Navigate to Search page ───────────────────────────────────
        _log(job_id, "[NAV] Going to Transactions → Search...")
        try:
            nav_to_search()
            _log(job_id, "[NAV] ✓ Search page loaded")
        except TimeoutException:
            _log(job_id, "[NAV] ⚠ Navigation may have failed — will retry per loan")

        # ── Process each loan ─────────────────────────────────────────
        total = len(loans)
        job["total"] = total

        for idx, (loan_no, name) in enumerate(loans, start=1):
            job["current"] = idx - 1
            _log(job_id, f"\n[{idx}/{total}] Loan: {loan_no}  |  {name}")
            result = {"loan_no": loan_no, "name": name,
                      "status": "UNKNOWN", "amount": "", "details": ""}
            try:
                # Navigate back to search
                try:
                    nav_to_search()
                except TimeoutException:
                    wait.until(EC.presence_of_element_located((By.ID, "Content_ddlProduct")))

                wait.until(EC.presence_of_element_located((By.XPATH, "//select")))
                time.sleep(0.8)

                select_opt("Content_ddlProduct",  ["RELATIONSHIP","PERSONAL"], "Product")
                time.sleep(1.5)
                select_opt("Content_ddlSearchType", ["PENDING"], "Search Type")
                time.sleep(1)
                select_opt("Content_dllSearchBy",  ["CD","LOAN"], "Search By")
                time.sleep(0.5)

                inp = None
                for sel in [
                    (By.ID,    "Content_txtInputValue"),
                    (By.XPATH, "//input[contains(@id,'InputValue')]"),
                    (By.XPATH, "//input[@type='text' and not(@disabled) and not(@readonly)]"),
                ]:
                    try:
                        inp = wait.until(EC.element_to_be_clickable(sel))
                        break
                    except TimeoutException:
                        continue

                if not inp:
                    result["status"] = "ERROR"
                    result["details"] = "Input field not found"
                    job["results"].append(result)
                    job["counts"][result["status"]] = job["counts"].get(result["status"], 0) + 1
                    continue

                slow_type(inp, loan_no, delay=0.05)
                time.sleep(0.5)

                sb = None
                for sel in [
                    (By.ID,    "Content_btnSearch"),
                    (By.XPATH, "//input[@type='submit' and contains(@value,'Search')]"),
                    (By.XPATH, "//button[contains(text(),'Search')]"),
                ]:
                    try:
                        b = driver.find_element(*sel)
                        if b.is_displayed():
                            driver.execute_script("arguments[0].click();", b)
                            sb = True; break
                    except NoSuchElementException:
                        continue
                if not sb:
                    inp.send_keys(Keys.RETURN)

                time.sleep(3)

                # dismiss alert
                for _ in range(4):
                    try:
                        al = driver.switch_to.alert
                        al.accept(); time.sleep(2); break
                    except NoAlertPresentException:
                        time.sleep(0.8)

                time.sleep(3)
                try:
                    wait.until(lambda d: "walkin.aspx" in d.current_url.lower())
                except TimeoutException:
                    pass

                # records-not-found check
                try:
                    wd = driver.find_element(By.ID, "Content_divWarning")
                    if wd.is_displayed() and "records not found" in wd.text.lower():
                        result["status"]  = "NOT ELIGIBLE"
                        result["details"] = "Records not found"
                        _log(job_id, "  ✗ NOT FOUND")
                        job["results"].append(result)
                        job["counts"][result["status"]] = job["counts"].get(result["status"], 0) + 1
                        continue
                except NoSuchElementException:
                    pass

                # eligible check
                eligible = False
                try:
                    dd = driver.find_element(By.ID, "Content_customerDetails")
                    style = (dd.get_attribute("style") or "").lower().replace(" ","")
                    if "display:none" not in style:
                        try:
                            lv = driver.find_element(By.ID, "Content_txtLoanNo").get_attribute("value")
                            if lv and lv.strip(): eligible = True
                        except NoSuchElementException:
                            pass
                except NoSuchElementException:
                    pass

                def gv(fid):
                    try: return driver.find_element(By.ID, fid).get_attribute("value").strip()
                    except NoSuchElementException: return ""

                if eligible:
                    existing = gv("Content_txtExistingProduct")
                    offer    = gv("Content_txtOfferProduct")
                    branch   = gv("Content_txtBranchName")
                    alt_ph   = gv("Content_txtAltPhoneNo")
                    amount   = gv("Content_txtRequiredLoanAmt")
                    tenor    = gv("Content_txtLoanTenor")
                    roi      = gv("Content_txtROI")
                    details  = (f"Existing: {existing} | Offer: {offer} | "
                                f"Branch: {branch} | Alt Phone: {alt_ph} | "
                                f"Tenor: {tenor}m | ROI: {roi}%")
                    result["status"]  = "ELIGIBLE"
                    result["amount"]  = amount
                    result["details"] = details
                    _log(job_id, f"  ✅ ELIGIBLE | ₹{amount} | Branch: {branch}")
                else:
                    err = ""
                    try:
                        err = driver.find_element(By.ID, "Content_lblErrorMessage").text.strip()
                    except NoSuchElementException:
                        pass
                    if not err and "not eligible" in driver.page_source.lower():
                        err = "Not eligible for Personal Loan"
                    result["status"]  = "NOT ELIGIBLE" if err or "not eligible" in driver.page_source.lower() else "UNKNOWN"
                    result["details"] = err
                    _log(job_id, f"  ❌ {result['status']}: {err}")

            except Exception as e:
                result["status"]  = "ERROR"
                result["details"] = str(e)[:200]
                _log(job_id, f"  [ERROR] {e}")

            job["results"].append(result)
            job["counts"][result["status"]] = job["counts"].get(result["status"], 0) + 1
            job["current"] = idx
            time.sleep(DELAY_BETWEEN)

        driver.quit()
        _log(job_id, "\n✓ All loans processed. Generating PDF...")
        generate_pdf(job_id)
        _set_status(job_id, "done")

    except Exception as e:
        _log(job_id, f"[FATAL] {e}")
        traceback.print_exc()
        _set_status(job_id, "error")
        jobs[job_id]["error_message"] = str(e)


# ═════════════════════════════════════════════════════════════════════
#  Flask routes
# ═════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/start", methods=["POST"])
def api_start():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()
    headless = request.form.get("headless", "true").lower() == "true"

    if not username or not password:
        return jsonify({"error": "Username and password are required."}), 400

    file = request.files.get("customer_file")
    if not file:
        return jsonify({"error": "No customer file uploaded."}), 400

    lines = file.read().decode("utf-8-sig").splitlines()
    loans = []
    for line in lines:
        line = line.strip()
        if not line: continue
        parts = line.split(",", 1)
        loan_no = parts[0].strip()
        name    = parts[1].strip() if len(parts) > 1 else ""
        if loan_no:
            loans.append((loan_no, name))

    if not loans:
        return jsonify({"error": "No valid loan numbers found in file."}), 400

    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "status":        "running",
        "current":       0,
        "total":         len(loans),
        "results":       [],
        "counts":        {},
        "logs":          [f"[INFO] {len(loans)} loans loaded. Starting automation..."],
        "pdf_path":      None,
        "otp_value":     None,
        "error_message": None,
    }

    t = threading.Thread(
        target=run_automation,
        args=(job_id, username, password, loans, headless),
        daemon=True
    )
    t.start()

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
        "logs":          job["logs"][-80:],
        "error_message": job.get("error_message"),
    })


@app.route("/api/submit_otp/<job_id>", methods=["POST"])
def api_submit_otp(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    data = request.get_json()
    otp  = (data or {}).get("otp", "").strip()
    if not otp or len(otp) != 6 or not otp.isdigit():
        return jsonify({"error": "Invalid OTP — must be 6 digits"}), 400
    job["otp_value"] = otp
    return jsonify({"ok": True})


@app.route("/download/<job_id>")
def download_pdf(job_id):
    job = jobs.get(job_id)
    if not job or not job.get("pdf_path") or not os.path.exists(job["pdf_path"]):
        return "PDF not ready yet", 404
    return send_file(
        job["pdf_path"],
        as_attachment=True,
        download_name=f"HDB_Eligibility_Report_{datetime.date.today()}.pdf",
        mimetype="application/pdf"
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
