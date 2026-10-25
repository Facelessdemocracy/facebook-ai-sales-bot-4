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
@csrf.exempt  # Temporary workaround for the reported missing CSRF token on login
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
