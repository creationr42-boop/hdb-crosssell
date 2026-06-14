# HDB CrossSell — Web Eligibility Checker
## Deploy on Railway in 5 minutes (FREE)

---

### WHAT THIS DOES
- You open the website on your phone or laptop
- Upload your customer.txt file
- Enter HDB username + password
- Automation runs on the server (headless Chrome)
- OTP popup appears on screen when needed — you type it
- When done: click Download PDF — ready report with Eligible / Not Eligible sections

---

### STEP 1 — Create Railway account
1. Go to https://railway.app
2. Click "Start a New Project" → Sign up with GitHub (free)

---

### STEP 2 — Upload this code to GitHub
1. Go to https://github.com → Click "+" → "New repository"
2. Name it: `hdb-crosssell` → Click "Create repository"
3. Click "uploading an existing file"
4. Upload ALL files from this folder:
   - app.py
   - requirements.txt
   - Procfile
   - nixpacks.toml
   - railway.json
   - templates/index.html
5. Click "Commit changes"

---

### STEP 3 — Deploy on Railway
1. Go to https://railway.app/new
2. Click "Deploy from GitHub repo"
3. Select your `hdb-crosssell` repository
4. Railway will auto-detect Python + install Chrome
5. Wait ~3 minutes for build to finish
6. Click your project → "Settings" → "Generate Domain"
7. You get a URL like: `https://hdb-crosssell-production.up.railway.app`

**That URL works on any phone, laptop, tablet — anywhere!**

---

### STEP 4 — Use the website
1. Open your Railway URL in browser
2. Enter HDB username and password
3. Upload your customer.txt file
4. Click "Start Checking"
5. If OTP popup appears → open Cymmetri app → enter code
6. Watch live progress on screen
7. When done → click "Download PDF Report"

---

### CUSTOMER.TXT FORMAT
One customer per line:
```
74449308,TANYA WO RAHUL
74512345,RAMESH KUMAR
74667890,PRIYA SHARMA
```

---

### COSTS
- Railway free tier: 500 hours/month (enough for daily use)
- No credit card needed for free tier
- If you need more: $5/month hobby plan

---

### TROUBLESHOOTING

**"Chrome not found" error:**
- The nixpacks.toml automatically installs Chrome on Railway
- If running locally: install Chrome manually and run `pip install -r requirements.txt`

**OTP not working:**
- Make sure you enter the code within 3 minutes
- The code refreshes every 30 seconds — use the CURRENT code shown in Cymmetri app

**Automation stops at login:**
- Check your username/password are correct
- HDB site may be down — try again later

---

### RUNNING LOCALLY (your own PC)
```
pip install flask selenium reportlab webdriver-manager gunicorn
python app.py
```
Then open: http://localhost:5000

---

### FILES IN THIS PACKAGE
```
hdb_web/
├── app.py                  ← Flask backend (main automation)
├── requirements.txt        ← Python packages
├── Procfile                ← Railway start command
├── nixpacks.toml           ← Tells Railway to install Chrome
├── railway.json            ← Railway config
├── customer.txt            ← Sample input (replace with yours)
└── templates/
    └── index.html          ← The website UI
```
