import os
import re
import uuid
import json
import logging
from datetime import datetime, timezone
from openai import OpenAI

from product_catalog import get_products, find_product_by_code, product_context

log = logging.getLogger("sales-bot")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY")) if os.getenv("OPENAI_API_KEY") else None
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# Demo memory only. Replace with Redis/PostgreSQL for persistent sessions and multi-worker hosting.
SESSIONS = {}

def utc_now():
    return datetime.now(timezone.utc).isoformat()

def normalize(text):
    return (text or "").strip().casefold()

def find_code(text, products):
    upper = (text or "").upper()
    for p in products:
        code = str(p.get("code", "")).upper()
        if code and code in upper:
            return p
    return None

def make_system_prompt(products, page_config, state):
    shop_name = page_config.get("page_name", "আমাদের দোকান")
    return f"""
তুমি {shop_name}-এর বাংলা Facebook Messenger sales assistant.
তোমার লক্ষ্য: কাস্টমারের প্রশ্নের সত্য, পরিষ্কার উত্তর দেওয়া; পণ্য খুঁজে দিতে সাহায্য করা; ইচ্ছা হলে COD অর্ডার বুকিং সম্পন্ন করা।
বর্তমান পণ্যের ক্যাটালগ (এটাই সত্যের একমাত্র উৎস):
{product_context(products)}
বর্তমান অর্ডার ধাপ:
{json.dumps(state, ensure_ascii=False)}

কঠোর নিয়ম:
1. সবসময় সহজ, স্বাভাবিক, ভদ্র বাংলায় কথা বলবে। কাস্টমার ইংরেজিতে লিখলে প্রয়োজনমতো ইংরেজি ব্যবহার করতে পারো।
2. দাম, stock, color, size, image, material, delivery fee বা product code ক্যাটালগ/দোকানের কনফিগারেশনে না থাকলে বানিয়ে বলবে না।
3. কাস্টমার কোন পণ্য বোঝাচ্ছেন অস্পষ্ট হলে নাম বা code জিজ্ঞেস করবে।
4. উত্তর জানা না থাকলে বলবে: "এই তথ্যটি এখন নিশ্চিত করতে পারছি না। আমি আমাদের প্রতিনিধির কাছে বিষয়টি পাঠানোর ব্যবস্থা করছি। আপনি এখানেই আপনার প্রশ্নটি লিখে রাখতে পারেন।" তারপর admin handoff flag ব্যবহার করবে।
5. চিকিৎসা/শিশুর স্বাস্থ্য সম্পর্কে নিশ্চয়তা, রোগ সারানোর দাবি বা চিকিৎসকের বিকল্প হিসেবে পণ্য উপস্থাপন করবে না।
6. গ্রাহকের পাসওয়ার্ড, OTP, card number বা অপ্রয়োজনীয় sensitive data চাইবে না।
7. অর্ডার নেওয়ার সময় product code, variant/color, quantity, customer name, phone, full delivery address সংগ্রহ করতে হবে।
8. কাস্টমারকে final summary (unit price, quantity, delivery, total, COD) দেখিয়ে স্পষ্টভাবে "কনফার্ম" লিখতে বলবে। কনফার্ম না হওয়া পর্যন্ত order save করা যাবে না।
9. কাস্টমার অর্ডার বাতিল বললে draft বাতিল করবে।
10. কোনো discount/free delivery/guarantee নিজে থেকে দেবে না।
11. "ইনবক্স করুন" বলে কথোপকথন বন্ধ করবে না—কাস্টমার ইতিমধ্যেই Messenger-এ আছেন। বরং এখানেই সাহায্য করবে বা প্রতিনিধি handoff করবে।
12. ক্যাটালগের সব পণ্য থেকে পছন্দ করতে চাইলে code, নাম, দাম ও available image দেখানোর জন্য বলবে।
শুধু reply text তৈরি করবে; নিজে থেকে অর্ডার save করার দাবি করবে না।
"""

def ai_answer(text, products, page_config, state):
    if not client:
        return (
            "আমি পণ্যের তথ্য ও অর্ডার সম্পর্কে সাহায্য করতে পারি। "
            "কোন পণ্যের নাম বা কোড জানতে চান? বিস্তারিত নিশ্চিত না হলে আমাদের প্রতিনিধি সাহায্য করবেন।"
        ), False
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": make_system_prompt(products, page_config, state)},
                {"role": "user", "content": text}
            ],
            temperature=0.2,
            max_tokens=350
        )
        reply = response.choices[0].message.content.strip()
        # Conservative handoff trigger when answer is explicitly uncertain.
        uncertain = any(term in reply.casefold() for term in [
            "নিশ্চিত করতে পারছি না", "জানি না", "প্রতিনিধির কাছে", "তথ্যটি নেই"
        ])
        return reply, uncertain
    except Exception:
        log.exception("OpenAI reply failed")
        return (
            "দুঃখিত, এই মুহূর্তে উত্তর তৈরি করতে সমস্যা হচ্ছে। "
            "আপনার প্রশ্নটি আমাদের প্রতিনিধির কাছে পাঠানোর ব্যবস্থা করছি।"
        ), True

def order_summary(draft, delivery):
    subtotal = int(draft["unit_price"]) * int(draft["quantity"])
    total = subtotal + delivery
    draft["delivery"] = delivery
    draft["total"] = total
    return (
        "আপনার অর্ডারের সারাংশ:\n"
        f'পণ্য: {draft["product_name"]}\n'
        f'কোড: {draft["product_code"]}\n'
        f'কালার/ভ্যারিয়েন্ট: {draft.get("color") or "প্রযোজ্য নয়"}\n'
        f'পরিমাণ: {draft["quantity"]}টি\n'
        f'প্রতি পিস: ৳{draft["unit_price"]}\n'
        f'পণ্যের মোট: ৳{subtotal}\n'
        f'ডেলিভারি চার্জ: ৳{delivery}\n'
        f'সর্বমোট: ৳{total}\n'
        f'নাম: {draft["customer_name"]}\n'
        f'ফোন: {draft["phone"]}\n'
        f'ঠিকানা: {draft["address"]}\n'
        "পেমেন্ট: Cash on Delivery (পণ্য হাতে পেয়ে পেমেন্ট)\n\n"
        'সব তথ্য ঠিক থাকলে "কনফার্ম" লিখুন। পরিবর্তন করতে "বাতিল" লিখুন।'
    )

def parse_customer_details(text):
    # Expected form: Name, phone, full address. Deliberately asks for structured input.
    parts = [x.strip() for x in re.split(r"[,،\n]", text) if x.strip()]
    compact = re.sub(r"[\s-]", "", text)
    phone_match = re.search(r"(?:\+?8801|01)\d{9}", compact)
    if len(parts) < 3 or not phone_match:
        return None
    phone = phone_match.group(0)
    name = parts[0]
    address_parts = [p for p in parts[1:] if re.sub(r"[\s-]", "", p) != phone]
    address = ", ".join(address_parts)
    if len(name) < 2 or len(address) < 5:
        return None
    return {"customer_name": name, "phone": phone, "address": address}

def handle_message(psid, page_id, page_config, text, store):
    session_key = f"{page_id}:{psid}"
    state = SESSIONS.setdefault(session_key, {"stage": "chat", "draft": {}, "last_activity": utc_now()})
    state["last_activity"] = utc_now()
    lower = normalize(text)
    products = get_products(store, page_id)
    images = []
    admin_alert = None

    if lower in ("মানুষ", "এজেন্ট", "প্রতিনিধি", "human", "agent", "support"):
        admin_alert = f'Human handoff requested. Page={page_config.get("page_name", page_id)} PSID={psid} Message={text}'
        return {"reply": "অবশ্যই, আমি আপনার কথাটি আমাদের প্রতিনিধির কাছে পাঠানোর ব্যবস্থা করছি। অনুগ্রহ করে আপনার প্রশ্নটি লিখে দিন।", "admin_alert": admin_alert}

    if state["stage"] == "confirm":
        if lower in ("বাতিল", "cancel", "না", "অর্ডার বাতিল"):
            state["stage"] = "chat"
            state["draft"] = {}
            return {"reply": "ঠিক আছে, অর্ডারটি বুক করা হয়নি। অন্য কোনো পণ্য সম্পর্কে জানতে চাইলে বলুন।"}
        if lower in ("কনফার্ম", "confirm", "অর্ডার কনফার্ম", "হ্যাঁ কনফার্ম", "জি কনফার্ম"):
            draft = state["draft"]
            key = f'{page_id}:{psid}:{draft["product_code"]}:{draft["phone"]}:{draft["quantity"]}:{draft.get("color","")}'
            existing = store.find_order_by_idempotency_key(key)
            if existing:
                state["stage"] = "chat"
                state["draft"] = {}
                return {"reply": f'এই অর্ডারটি আগে থেকেই রেকর্ড করা আছে। অর্ডার আইডি: {existing["order_id"]}।'}
            order_id = "ORD-" + uuid.uuid4().hex[:10].upper()
            order = {
                "order_id": order_id, "created_at": utc_now(), "page_id": page_id, "psid": psid,
                "product_code": draft["product_code"], "product_name": draft["product_name"],
                "unit_price": draft["unit_price"], "quantity": draft["quantity"],
                "color": draft.get("color", ""), "customer_name": draft["customer_name"],
                "phone": draft["phone"], "address": draft["address"],
                "delivery": draft["delivery"], "total": draft["total"],
                "payment_method": "Cash on Delivery", "status": "Confirmed",
                "idempotency_key": key
            }
            store.save_order(order)
            state["stage"] = "chat"
            state["draft"] = {}
            return {"reply": f'ধন্যবাদ! আপনার অর্ডার নিশ্চিত হয়েছে।\nঅর্ডার আইডি: {order_id}\nপেমেন্ট: Cash on Delivery\nডেলিভারির আগে প্রয়োজনে প্রতিনিধি যোগাযোগ করবেন।'}

        return {"reply": 'অর্ডার নিশ্চিত করতে "কনফার্ম" লিখুন, অথবা "বাতিল" লিখুন।'}

    if state["stage"] == "collect_details":
        details = parse_customer_details(text)
        if not details:
            return {
                "reply": "অনুগ্রহ করে এভাবে তথ্য দিন:\nআপনার নাম, 01XXXXXXXXX, সম্পূর্ণ বাড়ি/এলাকা/জেলা ঠিকানা\nফোন নম্বর ও ঠিকানা শুধু অর্ডার ডেলিভারির জন্য ব্যবহার করা হবে।"
            }
        state["draft"].update(details)
        state["stage"] = "confirm"
        qty = int(state["draft"]["quantity"])
        # Default shipping from the current shop policy; configurable per Page.
        delivery = int(page_config.get("delivery_single", 120) if qty == 1 else page_config.get("delivery_multiple", 160))
        return {"reply": order_summary(state["draft"], delivery)}

    if state["stage"] == "choose_variant":
        draft = state["draft"]
        product = next((p for p in products if p["code"] == draft.get("product_code")), None)
        if not product:
            state["stage"] = "chat"
            state["draft"] = {}
            return {"reply": "পণ্যটি এখন ক্যাটালগে পাওয়া যাচ্ছে না। আবার পণ্য কোডটি দিন।"}
        colors = product.get("colors", [])
        selected_color = next((c for c in colors if c.casefold() in lower), None)
        quantity_match = re.search(r"\b(\d+)\b", text)
        if selected_color:
            draft["color"] = selected_color
        if quantity_match:
            draft["quantity"] = max(1, min(int(quantity_match.group(1)), 20))
        if colors and not draft.get("color"):
            return {"reply": "কোন কালারটি চান? উপলভ্য কালার: " + ", ".join(colors)}
        if draft["quantity"] > int(product.get("stock", 0)):
            return {"reply": f'দুঃখিত, বর্তমানে স্টক {product.get("stock", 0)}টি। অনুগ্রহ করে কম পরিমাণ নির্বাচন করুন।'}
        state["stage"] = "collect_details"
        return {"reply": "ধন্যবাদ। এখন আপনার নাম, মোবাইল নম্বর ও সম্পূর্ণ ঠিকানা একসঙ্গে লিখুন।\nউদাহরণ: রহিম, 01XXXXXXXXX, বাড়ি/রোড/এলাকা/জেলা"}

    product = find_code(text, products)
    catalog_words = ["সব পণ্য", "পণ্য দেখাও", "প্রোডাক্ট", "ক্যাটালগ", "ছবি দেখাও", "কি কি আছে", "কি কি পণ্য", "পণ্যগুলো"]
    if any(w in lower for w in catalog_words):
        if not products:
            return {"reply": "দুঃখিত, এখন পণ্যের ক্যাটালগ পাওয়া যাচ্ছে না। আমাদের প্রতিনিধি সাহায্য করবেন.", "admin_alert": f"Catalog unavailable: page={page_id}"}
        lines = ["আমাদের উপলভ্য পণ্যগুলো:"]
        for p in products[:20]:
            lines.append(f'{p["name"]} | কোড: {p["code"]} | দাম: ৳{p["price"]} | স্টক: {p["stock"]}টি')
            if p.get("image_url"):
                images.append(p["image_url"])
        lines.append("\nযে পণ্যটি চান তার কোড লিখুন, যেমন: অর্ডার MUSTARD-101")
        return {"reply": "\n".join(lines), "images": images}

    if lower.startswith("অর্ডার") or lower.startswith("order") or product:
        if not product:
            product = find_code(text, products)
        if not product:
            reply, uncertain = ai_answer(text, products, page_config, state)
            if uncertain:
                admin_alert = f'Unknown product/question. Page={page_config.get("page_name", page_id)} PSID={psid} Message={text}'
            return {"reply": reply, "admin_alert": admin_alert}

        if int(product.get("price", 0)) <= 0 or int(product.get("stock", 0)) <= 0:
            return {
                "reply": f'দুঃখিত, {product["name"]} (কোড: {product["code"]})-এর দাম বা স্টক এখনো নিশ্চিত করা হয়নি। ভুল অর্ডার না নিতে আমাদের প্রতিনিধি স্টক যাচাই করবেন।',
                "admin_alert": f'Order inquiry needs price/stock verification: {product["code"]}, page={page_id}, psid={psid}'
            }
        state["draft"] = {
            "product_code": product["code"], "product_name": product["name"],
            "unit_price": int(product["price"]), "quantity": 1, "color": "",
        }
        if product.get("colors"):
            state["stage"] = "choose_variant"
            return {
                "reply": f'{product["name"]}\nকোড: {product["code"]}\nদাম: ৳{product["price"]}\nস্টক: {product["stock"]}টি\nউপলভ্য কালার: {", ".join(product["colors"])}\n\nকোন কালার ও কতটি চান লিখুন।',
                "images": [product["image_url"]] if product.get("image_url") else []
            }
        state["stage"] = "choose_variant"
        return {
            "reply": f'{product["name"]}\nকোড: {product["code"]}\nদাম: ৳{product["price"]}\nস্টক: {product["stock"]}টি\nকতটি নিতে চান লিখুন।',
            "images": [product["image_url"]] if product.get("image_url") else []
        }

    # Natural-language questions about product facts go to AI with catalog grounding.
    reply, uncertain = ai_answer(text, products, page_config, state)
    if uncertain:
        admin_alert = f'AI needs human review. Page={page_config.get("page_name", page_id)} PSID={psid} Message={text}'
    return {"reply": reply, "admin_alert": admin_alert}
