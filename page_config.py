import json, os
CONFIG_PATH=os.getenv('PAGES_CONFIG_PATH',os.path.join(os.path.dirname(__file__),'pages.json'))
def load_pages():
    if not os.path.exists(CONFIG_PATH): return {}
    with open(CONFIG_PATH,encoding='utf-8') as f: raw=json.load(f)
    pages={}
    for item in raw.get('pages',[]):
        page_id=str(item.get('page_id','')).strip(); token=str(item.get('page_access_token','')).strip()
        if page_id and item.get('page_name') and 'PUT_' not in page_id: pages[page_id]=item
    return pages
def save_pages(pages):
    os.makedirs(os.path.dirname(CONFIG_PATH) or '.',exist_ok=True)
    tmp=CONFIG_PATH+'.tmp'
    with open(tmp,'w',encoding='utf-8') as f: json.dump({'pages':list(pages.values())},f,ensure_ascii=False,indent=2)
    os.replace(tmp,CONFIG_PATH)
def get_page_config(page_id): return load_pages().get(str(page_id))
