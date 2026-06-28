import os
import json
import time
import datetime
import yfinance as yf
import mplfinance as mpf
from google import genai
from google.genai import types
from playwright.sync_api import sync_playwright

# ==================== 0. 智慧型台股開盤日檢查機制 ====================
def is_taiwan_market_open():
    """
    檢查今天台灣股市是否有開盤。
    1. 先判斷今天是不是週六或週日。
    2. 再透過 yfinance 抓取加權指數 (^TWII) 的最新交易日，判斷今天是不是國定假日/颱風假。
    """
    today = datetime.date.today()
    
    # 檢查週六(5)與週日(6)
    if today.weekday() in [5, 6]:
        print(f"🛑 【休市通知】今天是星期 {['一','二','三','四','五','六','日'][today.weekday()]}，屬於週末假期，系統自動休兵。")
        return False
        
    print("🔍 正在連線市場確認今天是否為國定假日或特殊休市日...")
    try:
        twii = yf.Ticker("^TWII")
        hist = twii.history(period="1d")
        
        if hist.empty:
            print("⚠️ 無法取得大盤數據，預設今日正常開盤。")
            return True
            
        last_market_date = hist.index[0].date()
        
        if last_market_date != today:
            print(f"🛑 【休市通知】經查今日為台股市場休市日（最新交易日為 {last_market_date}），系統自動進入休假模式。")
            return False
            
        print("📈 【開盤確認】經查今日台灣股市正常開盤交易！")
        return True
    except Exception as e:
        print(f"⚠️ 檢查休市日發生異常 ({e})，為防漏單，預設今日正常開盤. ")
        return True

# ==================== 1. 初始化與核心工具設定（K線 + 抓新聞） ====================
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
gemini_client = genai.Client(api_key=GEMINI_KEY) if GEMINI_KEY else None

class StockTools:
    def __init__(self):
        self.kline_count = 0
        self.news_count = 0

    def get_stock_kline_chart(self, stock_code: str) -> str:
        """
        輸入台灣股票四碼代碼（例如 '2330'），自動下載過去30天的股價，
        生成一張包含5MA、20MA與成交量 K 線圖。AI 每天最多能呼叫 3 次。
        """
        self.kline_count += 1
        if self.kline_count > 3:
            return "【系統提示】你今天查看 K 線圖的次數已達上限，請勿再呼叫此工具。"
        
        print(f"⏳ 正在調用 K 線圖生成工具 [代碼: {stock_code}]，進入防護延遲...")
        time.sleep(3) 
        
        try:
            code = str(stock_code).strip().replace(".TW", "").replace(".TWO", "")
            ticker_sym = f"{code}.TW"
            stock = yf.Ticker(ticker_sym)
            df = stock.history(period="30d")
            
            if df.empty:
                ticker_sym = f"{code}.TWO"
                stock = yf.Ticker(ticker_sym)
                df = stock.history(period="30d")
                if df.empty:
                    return f"【系統錯誤】找不到代碼 {code} 的股票數據。"
            
            image_path = f"{code}_kline.png"
            mpf.plot(
                df, type='candle', mav=(5, 20), volume=True,
                style='charles', title=f"Stock {code} - Last 30 Days",
                savefig=image_path
            )
            return f"【系統通知】成功生成 {code} 的 K 線圖，已輸入你的視覺大腦，請據此進行技術面分析。"
        except Exception as e:
            return f"【系統錯誤】暫時無法取得該 K 圖，原因：{str(e)}"

    def get_stock_news(self, stock_code: str) -> str:
        """
        輸入台灣股票四碼代碼（例如 '2330'），自動去市場上抓取該公司最新、最熱門的 3 則財經新聞與公告。
        AI 每天最多能呼叫 3 次。
        """
        self.news_count += 1
        if self.news_count > 3:
            return "【系統提示】你今天查看即時新聞的次數已達上限，請勿再呼叫此工具。"

        print(f"📰 正在為 AI 大腦蒐集 {stock_code} 的市場即時新聞公告與市場消息面...")
        time.sleep(2)

        try:
            code = str(stock_code).strip().replace(".TW", "").replace(".TWO", "")
            ticker = yf.Ticker(f"{code}.TW")
            news_list = ticker.news
            if not news_list:
                ticker = yf.Ticker(f"{code}.TWO")
                news_list = ticker.news
                
            if not news_list:
                return f"【系統提示】目前財經網絡上沒有關於股票代碼 {code} 的最新即時重大新聞。"
                
            result = f"=== 股票代碼 {code} 最新市場新聞面與公告 ===\n"
            for i, news in enumerate(news_list[:3]):  # 精選最新 3 則避開長篇大論
                title = news.get("title", "無標題")
                publisher = news.get("publisher", "未知媒體")
                link = news.get("link", "")
                result += f"[{i+1}] {title} (來源: {publisher})\n"
            return result
        except Exception as e:
            return f"【系統錯誤】新聞消息抓取失敗: {str(e)}"

# ==================== 2. 本地模擬交易撮合引擎 ====================
DB_FILE = "portfolio.json"

def load_db():
    if not os.path.exists(DB_FILE):
        init_data = {
            "openai_bot": {"cash": 100000.0, "holdings": {}, "trade_history": [{"date": str(datetime.date.today()), "action": "HOLD", "code": "NONE", "shares": 0, "price": 0, "fee": 0, "reason": "OpenAI 隊目前處於非賽季維護狀態。"}]},
            "gemini_bot": {"cash": 100000.0, "holdings": {}, "trade_history": []},
            "last_updated": str(datetime.date.today())
        }
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(init_data, f, indent=2)
    with open(DB_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except:
            return {
                "openai_bot": {"cash": 100000.0, "holdings": {}, "trade_history": [{"date": str(datetime.date.today()), "action": "HOLD", "code": "NONE", "shares": 0, "price": 0, "fee": 0, "reason": "OpenAI 隊目前處於非賽季維護狀態。"}]},
                "gemini_bot": {"cash": 100000.0, "holdings": {}, "trade_history": []},
                "last_updated": str(datetime.date.today())
            }

def save_db(data):
    data["last_updated"] = f"{datetime.date.today()} {datetime.datetime.now().strftime('%H:%M')}"
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def log_holiday_reason(reason_text):
    db = load_db()
    if not db["gemini_bot"]["trade_history"]:
         db["gemini_bot"]["trade_history"].append({"reason": reason_text})
    else:
         db["gemini_bot"]["trade_history"][-1]["reason"] = reason_text
    save_db(db)

def execute_trades(bot_key, ai_decision, current_mode):
    db = load_db()
    bot = db[bot_key]
    trades = ai_decision.get("trades", [])
    
    for t in trades:
        code = str(t.get("code")).strip().replace(".TW", "").replace(".TWO", "")
        action = t.get("action")
        try:
            shares = int(t.get("shares", 0))
            ai_price = float(t.get("price", 0)) 
        except:
            continue
            
        if shares <= 0 or action not in ["BUY", "SELL"]:
            continue
            
        stock_data = None
        for suffix in [".TW", ".TWO"]:
            try:
                tick = yf.Ticker(f"{code}{suffix}")
                hist = tick.history(period="1d")
                if not hist.empty:
                    stock_data = hist.iloc[-1]
                    break
            except:
                continue
                
        if stock_data is None:
            print(f"⚠️ 找不到股票代碼 {code} 的即時行情，取消本地模擬記帳。")
            continue
            
        today_low = float(stock_data['Low'])
        today_high = float(stock_data['High'])
        today_close = float(stock_data['Close']) 
        
        if current_mode == "盤中戰鬥模式":
            trade_price = today_close
            is_triggered = True
            log_prefix = "⚡【本地模擬-盤中現價】"
        else:
            trade_price = ai_price if ai_price > 0 else today_close
            log_prefix = "⏳【本地模擬-限價掛單】"
            is_triggered = (today_low <= trade_price) if action == "BUY" else (today_high >= trade_price)

        amount = trade_price * shares
        fee = max(20, int(amount * 0.001425)) 
        
        if action == "BUY":
            if is_triggered:
                total_cost = amount + fee
                if bot["cash"] >= total_cost:
                    bot["cash"] -= total_cost
                    if code not in bot["holdings"]:
                        bot["holdings"][code] = {"shares": 0, "avg_cost": 0.0}
                    h = bot["holdings"][code]
                    new_shares = h["shares"] + shares
                    h["avg_cost"] = ((h["avg_cost"] * h["shares"]) + total_cost) / new_shares
                    h["shares"] = new_shares
                    bot["trade_history"].append({
                        "date": str(datetime.date.today()), "action": "BUY", "code": code, 
                        "shares": shares, "price": trade_price, "fee": fee, "reason": ai_decision.get("reason", "")
                    })
                    print(f"✅ {log_prefix} 成功買進 {code} 共 {shares} 股，成交價 ${trade_price}")
                else:
                    print(f"❌ Gemini 欲買進 {code}，但本地帳戶資金不足！")
            else:
                print(f"⏳ 【本地掛單未成交】AI 想以 ${trade_price} 低接 {code}，今日市場未達此價位。")
                bot["trade_history"].append({
                    "date": str(datetime.date.today()), "action": "HOLD", "code": code,
                    "shares": 0, "price": 0, "fee": 0, "reason": f"【預約限價未觸發】原計畫以 ${trade_price} 買入 {code}，今日最低價為 ${today_low}。" + ai_decision.get("reason", "")
                })
                
        elif action == "SELL":
            if code in bot["holdings"] and bot["holdings"][code]["shares"] >= shares:
                if is_triggered:
                    tax = int(amount * 0.003) 
                    total_revenue = amount - fee - tax
                    bot["cash"] += total_revenue
                    bot["holdings"][code]["shares"] -= shares
                    bot["trade_history"].append({
                        "date": str(datetime.date.today()), "action": "SELL", "code": code, 
                        "shares": shares, "price": trade_price, "fee": fee + tax, "reason": ai_decision.get("reason", "")
                    })
                    print(f"✅ {log_prefix} 成功賣出 {code} 共 {shares} 股！")
                    if bot["holdings"][code]["shares"] == 0:
                        del bot["holdings"][code]
                else:
                    print(f"⏳ 【本地掛單未成交】AI 想以 ${trade_price} 高拋 {code}，市場未達此價位。")
            else:
                print(f"❌ Gemini 欲賣出 {code}，但本地並未持有足夠股數！")
                
    db[bot_key] = bot
    save_db(db)

# ==================== 3. Playwright CMoney 股市大富翁網頁下單 ====================
def order_on_cmoney(action, stock_code, shares, price=0):
    CMONEY_EMAIL = os.environ.get("CMONEY_EMAIL")
    CMONEY_PWD = os.environ.get("CMONEY_PASSWORD")

    if not CMONEY_EMAIL or not CMONEY_PWD:
        print("⚠️ [CMoney 提示] 未偵測到 CMONEY_EMAIL 與 CMONEY_PASSWORD，跳過實體網頁自動化下單。")
        return

    print(f"🤖 [Playwright] 隱形瀏覽器已在雲端初始化，準備發送委託 [{action} {stock_code} {shares}股]...")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True) 
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        
        try:
            print("🌐 正在連線至 CMoney 大富翁並等待跳轉認證頁...")
            page.goto("https://www.cmoney.tw/vt/main-page.aspx", timeout=60000)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(3000)
            
            current_url = page.url
            print(f"📍 目前瀏覽器所在網址: {current_url}")
            
            email_input = page.locator('input[type="email"], input[name*="username"], input[name*="mail"], #Username').first
            pwd_input = page.locator('input[type="password"], input[name*="password"], #Password').first
            
            email_input.wait_for(state="visible", timeout=15000)
            email_input.fill(CMONEY_EMAIL)
            pwd_input.fill(CMONEY_PWD)
            page.wait_for_timeout(500)
            
            submit_login = page.locator("button[type='submit'], button:has-text('登入'), .btn-primary").first
            submit_login.click()
            
            print("🚀 登入憑證已送出，正在強制導航回大富翁主交易頁...")
            page.wait_for_timeout(5000)
            page.goto("https://www.cmoney.tw/vt/main-page.aspx", timeout=60000)
            page.wait_for_load_state("networkidle")
            
            print("🔐 [CMoney 成功] 繞過轉址死鎖，雲端模擬真人登入成功！")

            search_input = page.locator('input[placeholder*="股票代號"], #txtStockCode').first
            search_input.wait_for(state="visible", timeout=10000)
            search_input.fill(str(stock_code))
            search_input.press("Enter")
            page.wait_for_timeout(4000) 
            
            if action == "BUY":
                page.locator("button:has-text('買進'), #btnBuy").first.click()
            else:
                page.locator("button:has-text('賣出'), #btnSell").first.click()
            page.wait_for_timeout(1500)

            if shares < 1000:
                odd_btn = page.locator("text='零股', #radOdd").first
                if odd_btn.is_visible():
                    odd_btn.click()
                    page.wait_for_timeout(1000)
            
            qty_input = page.locator('input[id*="Qty"], #txtQuantity').first
            qty_input.fill(str(shares))
            
            if price > 0:
                price_input = page.locator('input[id*="Price"], #txtPrice').first
                price_input.fill(str(price))
                
            page.wait_for_timeout(1500)
            
            submit_btn = page.locator("button:has-text('下單'), button:has-text('送出'), #btnSubmitOrder").first
            submit_btn.click()
            page.wait_for_timeout(4000)
            
            print(f"🎉 [CMoney 成功] 雲端自動化下單發送完成：{action} {stock_code} {shares} 股 (掛單限價: {price})")
            
        except Exception as e:
            print(f"💥 [CMoney 失敗] 雲端自動化流程中斷，原因：{str(e)}")
            
        browser.close()

# ==================== 4. 動態時段提示詞系統（加強消息面指引） ====================
def get_dynamic_prompt(current_mode, current_time_str):
    return f"""
你是擁有完全自主權的台股頂級量化基金操盤手。你現在有 10 萬元初始資金，支援零股交易。
🔔 【時段狀態感應】：現在是台北時間 {current_time_str}，系統正處於【{current_mode}】。

你的當前核心觀察名單為台股焦點股：【2330 (台積電), 2317 (鴻海), 2454 (聯發科)】。

🌟【操盤特權指引】：
1. 你擁有調閱即時新聞的權限。在做決定前，請務必先針對你想了解的股票呼叫「get_stock_news」工具，閱讀今天的最新財經新聞、公告與市場消息！
2. 閱讀完新聞後，如果想進一步分析技術面，請呼叫「get_stock_kline_chart」工具來查看該個股 30 天 K 線圖。
3. 如果此時是【盤前部署模式】（開盤前半小時）：請結合你剛看到的新聞利多或利空，推估合理的低接或高拋價格，進行「限價預約掛單」（trades 內需填寫理想的 price 價格）。
4. 自主權利：若新聞顯示大盤不穩或個股無明顯動能，你完全可以選擇冷靜觀望（trades 陣列保持為空 []）。

⚠️ 嚴格規格：
你的最終回應必須「完全符合」以下 JSON 格式，不要附帶任何額外的 Markdown 說明：
{{
  "reason": "【時段決策: {current_mode}】請詳細說明：你看了哪檔股票的新聞消息？發現了什麼利多/利空？並結合 K 線圖，給出你決定盤前限價掛單或觀望的宏觀與微觀理由。",
  "trades": [
    {{
      "code": "四碼台灣股票代碼", 
      "action": "BUY 或 SELL", 
      "shares": 股數, 
      "price": 你的理想限價
    }}
  ]
}}
"""

def ask_gemini(tools_object, current_mode, current_time_str):
    try:
        if not gemini_client:
            raise Exception("未設定 GEMINI_API_KEY 金鑰")
            
        print(f"⏳ [時段切換] 偵測到當前為【{current_mode}】，正在喚醒 Gemini 2.5 雙料分析核心...")
        time.sleep(3) 
        
        # 同時配備【K線工具】與【新聞工具】
        shared_tools = [tools_object.get_stock_kline_chart, tools_object.get_stock_news]
        prompt = get_dynamic_prompt(current_mode, current_time_str)
        
        response = gemini_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(tools=shared_tools)
        )
        
        clean_text = response.text.strip()
        if clean_text.startswith("```"):
            clean_text = clean_text.split("\n", 1)[1]
        if clean_text.endswith("```"):
            clean_text = clean_text.rsplit("\n", 1)[0]
        clean_text = clean_text.strip("`").strip()
            
        return json.loads(clean_text)
    except Exception as e:
        print(f"💥 Gemini 執行受限 ({e})，自動啟動防護模擬大腦...")
        return {"reason": "系統防護大腦啟動，今日暫不盲目出手。", "trades": []}

# ==================== 5. 網頁 HTML 生成與儀表板 ====================
def generate_html_dashboard():
    db = load_db()
    
    html_content = """
    <!DOCTYPE html>
    <html lang="zh-TW">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>🤖 雙模智慧全自主炒股直播 📈</title>
        <script src="https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4"></script>
    </head>
    <body class="bg-gray-900 text-gray-100 min-h-screen p-4 md:p-8 font-sans">
        <div class="max-w-6xl mx-auto">
            <header class="text-center my-6">
                <h1 class="text-3xl md:text-5xl font-extrabold text-transparent bg-clip-text bg-gradient-to-r from-teal-400 via-blue-500 to-purple-600 mb-2">🤖 全天候動態時段操盤系統 📈</h1>
                <p class="text-gray-400 text-sm md:text-base">開盤前半小時自動調閱【即時新聞消息】與【技術K線圖】雙料自主選股佈局</p>
                <div class="inline-block bg-gray-800 text-gray-400 px-4 py-1.5 rounded-full text-xs md:text-sm mt-3 border border-gray-700">
                    🕒 網頁最後更新時間：""" + db["last_updated"] + """
                </div>
            </header>
            
            <div class="grid grid-cols-1 md:grid-cols-2 gap-8 mt-6">
    """

    for bot_key, name, color, bg_gradient in [
        ("openai_bot", "OpenAI (ChatGPT-4o) 隊", "text-emerald-400", "from-gray-700 to-gray-800"), 
        ("gemini_bot", "Google (Gemini-2.5) 隊", "text-blue-400", "from-indigo-600 to-purple-800")
    ]:
        bot = db[bot_key]
        table_rows = ""
        total_stock_value = 0
        
        for code, info in list(bot["holdings"].items()):
            shares = info.get("shares", 0)
            avg_cost = info.get("avg_cost", 0)
            if shares <= 0: 
                continue
                
            price = None
            for suffix in [".TW", ".TWO"]:
                try:
                    price = yf.Ticker(f"{code}{suffix}").history(period="1d")['Close'].iloc[-1]
                    break
                except:
                    continue
            if price is None:
                price = avg_cost
                
            val = price * shares
            total_stock_value += val
            profit = val - (avg_cost * shares)
            roi = (profit / (avg_cost * shares)) * 100 if avg_cost > 0 else 0
            
            color_class = "text-red-500" if profit >= 0 else "text-green-500"
            
            table_rows += f"""
            <tr class="border-b border-gray-700 hover:bg-gray-700/40 transition">
                <td class="px-4 py-3 font-mono font-bold text-gray-200">{code}</td>
                <td class="px-4 py-3 text-right">{shares:,} 股</td>
                <td class="px-4 py-3 text-right">${avg_cost:,.1f}</td>
                <td class="px-4 py-3 text-right">${price:,.1f}</td>
                <td class="px-4 py-3 text-right {color_class} font-bold">{profit:+,.0f} 元</td>
                <td class="px-4 py-3 text-right {color_class} font-bold">{roi:+.2f}%</td>
            </tr>
            """
        
        assets = bot["cash"] + total_stock_value
        total_roi = ((assets - 100000) / 100000) * 100
        roi_color = "text-red-500 font-extrabold" if total_roi >= 0 else "text-green-500 font-extrabold"
        
        last_reason = "今日暫無新的交易決策理由。"
        if bot["trade_history"]:
            last_reason = bot["trade_history"][-1].get("reason", "未提供具體理由。")

        html_content += f"""
        <div class="bg-gray-800 rounded-2xl shadow-2xl border border-gray-700 overflow-hidden flex flex-col justify-between">
            <div>
                <div class="bg-gradient-to-r {bg_gradient} p-4 shadow-inner">
                    <h2 class="text-xl font-black text-white flex justify-between items-center">
                        <span>{name}</span>
                        <span class="text-xs bg-black/40 px-3 py-1 rounded-full border border-white/10">初始資金 $100,000</span>
                    </h2>
                </div>
                <div class="p-6">
                    <div class="grid grid-cols-3 gap-3 text-center mb-6">
                        <div class="bg-gray-900/60 p-3 rounded-xl border border-gray-700/50"><p class="text-xxs text-gray-400 mb-1">總資產價值</p><p class="text-base md:text-lg font-black {roi_color}">${assets:,.0f}</p></div>
                        <div class="bg-gray-900/60 p-3 rounded-xl border border-gray-700/50"><p class="text-base md:text-lg font-bold text-yellow-500">${bot['cash']:,.0f}</p></div>
                        <div class="bg-gray-900/60 p-3 rounded-xl border border-gray-700/50"><p class="text-xxs text-gray-400 mb-1">總累積投報</p><p class="text-base md:text-lg font-black {roi_color}">{total_roi:+.2f}%</p></div>
                    </div>
                    
                    <div class="bg-gray-900/40 p-4 rounded-xl border border-gray-700/30 mb-6">
                        <h4 class="text-xs font-bold text-yellow-400 uppercase tracking-wider mb-1">🧠 當前消息面與操盤思路</h4>
                        <p class="text-xs text-gray-300 leading-relaxed italic">「{last_reason}」</p>
                    </div>

                    <h3 class="text-xs font-black text-gray-400 uppercase tracking-wider mb-2 flex items-center">📋 當前持股庫存</h3>
                    <div class="overflow-x-auto rounded-xl border border-gray-700">
                        <table class="w-full text-left text-xs text-gray-300">
                            <thead class="bg-gray-900 text-gray-400 font-bold border-b border-gray-700">
                                <tr>
                                    <th class="px-4 py-2.5">代碼</th><th class="px-4 py-2.5 text-right">股數</th><th class="px-4 py-2.5 text-right">成本</th><th class="px-4 py-2.5 text-right">市價</th><th class="px-4 py-2.5 text-right">未實現</th><th class="px-4 py-2.5 text-right">報酬</th>
                                </tr>
                            </thead>
                            <tbody>
                                {table_rows if table_rows else '<tr><td colspan="6" class="text-center py-6 text-gray-500 font-medium">目前空倉（防禦性持有 100% 現金）</td></tr>'}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
            <div class="bg-gray-900/30 p-4 border-t border-gray-700/50 text-right">
                <span class="text-xxs text-gray-500 font-mono">歷史總交易次數: {len(bot['trade_history'])} 次</span>
            </div>
        </div>
        """
        
    html_content += """
            </div>
        </div>
    </body>
    </html>
    """
    
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html_content)
    print("✨ [網頁更新成功] 精美 index.html 直表面板已完成覆蓋！")

# ==================== 6. 引擎啟動入口 ====================
if __name__ == "__main__":
    print(f"🤖 正在啟動雙模智慧全自主網頁炒股核心引擎 (CMoney 完全體支援)...")
    
    if not is_taiwan_market_open():
        print("🏖️ 偵測到今日台股未開盤！直接進入休假模式。")
        log_holiday_reason("【今日休市】今天是週末或國定例假日，台股未開盤。AI 機器人正在休息覆盤中。")
        generate_html_dashboard()
    else:
        now_hour = datetime.datetime.now().hour
        now_minute = datetime.datetime.now().minute
        time_val = now_hour * 100 + now_minute
        current_time_str = datetime.datetime.now().strftime("%H:%M")

        # 🕒 區分開盤前（08:30~09:00）、盤中與盤後
        if 830 <= time_val < 900:
            current_mode = "盤前部署模式"
        elif 900 <= time_val <= 1330:
            current_mode = "盤中戰鬥模式"
        else:
            current_mode = "盤後覆盤模式"

        tools_manager = StockTools()
        print(f"👉 正在喚醒 Google Gemini 隊進行【{current_mode}】決策...")
        gemini_decision = ask_gemini(tools_manager, current_mode, current_time_str)
        
        execute_trades("gemini_bot", gemini_decision, current_mode)
        
        for t in gemini_decision.get("trades", []):
            order_on_cmoney(
                action=t.get("action"),
                stock_code=t.get("code"),
                shares=t.get("shares"),
                price=t.get("price", 0)
            )
        
        generate_html_dashboard()
