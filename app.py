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
