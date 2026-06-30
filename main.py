import os
import json
import time
import datetime
import pandas as pd
import yfinance as yf
import mplfinance as mpf
from google import genai
from google.genai import types
from playwright.sync_api import sync_playwright

# ==================== 0. 智慧型台股開盤日檢查機制 ====================
def is_taiwan_market_open():
    """
    檢查今天台灣股市是否有開盤。
    """
    utc_now = datetime.datetime.utcnow()
    tw_now = utc_now + datetime.timedelta(hours=8)
    today = tw_now.date()
    
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

# ==================== 1. 自動掃描市場台股量大熱門股 ====================
def get_taiwan_top_volume_tickers():
    """
    自動掃描台股市場上一些極具代表性的高成交量、高熱度個股池，
    從中篩選出最新成交量最大的幾檔，動態補充進 AI 的觀察雷達。
    """
    print("🔍 核心雷達啟動：正在動態掃描台股市場熱門量大標的...")
    candidate_pools = [
        "2330", "2317", "2454", # 核心權值
        "2303", "2408", "3481", "2409", # 聯電、南亞科、群創、友達
        "2382", "3231", "6669", "2308", # 廣達、緯創、緯穎、台達電
        "2603", "2609", "2618"          # 長榮、陽明、華航
    ]
    
    valid_list = []
    for code in candidate_pools:
        try:
            tick = yf.Ticker(f"{code}.TW")
            hist = tick.history(period="5d")
            if not hist.empty:
                last_vol = hist['Volume'].iloc[-1]
                last_close = hist['Close'].iloc[-1]
                valid_list.append({"code": code, "volume": last_vol, "close": last_close})
        except:
            continue
            
    # 依據成交量排序，取前 5 名作為今日動態熱門觀察股
    valid_list.sort(key=lambda x: x["volume"], reverse=True)
    top_5 = valid_list[:5]
    print(f"🎯 今日熱門股雷達成功鎖定標的：{[x['code'] for x in top_5]}")
    return top_5

# ==================== 2. 初始化與量化核心工具設定（KD+RSI+新聞） ====================
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
gemini_client = genai.Client(api_key=GEMINI_KEY) if GEMINI_KEY else None

class StockTools:
    def __init__(self):
        self.kline_count = 0
        self.news_count = 0

    def get_stock_kline_chart(self, stock_code: str) -> str:
        """
        輸入台灣股票四碼代碼（例如 '2330'），自動下載過去60天數據。
        除了生成圖片，還會自動計算出核心量化指標（MA、KD、RSI）直接文字餵給 AI！
        """
        self.kline_count += 1
        if self.kline_count > 5:
            return "【系統提示】你今天查看技術指標的次數已達上限。"
        
        print(f"⏳ 正在調用技術面量化指標計算工具 [代碼: {stock_code}]...")
        time.sleep(2) 
        
        try:
            code = str(stock_code).strip().replace(".TW", "").replace(".TWO", "")
            df = None
            for suffix in [".TW", ".TWO"]:
                tick = yf.Ticker(f"{code}{suffix}")
                df = tick.history(period="60d")
                if not df.empty:
                    break
            
            if df is None or df.empty:
                return f"【系統錯誤】找不到代碼 {code} 的股票數據。"
            
            # --- 量化指標高速計算機 (Pandas 實作) ---
            # 1. 均線 MA
            df['5MA'] = df['Close'].rolling(window=5).mean()
            df['20MA'] = df['Close'].rolling(window=20).mean()
            
            # 2. KD 指標 (9, 3, 3)
            low_9 = df['Low'].rolling(window=9).min()
            high_9 = df['High'].rolling(window=9).max()
            rsv = ((df['Close'] - low_9) / (high_9 - low_9)) * 100
            
            k_list, d_list = [], []
            current_k, current_d = 50.0, 50.0 # 初始值
            for r in rsv:
                if pd.isna(r):
                    k_list.append(50.0)
                    d_list.append(50.0)
                else:
                    current_k = (1/3) * r + (2/3) * current_k
                    current_d = (1/3) * current_k + (2/3) * current_d
                    k_list.append(current_k)
                    d_list.append(current_d)
            df['K'] = k_list
            df['D'] = d_list
            
            # 3. RSI 指標 (14)
            delta = df['Close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            df['RSI'] = 100 - (100 / (1 + rs))
            
            # 取得最新一天的數據指標
            latest = df.iloc[-1]
            prev = df.iloc[-2]
            
            ma_status = "多頭排列 (股價 > 5MA > 20MA)" if latest['Close'] > latest['5MA'] > latest['20MA'] else "空頭排列或震盪整理"
            kd_status = "📈 KD 黃金交叉（具備強力多頭上攻動能）" if (prev['K'] < prev['D'] and latest['K'] > latest['D']) else ("📉 KD 死亡交叉（有短期修正壓力）" if (prev['K'] > prev['D'] and latest['K'] < latest['D']) else "KD 處於既有趨勢中")
            
            # 🔥 修正後 100% 正確的三元運算子
            rsi_status = "⚠️ RSI > 75 進入極度超買過熱區" if latest['RSI'] > 75 else ("🟢 RSI < 25 進入超賣低迷低接區" if latest['RSI'] < 25 else "RSI 指標中性溫和")
            
            quant_report = f"""
=== 📊 股票代碼 {code} 頂級量化技術指標精確報告 ===
- 當前最新收盤價：${latest['Close']:.2f}
- 均線狀態：5MA=${latest['5MA']:.2f}, 20MA=${latest['20MA']:.2f} -> 【{ma_status}】
- KD 隨機指標：K值={latest['K']:.1f}, D值={latest['D']:.1f} -> 【{kd_status}】
- RSI 強弱指標 (14)：RSI值={latest['RSI']:.1f} -> 【{rsi_status}】
======================================================
"""
            # 順便存下一張實體 K 線圖備查
            image_path = f"{code}_kline.png"
            mpf.plot(df.tail(30), type='candle', mav=(5, 20), volume=True, style='charles', savefig=image_path)
            
            return quant_report
        except Exception as e:
            return f"【系統錯誤】暫時無法計算量化指標，原因：{str(e)}"

    def get_stock_news(self, stock_code: str) -> str:
        """
        抓取該公司最新、最熱門的 3 則財經新聞與公告。
        """
        self.news_count += 1
        if self.news_count > 5:
            return "【系統提示】你今天查看即時新聞的次數已達上限。"

        print(f"📰 正在為 AI 大腦蒐集 {stock_code} 的市場即時新聞公告...")
        time.sleep(1.5)

        try:
            code = str(stock_code).strip().replace(".TW", "").replace(".TWO", "")
            ticker = yf.Ticker(f"{code}.TW")
            news_list = ticker.news
            if not news_list:
                ticker = yf.Ticker(f"{code}.TWO")
                news_list = ticker.news
                
            if not news_list or not isinstance(news_list, list):
                return f"【系統提示】目前財經網絡上沒有關於股票代碼 {code} 的最新重大新聞。"
                
            result = f"=== 📰 股票代碼 {code} 最新市場新聞消息面 ===\n"
            valid_news_count = 0
            for news in news_list:
                title = news.get("title") or news.get("content", {}).get("title")
                publisher = news.get("publisher") or news.get("content", {}).get("provider", {}).get("displayName")
                if not title:
                    title = news.get("summary") or news.get("content", {}).get("summary", "（請查看技術面或市場公告）")
                if not publisher:
                    publisher = "財經新聞網"
                    
                title_clean = str(title).strip()[:80]
                result += f"[{valid_news_count+1}] {title_clean} (來源: {publisher})\n"
                
                valid_news_count += 1
                if valid_news_count >= 3:
                    break
            return result
        except Exception as e:
            return f"【系統錯誤】新聞消息抓取失敗: {str(e)}"

# ==================== 3. 本地模擬交易撮合引擎 ====================
DB_FILE = "portfolio.json"

def load_db():
    utc_now = datetime.datetime.utcnow()
    tw_now = utc_now + datetime.timedelta(hours=8)
    today_str = str(tw_now.date())

    if not os.path.exists(DB_FILE):
        init_data = {
            "openai_bot": {"cash": 100000.0, "holdings": {}, "trade_history": [{"date": today_str, "action": "HOLD", "code": "NONE", "shares": 0, "price": 0, "fee": 0, "reason": "OpenAI 隊目前處於維護狀態。"}]},
            "gemini_bot": {"cash": 100000.0, "holdings": {}, "trade_history": []},
            "last_updated": today_str
        }
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(init_data, f, indent=2)
    with open(DB_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except:
            return {
                "openai_bot": {"cash": 100000.0, "holdings": {}, "trade_history": []},
                "gemini_bot": {"cash": 100000.0, "holdings": {}, "trade_history": []},
                "last_updated": today_str
            }

def save_db(data):
    utc_now = datetime.datetime.utcnow()
    tw_now = utc_now + datetime.timedelta(hours=8)
    data["last_updated"] = f"{tw_now.date()} {tw_now.strftime('%H:%M')}"
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
    
    utc_now = datetime.datetime.utcnow()
    tw_now = utc_now + datetime.timedelta(hours=8)
    today_str = str(tw_now.date())

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
                        "date": today_str, "action": "BUY", "code": code, 
                        "shares": shares, "price": trade_price, "fee": fee, "reason": ai_decision.get("reason", "")
                    })
                    print(f"✅ {log_prefix} 成功買進 {code} 共 {shares} 股，成交價 ${trade_price}")
                else:
                    print(f"❌ Gemini 欲買進 {code}，但本地帳戶資金不足！")
            else:
                print(f"⏳ 【本地掛單未成交】AI 想以 ${trade_price} 低接 {code}，今日市場未達此價位。")
                bot["trade_history"].append({
                    "date": today_str, "action": "HOLD", "code": code,
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
                        "date": today_str, "action": "SELL", "code": code, 
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

# ==================== 4. Playwright CMoney 股市大富翁下單 ====================
def order_on_cmoney(action, stock_code, shares, price=0):
    CMONEY_EMAIL = os.environ.get("CMONEY_EMAIL")
    CMONEY_PWD = os.environ.get("CMONEY_PASSWORD")

    if not CMONEY_EMAIL or not CMONEY_PWD:
        return

    print(f"🤖 [Playwright] 準備發送委託至 CMoney [{action} {stock_code} {shares}股]...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True) 
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()
        try:
            page.goto("https://www.cmoney.tw/vt/main-page.aspx", timeout=60000)
            page.wait_for_timeout(2000)
            email_input = page.locator('input[type="email"], #Username').first
            pwd_input = page.locator('input[type="password"], #Password').first
            email_input.fill(CMONEY_EMAIL)
            pwd_input.fill(CMONEY_PWD)
            page.locator("button[type='submit'], button:has-text('登入')").first.click()
            page.wait_for_timeout(4000)
            
            page.goto("https://www.cmoney.tw/vt/main-page.aspx", timeout=60000)
            search_input = page.locator('input[placeholder*="股票代號"], #txtStockCode').first
            search_input.fill(str(stock_code))
            search_input.press("Enter")
            page.wait_for_timeout(3000) 
            
            if action == "BUY":
                page.locator("button:has-text('買進')").first.click()
            else:
                page.locator("button:has-text('賣出')").first.click()
            page.wait_for_timeout(1000)

            if shares < 1000:
                odd_btn = page.locator("text='零股'").first
                if odd_btn.is_visible(): odd_btn.click()
            
            page.locator('input[id*="Qty"], #txtQuantity').first.fill(str(shares))
            if price > 0:
                page.locator('input[id*="Price"], #txtPrice').first.fill(str(price))
                
            page.locator("button:has-text('下單'), button:has-text('送出')").first.click()
            page.wait_for_timeout(3000)
            print(f"🎉 [CMoney 下單成功]：{action} {stock_code} {shares} 股")
        except Exception as e:
            print(f"💥 [CMoney 流程中斷]: {str(e)}")
        browser.close()

# ==================== 5. 動態智慧提示詞系統 (大師演算法) ====================
def get_dynamic_prompt(current_mode, current_time_str, top_stocks, current_portfolio_text):
    tickers_str = ", ".join([f"{x['code']}" for x in top_stocks])
    
    return f"""
你是台股「波段價值動能綜合流派」的量化操盤大師。初始資金 10 萬，支援零股交易。
🔔 【時段狀態感應】：台北時間 {current_time_str}，當前時段【{current_mode}】。

📖 【你的歷史記憶與庫存現況】：
{current_portfolio_text}

🎯 【今日雷達自動掃描到的熱門觀察股池】：
台灣股市今日成交量與波動度最高的焦點股為：【{tickers_str}】。

🌟 【大師級操盤特權與嚴格紀律】：
1. 嚴禁盲目觀望：做決定前，請選定你想捕捉行情的股票，強烈呼叫「get_stock_news」與「get_stock_kline_chart」！工具會精確回傳 KD、RSI 與均線數據！
2. 大師判斷邏輯：
   - 【買進 BUY 訊號】：若 KD 指標出現黃金交叉、或 RSI 處於25以下超賣低階區，且新聞面無極度重大利空，你必須果斷限價或現價「BUY」進場！
   - 【賣出 SELL 訊號】：若你持有的庫存股帳面利潤已達 7%（波段停利），或是 KD 出現死亡交叉、股價跌破20MA（破線停損），請毫不猶豫執行「SELL」！
3. 自主控制：若工具回傳的數據顯示全市場均處於高檔爆量過熱區 (RSI>80)，你才可基於風控選擇空倉觀望。

⚠️ 嚴格 JSON 規格輸出（不要附帶 any 其他 Markdown 說明）：
{{
  "reason": "【時段決策: {current_mode}】請以大師口吻詳細分析：你呼叫了哪幾檔股票的量化指標？其KD與RSI數據如何？你目前的持股庫存需要停損或停利嗎？據此給出交易決策。",
  "trades": [
    {{
      "code": "四碼代碼", 
      "action": "BUY 或 SELL", 
      "shares": 股數, 
      "price": 理想限價價格
    }}
  ]
}}
"""

def ask_gemini(tools_object, current_mode, current_time_str, top_stocks):
    try:
        if not gemini_client:
            raise Exception("未設定 API 金鑰")
            
        print(f"⏳ [大腦喚醒] 正在喚醒 Gemini 2.5 量化大師核心，塞入熱門股雷達數據...")
        
        # 建立目前的持股上下文
        db = load_db()
        bot = db["gemini_bot"]
        p_text = f"- 現金餘額: ${bot['cash']:.1f} 元\n- 當前持股庫存：\n"
        if not bot["holdings"]:
            p_text += "  (目前為空倉，無任何持股庫存)\n"
        for code, info in bot["holdings"].items():
            p_text += f"  * 股票代碼 {code}: 持有 {info['shares']} 股，每股平均成本 ${info['avg_cost']:.1f}\n"
            
        shared_tools = [tools_object.get_stock_kline_chart, tools_object.get_stock_news]
        prompt = get_dynamic_prompt(current_mode, current_time_str, top_stocks, p_text)
        
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
        print(f"💥 Gemini 執行受限 ({e})，啟動防護備用防禦...")
        return {"reason": "系統防護大腦啟動，今日暫不盲目出手。", "trades": []}

# ==================== 6. 網頁 HTML 生成與儀表板 ====================
def generate_html_dashboard():
    db = load_db()
    html_content = """
    <!DOCTYPE html>
    <html lang="zh-TW">
    <head>
        <meta charset="UTF-8">
        <title>🤖 雙模智慧全自主炒股直播 📈</title>
        <script src="https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4"></script>
    </head>
    <body class="bg-gray-900 text-gray-100 min-h-screen p-4 md:p-8 font-sans">
        <div class="max-w-6xl mx-auto">
            <header class="text-center my-6">
                <h1 class="text-3xl md:text-5xl font-extrabold text-transparent bg-clip-text bg-gradient-to-r from-teal-400 via-blue-500 to-purple-600 mb-2">🤖 全天候動態時段操盤系統 📈</h1>
                <p class="text-gray-400 text-sm md:text-base">全自動「熱門股雷達」+ 內建量化指標計算機完全體大師大腦</p>
                <div class="inline-block bg-gray-800 text-gray-400 px-4 py-1.5 rounded-full text-xs md:text-sm mt-3 border border-gray-700">
                    🕒 網頁最後更新時間：""" + db["last_updated"] + """
                </div>
            </header>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-8 mt-6">
    """

    for bot_key, name, bg_gradient in [
        ("openai_bot", "OpenAI (ChatGPT-4o) 隊", "from-gray-700 to-gray-800"), 
        ("gemini_bot", "Google (Gemini-2.5) 大師隊", "from-indigo-600 to-purple-800")
    ]:
        bot = db[bot_key]
        table_rows = ""
        total_stock_value = 0
        
        for code, info in list(bot["holdings"].items()):
            shares = info.get("shares", 0)
            avg_cost = info.get("avg_cost", 0)
            if shares <= 0: continue
                
            price = None
            for suffix in [".TW", ".TWO"]:
                try:
                    price = yf.Ticker(f"{code}{suffix}").history(period="1d")['Close'].iloc[-1]
                    break
                except: continue
            if price is None: price = avg_cost
                
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
        last_reason = bot["trade_history"][-1].get("reason", "未提供具體理由。") if bot["trade_history"] else "今日暫無新的交易決策理由。"

        html_content += f"""
        <div class="bg-gray-800 rounded-2xl shadow-2xl border border-gray-700 overflow-hidden flex flex-col justify-between">
            <div>
                <div class="bg-gradient-to-r {bg_gradient} p-4 shadow-inner">
                    <h2 class="text-xl font-black text-white flex justify-between items-center">
                        <span>{name}</span>
                        <span class="text-xs bg-black/40 px-3 py-1 rounded-full border border-white/10">資金 $100,000</span>
                    </h2>
                </div>
                <div class="p-6">
                    <div class="grid grid-cols-3 gap-3 text-center mb-6">
                        <div class="bg-gray-900/60 p-3 rounded-xl border border-gray-700/50"><p class="text-xxs text-gray-400 mb-1">總資產價值</p><p class="text-base md:text-lg font-black {roi_color}">${assets:,.0f}</p></div>
                        <div class="bg-gray-900/60 p-3 rounded-xl border border-gray-700/50"><p class="text-base md:text-lg font-bold text-yellow-500">${bot['cash']:,.0f}</p></div>
                        <div class="bg-gray-900/60 p-3 rounded-xl border border-gray-700/50"><p class="text-xxs text-gray-400 mb-1">總累積投報</p><p class="text-base md:text-lg font-black {roi_color}">{total_roi:+.2f}%</p></div>
                    </div>
                    <div class="bg-gray-900/40 p-4 rounded-xl border border-gray-700/30 mb-6">
                        <h4 class="text-xs font-bold text-yellow-400 uppercase tracking-wider mb-1">🧠 當前量化大師操盤思路</h4>
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
    html_content += "</div></div></body></html>"
    with open("index.html", "w", encoding="utf-8") as f: f.write(html_content)
    print("✨ [網頁更新成功] 精美 index.html 直表面板已完成覆蓋！")

# ==================== 7. 引擎啟動入口 ====================
if __name__ == "__main__":
    print(f"🤖 正在啟動雙模智慧全自主網頁炒股核心引擎 (CMoney 完全體支援)...")
    
    if not is_taiwan_market_open():
        print("🏖️ 偵測到今日台股未開盤！直接進入休假模式。")
        log_holiday_reason("【今日休市】今天是週末或國定例假日，台股未開盤。AI 機器人正在休息覆盤中。")
        generate_html_dashboard()
    else:
        utc_now = datetime.datetime.utcnow()
        tw_now = utc_now + datetime.timedelta(hours=8)
        
        now_hour = tw_now.hour
        now_minute = tw_now.minute
        time_val = now_hour * 100 + now_minute
        current_time_str = tw_now.strftime("%H:%M")

        if 830 <= time_val < 900:
            current_mode = "盤前部署模式"
        elif 900 <= time_val <= 1330:
            current_mode = "盤中戰鬥模式"
        else:
            current_mode = "盤後覆盤模式"

        # 🔥 啟動動態熱門量大個股掃描器
        top_stocks = get_taiwan_top_volume_tickers()

        tools_manager = StockTools()
        print(f"👉 正在喚醒 Google Gemini 隊進行【{current_mode}】決策... (精準台灣時間: {current_time_str})")
        gemini_decision = ask_gemini(tools_manager, current_mode, current_time_str, top_stocks)
        
        execute_trades("gemini_bot", gemini_decision, current_mode)
        
        # ─── 📢 讓 AI 大腦在命令列公開宣佈今日決策 ───
        print("\n📢 【Gemini 量化操盤大師本日決策公開】")
        print(f"💬 核心思路：{gemini_decision.get('reason', '無詳細說明')}")
        if not gemini_decision.get("trades"):
            print("💤 決策結果：今日數據未達買賣門檻，決定【空倉冷靜觀望】，不進行買賣委託。")
        for t in gemini_decision.get("trades", []):
            print(f"🎯 委託計畫：預計執行【{t.get('action')}】股票代碼 {t.get('code')}，數量 {t.get('shares')} 股，目標價格 ${t.get('price', '市價')}")
        print("───────────────────────────────────\n")

        for t in gemini_decision.get("trades", []):
            order_on_cmoney(
                action=t.get("action"),
                stock_code=t.get("code"),
                shares=t.get("shares"),
                price=t.get("price", 0)
            )
        
        generate_html_dashboard()
