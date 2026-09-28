import os, httpx

BOT_TOKEN=os.getenv("TELEGRAM_BOT_TOKEN","").strip()
BACKEND_URL=os.getenv("BACKEND_URL","http://localhost:8000").rstrip("/")
API_SECRET=os.getenv("API_SECRET","").strip()

async def create_stars_invoice(chat_id,title,description,payload,stars):
    url=f"https://api.telegram.org/bot{BOT_TOKEN}/sendInvoice"
    body={"chat_id":chat_id,"title":title[:32],"description":description[:255],
          "payload":payload,"provider_token":"","currency":"XTR",
          "prices":[{"label":title[:32],"amount":stars}]}
    async with httpx.AsyncClient(timeout=15) as c:
        r=await c.post(url,json=body); r.raise_for_status(); d=r.json()
        if not d.get("ok"): raise RuntimeError(d)
        return d["result"]

async def answer_pre_checkout(query_id,ok=True,error_message=None):
    url=f"https://api.telegram.org/bot{BOT_TOKEN}/answerPreCheckoutQuery"
    body={"pre_checkout_query_id":query_id,"ok":ok}
    if not ok: body["error_message"]=error_message or "Paiement impossible."
    async with httpx.AsyncClient(timeout=10) as c:
        r=await c.post(url,json=body); r.raise_for_status(); return r.json()

async def mark_successful_payment(payment_id,charge_id,stars,payload):
    url=f"{BACKEND_URL}/payments/{payment_id}/mark-paid"
    headers={"X-API-Secret":API_SECRET} if API_SECRET else {}
    params={"telegram_charge_id":charge_id,"stars":stars,"payload":payload}
    async with httpx.AsyncClient(timeout=15) as c:
        r=await c.post(url,params=params,headers=headers); r.raise_for_status(); return r.json()
