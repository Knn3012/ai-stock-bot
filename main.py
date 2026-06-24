import os
import json
import datetime
import yfinance as yf
import mplfinance as mpf
from tabulate import tabulate
from openai import OpenAI
from google import genai
from google.genai import types

# ==================== 1. 初始化與工具設定 ====================
OPENAI_KEY = os.environ.get("OPENAI_API_KEY")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

openai_client = OpenAI(api_key=OPENAI_KEY)
gemini_client = genai.Client(api_key=GEMINI_KEY)

class StockTools:
    def __init__(self):
        self.call_count = {}

    def get_kline_chart(self, stock_code: str) -> str:
        """為 AI 畫出過去 30 天的台股 K 線圖工具"""
        # 限制單一 AI 每天最多看 3 張圖，防止無限循環卡死
        self.call_count[stock_code] = self.call_count.get(stock_code, 0) + 1
        if sum(self.call_count.values()) > 4:
            return "【系統提示】你今天看 K 線圖的總次數已達上限，請直接做出最終 JSON 決策。"
        
        try:
            ticker_sym = f"{stock_code}.TW"
            stock = yf.Ticker(ticker_sym)
            df = stock.history(period="30d")
            if df.empty:
                return f"【系統錯誤】找不到代碼 {stock_code} 的台股數據。"
            
            image_path = f"{stock_code}_kline.png"
            mpf.plot(df, type='candle', mav=(5, 20), volume=True,
                     style='charles', title=f"Stock {stock_code}", savefig=image_path)
            return f"成功生成 {stock_code} 的 K 線圖，已傳送至你的視覺大腦。"
        except Exception as e:
            return f"【系統錯誤】畫圖失敗，原因：{str(e)}"

# ==================== 2. 資料庫讀寫與交易引擎 ====================
DB_FILE = "portfolio.json"

def load_db():
    if not os.path.exists(DB_FILE):
        init_data = {
            "openai_bot": {"cash": 100000.0, "holdings": {}, "trade_history": []},
            "gemini_bot": {"cash": 100000.0, "holdings": {}, "trade_history": []},
            "last_updated": str(datetime.date.today())
        }
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(init_data, f, indent=2)
    with open(DB_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_db(data):
    data["last_updated"] = str(datetime.date.today())
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def execute_trades(bot_key, ai_decision):
    db = load_db()
    bot = db[bot_key]
    trades = ai_decision.get("trades", [])
    
    for t in trades:
        code = str(t.get("code")).strip()
        action = t.get("action")
        shares = int(t.get("shares", 0))
        if shares <= 0 or action not in ["BUY", "SELL"]:
            continue
            
        # 真實台股即時查價
        try:
            tick = yf.Ticker(f"{code}.TW")
            hist = tick.history(period="1d")
            if hist.empty:
                continue
            price = float(hist['Close'].iloc[-1])
        except:
            continue # 查不到真實價格就跳過，確保不污染資料庫
            
        amount = price * shares
        fee = int(amount * 0.001425) # 買賣手續費
        
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
                bot["trade_history"].append({"date": str(datetime.date.today()), "action": "BUY", "code": code, "shares": shares, "price": price, "fee": fee})
                
        elif action == "SELL":
            if code in bot["holdings"] and bot["holdings"][code]["shares"] >= shares:
                tax = int(amount * 0.003) # 台灣股票證交稅 0.3%
                total_revenue = amount - fee - tax
                bot["cash"] += total_revenue
                bot["holdings"][code]["shares"] -= shares
                bot["trade_history"].append({"date": str(datetime.date.today()), "action": "SELL", "code": code, "shares": shares, "price": price, "fee": fee + tax})
                if bot["holdings"][code]["shares"] == 0:
                    del bot["holdings"][code]
    db[bot_key] = bot
    save_db(db)

# ==================== 3. 呼叫大腦模型 ====================
SYSTEM_PROMPT = """
你是台股神級操盤手，初始資金 10 萬，支援零股交易。你可以操作全台灣市場任何股票。
請按以下步驟操作：
1. 先用「Google 搜尋工具」調查今天最熱門的財經新聞、美股收盤與台股題材。
2. 鎖定黑馬股後，呼叫「get_kline_chart」查看該股票的 30 天 K 線圖（包含均線與成交量）。
3. 綜合分析技術面與消息面後，決定今天的買賣。

⚠️ 嚴格規則：你的輸出必須完全符合以下 JSON 格式，且不要回答任何多餘對話：
{
  "reason": "綜合消息面與 K 線技術面的詳細理由",
  "trades": [
    {"code": "四碼台股代碼", "action": "BUY 或 SELL", "shares": 股數}
  ]
}
"""

def run_openai_bot(tools):
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": SYSTEM_PROMPT}],
            tools=[{"type": "web_search"}], # 開啟 OpenAI 聯網
            response_format={"type": "json_object"}
        )
        # 這裡簡化模擬工具調用，實際環境中 GPT-4o 如果需要看圖可手動在 backend 傳遞
        return json.loads(response.choices[0].message.content)
    except:
        return {"reason": "OpenAI 發生異常", "trades": []}

def run_gemini_bot(tools_object):
    try:
        shared_tools = [types.Tool(google_search=types.GoogleSearch()), tools_object.get_kline_chart]
        response = gemini_client.models.generate_content(
            model='gemini-3.5-flash',
            contents=SYSTEM_PROMPT,
            config=types.GenerateContentConfig(tools=shared_tools, response_mime_type="application/json")
        )
        return json.loads(response.text)
    except:
        return {"reason": "Gemini 發生異常", "trades": []}

# ==================== 4. 戰報儀表板印出 ====================
def print_dashboard():
    db = load_db()
    for bot_key, name in [("openai_bot", "OpenAI (ChatGPT) 隊"), ("gemini_bot", "Google (Gemini) 隊")]:
        print(f"\n======== 🤖 {name} 戰報 ========")
        bot = db[bot_key]
        table = []
        total_stock_value = 0
        for code, info in bot["holdings"].items():
            if info["shares"] <= 0: continue
            try:
                price = yf.Ticker(f"{code}.TW").history(period="1d")['Close'].iloc[-1]
            except:
                price = info["avg_cost"]
            val = price * info["shares"]
            total_stock_value += val
            profit = val - (info["avg_cost"] * info["shares"])
            roi = (profit / (info["avg_cost"] * info["shares"])) * 100
            table.append([code, f"{info['shares']} 股", f"{info['avg_cost']:.1f}", f"{price:.1f}", f"{profit:+.0f}", f"{roi:+.2f}%"])
        
        assets = bot["cash"] + total_stock_value
        print(f"💰 總資產: {assets:,.0f} 元 (累積投報率: {((assets-100000)/100000)*100:+.2f}%)")
        print(f"💵 現金: {bot['cash']:,.0f} 元 | 📈 持股加總: {total_stock_value:,.0f} 元")
        if table:
            print(tabulate(table, headers=["代碼", "股數", "平均成本", "當前市價", "未實現損益", "報酬率"], tablefmt="grid"))
        else:
            print("空倉（目前未持有任何股票）")

# ==================== 5. 主執行邏輯 ====================
if __name__ == "__main__":
    print("🚀 啟動雙 AI 全自主炒股實驗...")
    tools = StockTools()
    
    # 跑 ChatGPT
    op_decision = run_openai_bot(tools)
    execute_trades("openai_bot", op_decision)
    
    # 跑 Gemini
    ge_decision = run_gemini_bot(tools)
    execute_trades("gemini_bot", ge_decision)
    
    # 印出最新戰報
    print_dashboard()