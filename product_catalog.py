import json

def get_products(store, page_id):
    visible=[]
    for p in store.get_products():
        if not p.get('active',True): continue
        allowed_pages=p.get('page_ids',['*'])
        if '*' not in allowed_pages and str(page_id) not in [str(x) for x in allowed_pages]: continue
        visible.append(p)
    return visible

def find_product_by_code(products, code):
    if not code: return None
    code=str(code).strip().upper()
    return next((p for p in products if str(p.get('code','')).upper()==code),None)

def product_context(products): return json.dumps(products,ensure_ascii=False,indent=2)
