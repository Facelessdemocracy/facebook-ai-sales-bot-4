import os, logging, hmac, hashlib, json, secrets
from functools import wraps
from flask import Flask, request, jsonify, render_template, redirect, url_for, session, flash, abort
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
from flask_wtf.csrf import CSRFProtect
from page_config import get_page_config, load_pages, save_pages
from database import SQLiteStore, init_db, upsert_product, delete_product, list_orders, update_order_status, dashboard_stats, connect, list_admin_users, get_admin_user_by_username, create_admin_user, update_admin_user, set_user_last_login, count_active_admins
from bot import handle_message
from meta_api import send_text, send_image, send_typing_on

load_dotenv()
logging.basicConfig(level=logging.INFO)
log=logging.getLogger('multi-product-sales-bot')
app=Flask(__name__)
app.secret_key=os.getenv('FLASK_SECRET_KEY') or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', MAX_CONTENT_LENGTH=8*1024*1024)
csrf=CSRFProtect(app)
VERIFY_TOKEN=os.getenv('META_VERIFY_TOKEN','')
APP_SECRET=os.getenv('META_APP_SECRET','')
ADMIN_USERNAME=os.getenv('ADMIN_USERNAME','admin')
ADMIN_PASSWORD=os.getenv('ADMIN_PASSWORD','')
init_db()
# Seed the first owner account from .env once; future users are managed in the Dashboard.
if not get_admin_user_by_username(ADMIN_USERNAME) and ADMIN_PASSWORD:
    try: create_admin_user(ADMIN_USERNAME, generate_password_hash(ADMIN_PASSWORD), 'admin')
    except Exception: log.exception('Could not seed initial admin account')

@app.before_request
def protect_dashboard():
    # Dashboard routes require an active database user; webhook and health remain public.
    if request.path.startswith('/admin') and request.endpoint not in ('admin_login','admin_login_post','static'):
        uid=session.get('user_id')
        if not uid:
            return redirect(url_for('admin_login', next=request.path))
        user=None
        try: user=next((u for u in list_admin_users() if u['id']==uid),None)
        except Exception: user=None
        if not user or not user['active']:
            session.clear(); return redirect(url_for('admin_login'))
        session['username']=user['username']; session['role']=user['role']
        endpoint=request.endpoint or ''
        if user['role']=='staff' and endpoint not in {'admin_home','admin_orders','order_status','admin_logout','static'}:
            abort(403)
        if user['role']=='manager' and endpoint in {'admin_users','admin_user_create','admin_user_update'}:
            abort(403)

def verify_signature(raw_body, signature):
    if not APP_SECRET or not signature or not signature.startswith('sha256='): return False
    expected='sha256='+hmac.new(APP_SECRET.encode(),raw_body,hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected,signature)

@app.get('/')
def health(): return {'ok':True,'service':'multi-page-multi-product-ai-sales-bot','webhook':'/webhook','admin':'/admin'}

@app.get('/admin/login')
def admin_login(): return render_template('login.html')
@app.post('/admin/login')
def admin_login_post():
    username=request.form.get('username','').strip(); password=request.form.get('password','')
    user=get_admin_user_by_username(username)
    if user and user.get('active') and check_password_hash(user['password_hash'],password):
        session.clear(); session['user_id']=user['id']; session['username']=user['username']; session['role']=user['role']
        set_user_last_login(user['id'])
        return redirect(request.args.get('next') or url_for('admin_home'))
    flash('ইউজারনেম অথবা পাসওয়ার্ড সঠিক নয়, অথবা অ্যাকাউন্ট বন্ধ।','error')
    return redirect(url_for('admin_login'))
@app.post('/admin/logout')
def admin_logout(): session.clear(); return redirect(url_for('admin_login'))

@app.get('/admin/users')
def admin_users():
    return render_template('users.html',users=list_admin_users(),current_user_id=session.get('user_id'))

@app.post('/admin/users/new')
def admin_user_create():
    username=request.form.get('username','').strip(); password=request.form.get('password',''); role=request.form.get('role','staff')
    try:
        if len(password)<10: raise ValueError('Password কমপক্ষে ১০ অক্ষরের হতে হবে।')
        create_admin_user(username,generate_password_hash(password),role)
        flash('নতুন Dashboard user তৈরি হয়েছে।','success')
    except Exception as e: flash(str(e) if 'UNIQUE' not in str(e).upper() else 'এই username আগে থেকেই আছে।','error')
    return redirect(url_for('admin_users'))

@app.post('/admin/users/<int:user_id>/update')
def admin_user_update(user_id):
    user=next((u for u in list_admin_users() if u['id']==user_id),None)
    if not user: abort(404)
    role=request.form.get('role',user['role']); active=request.form.get('active')=='on'; password=request.form.get('password','')
    if user_id==session.get('user_id') and (not active or role!='admin'):
        flash('নিজের Admin account বন্ধ বা role পরিবর্তন করা যাবে না।','error'); return redirect(url_for('admin_users'))
    if user['role']=='admin' and (not active or role!='admin') and count_active_admins()<=1:
        flash('শেষ সক্রিয় Admin account বন্ধ বা downgrade করা যাবে না।','error'); return redirect(url_for('admin_users'))
    if password and len(password)<10:
        flash('নতুন Password কমপক্ষে ১০ অক্ষরের হতে হবে।','error'); return redirect(url_for('admin_users'))
    try:
        update_admin_user(user_id,role=role,active=active,password_hash=generate_password_hash(password) if password else None)
        flash('User settings আপডেট হয়েছে।','success')
    except Exception as e: flash(str(e),'error')
    return redirect(url_for('admin_users'))

@app.get('/admin')
def admin_home():
    stats=dashboard_stats()
    recent=list_orders(limit=8)
    return render_template('dashboard.html',stats=stats,recent=recent,pages=load_pages())

@app.get('/admin/products')
def admin_products():
    products=SQLiteStore().get_products()
    q=request.args.get('q','').strip()
    if q: products=[p for p in products if q.casefold() in (p.get('name','')+' '+p.get('code','')+' '+p.get('category','')).casefold()]
    return render_template('products.html',products=products,q=q)

def parse_product_form():
    return {'code':request.form.get('code',''),'name':request.form.get('name',''),'category':request.form.get('category',''),
      'price':request.form.get('price',0),'stock':request.form.get('stock',0),'size':request.form.get('size',''),
      'mustard_grams':request.form.get('mustard_grams',''),'cover_material':request.form.get('cover_material',''),
      'colors':[x.strip() for x in request.form.get('colors','').split(',') if x.strip()], 'image_url':request.form.get('image_url',''),
      'description':request.form.get('description',''),'page_ids':[x.strip() for x in request.form.get('page_ids','*').split(',') if x.strip()] or ['*'],
      'active':request.form.get('active')=='on'}

@app.route('/admin/products/new',methods=['GET','POST'])
def product_new():
    if request.method=='POST':
        try:
            d=parse_product_form()
            if not d['name'].strip(): raise ValueError('পণ্যের নাম দিতে হবে।')
            upsert_product(d)
            flash('পণ্য যোগ করা হয়েছে।','success'); return redirect(url_for('admin_products'))
        except Exception as e: flash(str(e),'error')
    return render_template('product_form.html',p=None)

@app.route('/admin/products/<code>/edit',methods=['GET','POST'])
def product_edit(code):
    p=next((x for x in SQLiteStore().get_products() if x['code'].upper()==code.upper()),None)
    if not p: abort(404)
    if request.method=='POST':
        try:
            d=parse_product_form()
            if not d['name'].strip(): raise ValueError('পণ্যের নাম দিতে হবে।')
            upsert_product(d,old_code=code)
            flash('পণ্যের তথ্য আপডেট হয়েছে।','success'); return redirect(url_for('admin_products'))
        except Exception as e: flash(str(e),'error')
    return render_template('product_form.html',p=p)

@app.post('/admin/products/<code>/delete')
def product_delete(code):
    delete_product(code); flash('পণ্য মুছে ফেলা হয়েছে।','success'); return redirect(url_for('admin_products'))

@app.get('/admin/orders')
def admin_orders():
    status=request.args.get('status','All'); q=request.args.get('q','').strip()
    orders=list_orders(status=status,q=q)
    return render_template('orders.html',orders=orders,status=status,q=q)
@app.post('/admin/orders/<order_id>/status')
def order_status(order_id):
    try: update_order_status(order_id,request.form.get('status','Pending')); flash('অর্ডারের স্ট্যাটাস আপডেট হয়েছে।','success')
    except Exception as e: flash(str(e),'error')
    return redirect(request.referrer or url_for('admin_orders'))

@app.route('/admin/pages',methods=['GET','POST'])
def admin_pages():
    pages=load_pages()
    if request.method=='POST':
        page_id=request.form.get('page_id','').strip()
        page_name=request.form.get('page_name','').strip()
        token=request.form.get('page_access_token','').strip()
        if not page_id or not page_name or (not token and page_id not in pages):
            flash('Page ID, Page Name এবং নতুন Page-এর Access Token দিতে হবে।','error')
        else:
            item=pages.get(page_id,{})
            item.update({'page_id':page_id,'page_name':page_name,'enabled':request.form.get('enabled')=='on',
              'page_access_token':token or item.get('page_access_token',''),
              'delivery_single':int(request.form.get('delivery_single') or 120),
              'delivery_multiple':int(request.form.get('delivery_multiple') or 160),
              'admin_telegram_bot_token':request.form.get('admin_telegram_bot_token','').strip(),
              'admin_telegram_chat_id':request.form.get('admin_telegram_chat_id','').strip()})
            pages[page_id]=item; save_pages(pages)
            flash('Facebook Page config সংরক্ষণ হয়েছে। Token খালি রাখলে আগের token থাকবে।','success')
            return redirect(url_for('admin_pages'))
    return render_template('pages.html',pages=list(pages.values()))

@app.post('/admin/pages/<page_id>/toggle')
def admin_page_toggle(page_id):
    pages=load_pages(); page=pages.get(page_id)
    if not page: abort(404)
    page['enabled']=not bool(page.get('enabled',True))
    save_pages(pages)
    flash(('Page চালু করা হয়েছে।' if page['enabled'] else 'Page বন্ধ করা হয়েছে।'), 'success')
    return redirect(url_for('admin_pages'))

@app.get('/webhook')
def verify_webhook():
    mode=request.args.get('hub.mode'); token=request.args.get('hub.verify_token'); challenge=request.args.get('hub.challenge')
    if mode=='subscribe' and token and token==VERIFY_TOKEN: return challenge or '',200
    return 'Webhook verification failed',403

@app.post('/webhook')
@csrf.exempt
def receive_webhook():
    raw_body=request.get_data(cache=True)
    if not verify_signature(raw_body,request.headers.get('X-Hub-Signature-256','')): return jsonify({'error':'invalid webhook signature'}),403
    payload=request.get_json(silent=True) or {}
    if payload.get('object')!='page': return jsonify({'ok':True}),200
    for entry in payload.get('entry',[]):
        page_id=str(entry.get('id','')); config=get_page_config(page_id)
        if not config or not config.get('enabled',True) or not config.get('page_access_token'): continue
        token=config['page_access_token']; store=SQLiteStore(config)
        for event in entry.get('messaging',[]):
            sender=str((event.get('sender') or {}).get('id','')); message=event.get('message') or {}
            if not sender or message.get('is_echo'): continue
            text=(message.get('text') or '').strip()
            if not text: send_text(sender,'আপনার প্রশ্নটি লিখে পাঠান—আমি সাহায্য করছি।',token); continue
            try:
                send_typing_on(sender,token)
                result=handle_message(psid=sender,page_id=page_id,page_config=config,text=text,store=store)
                for image_url in result.get('images',[])[:5]: send_image(sender,image_url,token)
                if result.get('reply'): send_text(sender,result['reply'],token)
                if result.get('admin_alert'): notify_admin(config,result['admin_alert'])
            except Exception:
                log.exception('Message processing failed for page %s',page_id)
                send_text(sender,'দুঃখিত, এই মুহূর্তে উত্তর দিতে সমস্যা হচ্ছে। বিষয়টি আমাদের প্রতিনিধির কাছে পাঠানোর ব্যবস্থা করছি।',token)
    return jsonify({'ok':True}),200

def notify_admin(config,message):
    bot_token=config.get('admin_telegram_bot_token',''); chat_id=config.get('admin_telegram_chat_id','')
    if not bot_token or not chat_id: log.warning('Admin handoff requested but no Telegram configured: %s',message); return
    import requests
    try: requests.post(f'https://api.telegram.org/bot{bot_token}/sendMessage',json={'chat_id':chat_id,'text':message},timeout=10).raise_for_status()
    except Exception: log.exception('Admin notification failed')

if __name__=='__main__': app.run(host='0.0.0.0',port=int(os.getenv('PORT','8000')),debug=False)
