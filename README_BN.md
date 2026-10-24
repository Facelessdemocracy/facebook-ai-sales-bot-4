# SalesBot Pro — Facebook Messenger AI Sales Bot + Admin Dashboard

এই প্রজেক্টে Google Sheets-এর বদলে SQLite database ব্যবহার করা হয়েছে। Dashboard থেকে একাধিক Facebook Page, যেকোনো সংখ্যক product, stock, price, order এবং Dashboard user পরিচালনা করা যায়। Meta Messenger-এর live webhook চালাতে public HTTPS hosting দরকার।

## Dashboard ফিচার
- Secure login: password hash করে database-এ রাখা হয়; POST form-এ CSRF protection আছে।
- Overview: মোট product, stock, order, pending order ও delivered sales.
- Products: Add/Edit/Delete, price, stock, SKU/code, image URL, size, color, description, page visibility, active/inactive.
- Facebook Pages: নতুন Page config যোগ, একই Page ID দিয়ে settings update, Page On/Off, per-Page delivery fee, optional Telegram human-handoff notification.
- User Management: Admin, Manager, Staff account; নতুন user, account active/inactive, role update, password reset.
- Orders: search, customer/order details, status change.
- Database: SQLite; Google Sheets প্রয়োজন নেই।

## User roles
- **Admin:** সব feature, including User Management.
- **Manager:** products, orders, Facebook Page settings পরিচালনা করতে পারে; user management পারে না।
- **Staff:** overview ও order status workflow ব্যবহার করতে পারে; products, Pages ও user settings পরিবর্তন করতে পারে না।

প্রথম Admin account `.env`-এর `ADMIN_USERNAME` ও `ADMIN_PASSWORD` দিয়ে প্রথমবার database-এ তৈরি হয়। নতুন server-এ launch করার আগে শক্তিশালী password এবং `FLASK_SECRET_KEY` সেট করুন। Password কমপক্ষে ১০ অক্ষর ব্যবহার করুন।

## Windows-এ লোকালি চালানো
Python 3.10+ ইনস্টল করুন। Command Prompt-এ project folder-এ গিয়ে:

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

`.env` ফাইল খুলে অন্তত এগুলো পরিবর্তন করুন:
- `ADMIN_USERNAME`
- `ADMIN_PASSWORD`
- `FLASK_SECRET_KEY` (দীর্ঘ, random secret)
- `META_VERIFY_TOKEN`
- `META_APP_SECRET`
- `OPENAI_API_KEY` (AI conversational replies চাইলে)

তারপর চালান:

```bat
python app.py
```

Dashboard: `http://127.0.0.1:8000/admin/login`

লোকাল Dashboard পরীক্ষা করা যাবে, কিন্তু `127.0.0.1` Meta webhook থেকে পৌঁছানো যায় না। Live Messenger চালাতে public HTTPS URL লাগবে।

## Facebook Page যোগ ও চালু/বন্ধ
1. Dashboard → **Facebook Pages** খুলুন।
2. Page ID, Page Name, Page Access Token এবং delivery charge দিন; Enabled checkbox চালু রেখে save করুন।
3. Page বন্ধ করতে ওই Page-এর **Page বন্ধ করুন** বাটনে চাপুন। আবার চালু করতে **Page চালু করুন** চাপুন।
4. নতুন Page-এর token অবশ্যই দিতে হবে। Existing Page update করার সময় token field ফাঁকা রাখলে আগের token অপরিবর্তিত থাকবে।
5. Page চালু করার আগে token, webhook subscription এবং permissions ঠিক আছে নিশ্চিত করুন।

Meta for Developers-এ Messenger webhook callback URL হিসেবে `https://YOUR-DOMAIN/webhook` দিন। Verify Token `.env`-এর `META_VERIFY_TOKEN`-এর সঙ্গে হুবহু মিলতে হবে। `META_APP_SECRET` সেট করা আবশ্যক, কারণ webhook signature validate করা হয়। Meta App/Page permissions ও app review Meta-এর নিয়ম অনুযায়ী সম্পন্ন করতে হবে।

## Products ও orders
- Dashboard → Products → Add Product.
- পণ্যের image URL public HTTPS direct image URL হওয়া উচিত। এই সংস্করণে image file সরাসরি server-এ upload করার feature নেই।
- Page IDs ফিল্ডে `*` দিলে সব configured Page-এ product দেখা যাবে; নির্দিষ্ট Page ID comma দিয়ে আলাদা করুন।
- নমুনা MUSTARD-101 পণ্যের stock 0 এবং image URL ফাঁকা; real orders নেওয়ার আগে সত্যিকারের দাম, stock, ছবি ও details দিন।
- Bot explicit customer confirmation না পাওয়া পর্যন্ত order save করা উচিত নয়; live traffic চালুর আগে test করুন।

## Hosting: GitHub Pages কি যথেষ্ট?
**না। GitHub Pages static HTML/CSS/JavaScript hosting করে; Python Flask app, always-on Messenger webhook, secret tokens এবং SQLite database চালায় না।** GitHub-এ source code private repository-তে রাখতে পারেন, কিন্তু app চালানোর জন্য আলাদা backend hosting লাগবে। GitHub Actions-ও 24/7 web server-এর বিকল্প নয়।

### Option A — Render Web Service + Persistent Disk
- GitHub repository connect করে Python app deploy করা যায়।
- এই project-এ `render.yaml` example দেওয়া আছে। Blueprint-এ disk mount `/var/data`, `DATABASE_PATH=/var/data/sales_bot.db` এবং `PAGES_CONFIG_PATH=/var/data/pages.json` ব্যবহার করা হয়েছে।
- Render dashboard-এ `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `FLASK_SECRET_KEY`, `META_VERIFY_TOKEN`, `META_APP_SECRET`, `OPENAI_API_KEY` environment variables configure করুন।
- Persistent disk সাধারণত paid plan/paid resource প্রয়োজন করে; plan ও price deploy করার সময় Render-এর current pricing যাচাই করুন। Free service sleep বা ephemeral filesystem হতে পারে—SQLite/Page config হারানোর ঝুঁকি এড়াতে persistent storage নিশ্চিত করুন।

### Option B — VPS (Ubuntu)
- একটি VPS, domain, HTTPS certificate (যেমন Nginx + Certbot), Python/Gunicorn এবং persistent disk লাগবে।
- Bot এবং Dashboard 24/7 চালানো যায়; backup, security updates, firewall ও monitoring নিজেরা maintain করতে হবে।
- একাধিক worker/বেশি traffic হলে SQLite-এর বদলে PostgreSQL এবং shared session storage ব্যবহার করা ভালো।

### Option C — GitHub Pages + আলাদা backend
GitHub Pages-এ শুধু marketing/static front-end রাখা সম্ভব। Admin API, login, webhook এবং database অবশ্যই Render/VPS/অন্য backend service-এ চলবে। Admin Dashboard-এর secure API access configure না করে database বা secrets browser-এ প্রকাশ করবেন না।

## Render deploy (high-level)
1. Project GitHub-এ push করুন; `.env`, `pages.json`, `data/sales_bot.db`, secrets এবং token কখনো commit করবেন না।
2. Render-এ New → Blueprint deploy করে `render.yaml` নির্বাচন করুন অথবা Web Service তৈরি করুন।
3. Environment variables-এ secrets দিন; initial admin account তৈরি হওয়ার আগে `ADMIN_USERNAME` ও `ADMIN_PASSWORD` অবশ্যই configure করুন।
4. Persistent Disk mount এবং দুইটি path environment variable ঠিক আছে নিশ্চিত করুন।
5. Deploy হওয়ার পর `https://YOUR-SERVICE/admin/login` খুলুন।
6. Meta Developer webhook callback URL হিসেবে `https://YOUR-SERVICE/webhook` দিন এবং Page events subscribe করুন।
7. একটি test Page, test product এবং নিজের test account দিয়ে end-to-end test করে তারপর live customer traffic চালু করুন।

## Security ও production checklist
- `.env`, `pages.json`, SQLite database এবং backups public repo-তে দেবেন না।
- Production-এ unique strong `FLASK_SECRET_KEY`, strong admin password, HTTPS এবং restricted server access ব্যবহার করুন।
- `pages.json`-এ Page Access Token সংরক্ষিত থাকে; server disk access সীমিত রাখুন এবং backup encrypt করুন।
- নিয়মিত database ও Page config backup নিন।
- Live চালুর আগে CSRF, login/role restrictions, page toggle, duplicate order, stock, customer confirmation এবং webhook signature পরীক্ষা করুন।
- SQLite small-scale start-এর জন্য; concurrent/large-scale operation-এর জন্য PostgreSQL, atomic stock reservation, persistent conversation storage, monitoring এবং rate limiting যোগ করুন।
- এই bundle একটি deployable starter, আপনার Meta App/token দিয়ে আগে থেকেই live connected নয়। Meta permissions ও external services configuration আলাদা করে করতে হবে।
