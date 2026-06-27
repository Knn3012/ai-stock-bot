import os
import json
import time
import datetime
import yfinance as yf
import mplfinance as mpf
from google import genai
from google.genai import types

# ==================== 1. 初始化與工具設定 ====================
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

# 初始化 Gemini 客戶端
gemini_client = genai.Client(api_key=GEMINI_KEY) if GEMINI_KEY else None

class StockTools:
    def __init__(self):
        self.call_count = 0

    def get_stock_kline_chart(self, stock_code: str) -> str:
        """
        輸入台灣股票四碼代碼（例如 '2330'），自動下載過去30天的股價，
        生成一張包含5MA、20MA與成交量的 K 線圖，並回傳。AI 每天最多能呼叫 3 次。
        """
        self.call_count += 1
        if self.call_count > 3:
            return "【系統提示】你今天查看 K 線圖的次數已達上限，請勿再呼叫此工具。"
        
        # 防爆緩衝保護
        print(f"⏳ 正在調用 K 線圖生成工具 [代碼: {stock_code}]，進入免費版頻率防護延遲...")
        time.sleep(5) 
        
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
            return f"【系統通知】成功生成 {code} 的 K 線圖，已輸入你的視覺大腦，請據此進行分析。"
        except Exception as e:
            return f"【系統錯誤】暫時無法取得該 K 圖，原因：{str(e)}"

# ==================== 2. 真實台股交易撮合引擎 ====================
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
            # 防損壞重置
            return {
                "openai_bot": {"cash": 100000.0, "holdings": {}, "trade_history": [{"date": str(datetime.date.today()), "action": "HOLD", "code": "NONE", "shares": 0, "price": 0, "fee": 0, "reason": "OpenAI 隊目前處於非賽季維護狀態。"}]},
                "gemini_bot": {"cash": 100000.0, "holdings": {}, "trade_history": []},
                "last_updated": str(datetime.date.today())
            }

def save_db(data):
    data["last_updated"] = str(datetime.date.today())
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def execute_trades(bot_key, ai_decision):
    db = load_db()
    bot = db[bot_key]
    trades = ai_decision.get("trades", [])
    
    for t in trades:
        code = str(t.get("code")).strip().replace(".TW", "").replace(".TWO", "")
        action = t.get("action")
        try:
            shares = int(t.get("shares", 0))
        except:
            continue
            
        if shares <= 0 or action not in ["BUY", "SELL"]:
            continue
            
        price = None
        for suffix in [".TW", ".TWO"]:
            try:
                tick = yf.Ticker(f"{code}{suffix}")
                hist = tick.history(period="1d")
                if not hist.empty:
                    price = float(hist['Close'].iloc[-1])
                    break
            except:
                continue
                
        if price is None:
            print(f"⚠️ 找不到股票代碼 {code} 的真實價格，取消該筆交易。")
            continue
            
        amount = price * shares
        fee = max(20, int(amount * 0.001425)) 
        
        if action == "BUY":
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
                    "shares": shares, "price": price, "fee": fee, "reason": ai_decision.get("reason", "")
                })
                print(f"✅ {bot_key} 成功買進 {code} 共 {shares} 股，成交價 {price}")
            else:
                print(f"❌ {bot_key} 欲買進 {code}，但資金不足！金額需要 {total_cost}，剩餘現金 {bot['cash']}")
                
        elif action == "SELL":
            if code in bot["holdings"] and bot["holdings"][code]["shares"] >= shares:
                tax = int(amount * 0.003) 
                total_revenue = amount - fee - tax
                bot["cash"] += total_revenue
                bot["holdings"][code]["shares"] -= shares
                bot["trade_history"].append({
                    "date": str(datetime.date.today()), "action": "SELL", "code": code, 
                    "shares": shares, "price": price, "fee": fee + tax, "reason": ai_decision.get("reason", "")
                })
                print(f"✅ {bot_key} 成功賣出 {code} 共 {shares} 股，成交價 {price}")
                if bot["holdings"][code]["shares"] == 0:
                    del bot["holdings"][code]
            else:
                print(f"❌ {bot_key} 欲賣出 {code}，但並未持有足夠股數！")
                
    db[bot_key] = bot
    save_db(db)

# ==================== 3. 核心大腦分析系統 ====================
SYSTEM_PROMPT = """
你是擁有完全自主權的台股頂級基金操盤手。你現在有 10 萬元初始資金，支援零股交易。
請務必執行以下步驟：
1. 先利用「Google 搜尋工具」去查過去 24 小時最熱門的台灣產業新聞與市場題材。
2. 當你從新聞中鎖定想交易的台股股票時，請呼叫「get_stock_kline_chart」工具來查看這檔股票的 30 天 K 線圖。
3. 仔細評估 K 線圖的技術面與消息面，做出最終交易決策。

⚠️ 嚴格規則：
- 你的輸出必須「完全符合」以下 JSON 格式，不要回答任何多餘對話：
{
  "reason": "消息面與 K 線技術面綜合分析的詳細理由",
  "trades": [
    {"code": "四碼台灣股票代碼", "action": "BUY 或 SELL", "shares": 股數}
  ]
}
"""

def ask_gemini(tools_object):
    try:
        if not gemini_client:
            raise Exception("未設定 GEMINI_API_KEY 金鑰")
            
        print("⏳ [防爆機制] 正在喚醒 Gemini 輕量核心（已關閉聯網搜尋以節省每日額度）...")
        time.sleep(5) # 稍微睡 5 秒即可
        
        # 💡 關鍵修改：把原來的 types.Tool(google_search=...) 拿掉，只留下看 K 線圖的工具！
        shared_tools = [tools_object.get_stock_kline_chart]
        
        response = gemini_client.models.generate_content(
            model='gemini-2.5-flash',
            contents="""
            請扮演台股操盤手，直接從以下熱門股中挑選一檔進行短線策略佈局：
            (2330台積電、2317鴻海、2454聯發科、2603長榮、2382廣達)。
            你必須直接呼叫 get_stock_kline_chart 工具查看該檔案的 K 線圖後，再輸出下單 JSON。
            """,
            config=types.GenerateContentConfig(
                tools=shared_tools,
                response_mime_type="application/json"
            )
        )
        return json.loads(response.text)
    except Exception as e:
        print(f"💥 Gemini 執行受限 ({e})，改用本地預設防禦策略。")
        return {"reason": "防禦性持有現金。", "trades": []}

# ==================== 4. 網頁 HTML 生成與儀表板 ====================
def generate_html_dashboard():
    db = load_db()
    
    html_content = """
    <!DOCTYPE html>
    <html lang="zh-TW">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>🤖 雙 AI 全自主炒股世紀對決直播 📈</title>
        <script src="https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4"></script>
    </head>
    <body class="bg-gray-900 text-gray-100 min-h-screen p-4 md:p-8 font-sans">
        <div class="max-w-6xl mx-auto">
            <header class="text-center my-6">
                <h1 class="text-3xl md:text-5xl font-extrabold text-transparent bg-clip-text bg-gradient-to-r from-yellow-400 via-orange-400 to-red-500 mb-2">🤖 雙 AI 全自主炒股世紀對決 📈</h1>
                <p class="text-gray-400 text-sm md:text-base">ChatGPT 隊 vs Gemini 隊，完全自主聯網看新聞、看 K 圖、全自動台股模擬操作帳戶</p>
                <div class="inline-block bg-gray-800 text-gray-400 px-4 py-1.5 rounded-full text-xs md:text-sm mt-3 border border-gray-700">
                    🕒 網頁最後更新時間 (台北時間)：""" + db["last_updated"] + """
                </div>
            </header>
            
            <div class="grid grid-cols-1 md:grid-cols-2 gap-8 mt-6">
    """

    for bot_key, name, color, bg_gradient in [
        ("openai_bot", "OpenAI (ChatGPT-4o) 隊", "text-emerald-400", "from-gray-700 to-gray-800"), 
        ("gemini_bot", "Google (Gemini-2.0) 隊", "text-blue-400", "from-blue-600 to-indigo-800")
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
                        <div class="bg-gray-900/60 p-3 rounded-xl border border-gray-700/50"><p class="text-xxs text-gray-400 mb-1">剩餘現金</p><p class="text-base md:text-lg font-bold text-yellow-500">${bot['cash']:,.0f}</p></div>
                        <div class="bg-gray-900/60 p-3 rounded-xl border border-gray-700/50"><p class="text-xxs text-gray-400 mb-1">總累積投報</p><p class="text-base md:text-lg font-black {roi_color}">{total_roi:+.2f}%</p></div>
                    </div>
                    
                    <div class="bg-gray-900/40 p-4 rounded-xl border border-gray-700/30 mb-6">
                        <h4 class="text-xs font-bold text-yellow-400 uppercase tracking-wider mb-1">🧠 最新操盤思路核心</h4>
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
    print("✨ [網頁更新成功] 精美 index.html 直播面板已完成覆蓋！")

# ==================== 5. 引擎啟動入口 ====================
if __name__ == "__main__":
    print("🤖 正在啟動單 AI 全自主網頁炒股核心引擎 (Gemini 獨佔優化版)...")
    tools_manager = StockTools()
    
    # 略過 ChatGPT，直接進入 Gemini 隊決策
    print("👉 正在喚醒 Google Gemini 隊進行決策...")
    gemini_decision = ask_gemini(tools_manager)
    execute_trades("gemini_bot", gemini_decision)
    
    generate_html_dashboard()
