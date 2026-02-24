import os
import time
import hmac
import hashlib
import base64
import urllib.parse
import akshare as ak
import yfinance as yf
from dotenv import load_dotenv
from openai import OpenAI
from tavily import TavilyClient
import requests

# -------------------------- 核心配置加载（复用你已有的.env配置，无需修改此处） --------------------------
# 加载仓库根目录的.env文件，直接复用你已经配好的所有密钥
load_dotenv()

# AI模型配置（优先用你配置的DeepSeek，兼容OpenAI格式，无国内网络限制）
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "deepseek-chat")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# 钉钉推送配置（直接复用你已配好的密钥）
DINGTALK_WEBHOOK = os.getenv("DINGTALK_WEBHOOK_URL")
DINGTALK_SECRET = os.getenv("DINGTALK_SECRET")

# 新闻搜索配置（复用你已配的Tavily）
TAVILY_API_KEY = os.getenv("TAVILY_API_KEYS")
NEWS_MAX_AGE_DAYS = int(os.getenv("NEWS_MAX_AGE_DAYS", 3))

# 交易纪律配置（和原系统保持一致）
BIAS_THRESHOLD = float(os.getenv("BIAS_THRESHOLD", 5.0))
# ------------------------------------------------------------------------------------------------------

# -------------------------- 工具函数（和原系统逻辑完全对齐，避免兼容问题） --------------------------
def dingtalk_sign(secret):
    """钉钉官方标准加签算法，和原系统完全一致"""
    timestamp = str(round(time.time() * 1000))
    secret_enc = secret.encode('utf-8')
    string_to_sign = f"{timestamp}\n{secret}"
    string_to_sign_enc = string_to_sign.encode('utf-8')
    hmac_code = hmac.new(secret_enc, string_to_sign_enc, digestmod=hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    return timestamp, sign

def get_stock_type(code):
    """自动识别股票市场类型，兼容原系统代码格式"""
    code = code.strip().upper()
    if code.startswith(("60", "68", "900")):
        return "cn_sh", "A股沪市"
    elif code.startswith(("00", "30", "200")):
        return "cn_sz", "A股深市"
    elif code.startswith("HK"):
        return "hk", "港股"
    else:
        return "us", "美股"

def get_stock_base_info(code):
    """获取股票基础信息、实时行情、核心技术指标，和原系统数据源一致"""
    code = code.strip().upper()
    stock_type, market_name = get_stock_type(code)
    base_info = {"code": code, "market": market_name, "name": "未知", "error": None}
    
    try:
        # A股/港股用AkShare（和原系统一致）
        if stock_type in ["cn_sh", "cn_sz", "hk"]:
            if stock_type in ["cn_sh", "cn_sz"]:
                # A股实时行情
                spot_df = ak.stock_zh_a_spot_em()
                stock_row = spot_df[spot_df["代码"] == code]
                if not stock_row.empty:
                    base_info["name"] = stock_row.iloc[0]["名称"]
                    base_info["latest_price"] = float(stock_row.iloc[0]["最新价"])
                    base_info["change_percent"] = float(stock_row.iloc[0]["涨跌幅"])
                    base_info["volume"] = stock_row.iloc[0]["成交量"]
                    base_info["turnover"] = stock_row.iloc[0]["成交额"]
                
                # A股K线与均线数据
                kline_df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date="20250101", adjust="qfq")
                 if not kline_df.empty:
                    kline_df = kline_df.sort_values("日期", ascending=False).head(60)
                    base_info["ma5"] = round(kline_df["收盘"].head(5).mean(), 2)
                    base_info["ma10"] = round(kline_df["收盘"].head(10).mean(), 2)
                    base_info["ma20"] = round(kline_df["收盘"].head(20).mean(), 2)
                    base_info["ma60"] = round(kline_df["收盘"].head(60).mean(), 2)
                    # 乖离率计算（和原系统交易纪律一致）
                    base_info["bias_5"] = round((base_info["latest_price"] - base_info["ma5"]) / base_info["ma5"] * 100, 2)
            
            # 港股行情
            elif stock_type == "hk":
                hk_code = code.replace("HK", "").zfill(5)
                spot_df = ak.stock_hk_spot_em()
                stock_row = spot_df[spot_df["代码"] == hk_code]
                if not stock_row.empty:
                    base_info["name"] = stock_row.iloc[0]["名称"]
                    base_info["latest_price"] = float(stock_row.iloc[0]["最新价"])
                    base_info["change_percent"] = float(stock_row.iloc[0]["涨跌幅"])
                
                kline_df = ak.stock_hk_hist(symbol=hk_code, period="daily", start_date="20250101", adjust="qfq")
                if not kline_df.empty:
                    kline_df = kline_df.sort_values("日期", ascending=False).head(60)
                    base_info["ma5"] = round(kline_df["收盘"].head(5).mean(), 2)
                    base_info["ma10"] = round(kline_df["收盘"].head(10).mean(), 2)
                    base_info["ma20"] = round(kline_df["收盘"].head(20).mean(), 2)
                    base_info["bias_5"] = round((base_info["latest_price"] - base_info["ma5"]) / base_info["ma5"] * 100, 2)
        
        # 美股用YFinance（和原系统一致）
        elif stock_type == "us":
            ticker = yf.Ticker(code)
            info = ticker.info
            base_info["name"] = info.get("shortName", code)
            hist = ticker.history(period="60d", interval="1d")
            if not hist.empty:
                hist = hist.sort_index(ascending=False)
                base_info["latest_price"] = round(hist["Close"].iloc[0], 2)
                base_info["change_percent"] = round((hist["Close"].iloc[0] - hist["Close"].iloc[1]) / hist["Close"].iloc[1] * 100, 2)
                base_info["ma5"] = round(hist["Close"].head(5).mean(), 2)
                base_info["ma10"] = round(hist["Close"].head(10).mean(), 2)
                base_info["ma20"] = round(hist["Close"].head(20).mean(), 2)
                base_info["ma60"] = round(hist["Close"].head(60).mean(), 2)
                base_info["bias_5"] = round((base_info["latest_price"] - base_info["ma5"]) / base_info["ma5"] * 100, 2)
    
    except Exception as e:
        base_info["error"] = f"行情获取失败：{str(e)}"
        print(f"⚠️  {code} 行情获取异常：{str(e)}")
    
    return base_info

def get_stock_news(stock_name, code, market):
    """获取股票最新新闻，和原系统Tavily搜索逻辑一致"""
    if not TAVILY_API_KEY:
        return "未配置Tavily API，无法获取新闻数据"
    
    try:
        tavily = TavilyClient(api_key=TAVILY_API_KEY.split(",")[0])
        search_keyword = f"{stock_name} {code} {market} 最新新闻 公告 业绩 行业动态 2026"
        response = tavily.search(
            query=search_keyword,
            max_results=5,
            max_age_days=NEWS_MAX_AGE_DAYS,
            include_domains=["eastmoney.com", "10jqka.com.cn", "cls.cn", "reuters.com", "bloomberg.com"],
            exclude_pornographic=True
        )
        
        news_list = []
        for idx, result in enumerate(response.get("results", []), 1):
            news_list.append(f"{idx}. {result['title']}：{result['content'][:200]}...")
        
        return "\n".join(news_list) if news_list else f"近{NEWS_MAX_AGE_DAYS}天暂无相关重大新闻"
    
    except Exception as e:
        return f"新闻获取失败：{str(e)}"

def generate_analysis_report(stock_info, news_content, strategy_config):
    """调用AI生成分析报告，严格遵循原系统决策仪表盘格式和策略要求"""
    # 优先使用DeepSeek（OpenAI兼容格式），无配置则用Gemini
    if OPENAI_API_KEY:
        client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
        prompt = f"""
        你是专业的股票量化分析助手，严格按照以下【策略规则】和【输出格式】生成分析报告，禁止偏离要求。

        【策略规则】
        {strategy_config}

        【股票基础数据】
        股票代码：{stock_info['code']}
        股票名称：{stock_info['name']}
        所属市场：{stock_info['market']}
        最新价格：{stock_info.get('latest_price', '未知')}
        当日涨跌幅：{stock_info.get('change_percent', '未知')}%
        MA5均线：{stock_info.get('ma5', '未知')}
        MA10均线：{stock_info.get('ma10', '未知')}
        MA20均线：{stock_info.get('ma20', '未知')}
        5日乖离率：{stock_info.get('bias_5', '未知')}%
        乖离率阈值：{BIAS_THRESHOLD}%

        【最新相关新闻/公告】
        {news_content}

        【输出格式要求】
        严格按照原系统决策仪表盘格式输出，使用markdown，适配钉钉渲染，结构如下：
        ⚪ {股票名称}({股票代码})
        📊 综合评分：0-100分 | 操作建议：买入/观望/卖出 | 多空观点：看多/震荡/看空
        📰 重要信息速览
        💭 舆情情绪：一句话总结舆情多空方向
        📈 技术面判断：一句话总结均线、趋势、乖离率情况
        📊 业绩与基本面：一句话总结核心基本面情况
        🚨 风险警报：分点列出核心风险，最多3点，每点不超过50字
        ✨ 利好催化：分点列出核心利好，最多3点，每点不超过50字
        🎯 精确操作点位
        - 买入参考价：xxx
        - 止损参考价：xxx
        - 第一目标价：xxx
        - 第二目标价：xxx
        📝 操作检查清单：按策略规则，每项标注「满足/注意/不满足」
        """
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            stream=False
        )
        return response.choices[0].message.content.strip()
    
    # Gemini备用方案
    elif GEMINI_API_KEY:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-1.5-flash")
        prompt = f"""
        你是专业的股票量化分析助手，严格按照以下【策略规则】和【输出格式】生成分析报告，禁止偏离要求。

        【策略规则】
        {strategy_config}

        【股票基础数据】
        股票代码：{stock_info['code']}
        股票名称：{stock_info['name']}
        所属市场：{stock_info['market']}
        最新价格：{stock_info.get('latest_price', '未知')}
        当日涨跌幅：{stock_info.get('change_percent', '未知')}%
        MA5均线：{stock_info.get('ma5', '未知')}
        MA10均线：{stock_info.get('ma10', '未知')}
        MA20均线：{stock_info.get('ma20', '未知')}
        5日乖离率：{stock_info.get('bias_5', '未知')}%
        乖离率阈值：{BIAS_THRESHOLD}%

        【最新相关新闻/公告】
        {news_content}

        【输出格式要求】
        严格按照原系统决策仪表盘格式输出，使用markdown，适配钉钉渲染，结构如下：
        ⚪ {股票名称}({股票代码})
        📊 综合评分：0-100分 | 操作建议：买入/观望/卖出 | 多空观点：看多/震荡/看空
        📰 重要信息速览
        💭 舆情情绪：一句话总结舆情多空方向
        📈 技术面判断：一句话总结均线、趋势、乖离率情况
        📊 业绩与基本面：一句话总结核心基本面情况
        🚨 风险警报：分点列出核心风险，最多3点，每点不超过50字
        ✨ 利好催化：分点列出核心利好，最多3点，每点不超过50字
        🎯 精确操作点位
        - 买入参考价：xxx
        - 止损参考价：xxx
        - 第一目标价：xxx
        - 第二目标价：xxx
        📝 操作检查清单：按策略规则，每项标注「满足/注意/不满足」
        """
        response = model.generate_content(prompt)
        return response.text.strip()
    
    else:
        return "❌ 未配置任何AI模型API，无法生成分析报告"

def push_to_dingtalk(report_content, stock_codes):
    """推送报告到钉钉，和原系统推送逻辑完全对齐"""
    if not DINGTALK_WEBHOOK or not DINGTALK_SECRET:
        print("⚠️  未配置钉钉Webhook或SECRET，跳过推送")
        return False
    
    try:
        timestamp, sign = dingtalk_sign(DINGTALK_SECRET)
        final_webhook = f"{DINGTALK_WEBHOOK}&timestamp={timestamp}&sign={sign}"
        
        # 钉钉markdown格式，和原系统保持一致
        full_report = f"""
# 🎯 手动股票分析报告
分析时间：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}
本次分析标的：{stock_codes}

---
{report_content}

---
生成自 daily_stock_analysis 系统
        """
        
        data = {
            "msgtype": "markdown",
            "markdown": {
                "title": "股票分析报告",
                "text": full_report
            }
        }
        
        headers = {"Content-Type": "application/json;charset=utf-8"}
        response = requests.post(url=final_webhook, json=data, headers=headers, timeout=10)
        result = response.json()
        
        if result.get("errcode") == 0:
            print("✅ 钉钉推送成功")
            return True
        else:
            print(f"❌ 钉钉推送失败，错误：{result.get('errmsg')}")
            return False
    
    except Exception as e:
        print(f"❌ 钉钉推送异常：{str(e)}")
        return False

# -------------------------- 主程序（手动输入核心逻辑） --------------------------
if __name__ == "__main__":
    print("="*50)
    print("📈 手动股票分析工具（适配daily_stock_analysis系统）")
    print("="*50)
    
    # 1. 加载策略配置
    strategy_path = "stock_strategy.yml"
    if not os.path.exists(strategy_path):
        print(f"❌ 策略文件 {strategy_path} 不存在，请先放在仓库根目录")
        exit(1)
    
    with open(strategy_path, "r", encoding="utf-8") as f:
        strategy_config = f.read()
    
    # 2. 手动输入股票代码
    print("💡 请输入要分析的股票代码，多个代码用英文逗号分隔")
    print("示例：600519,000858,AAPL,hk00700")
    input_code = input("👉 股票代码：").strip()
    
    if not input_code:
        print("❌ 未输入任何股票代码，程序退出")
        exit(1)
    
    stock_codes = [code.strip() for code in input_code.split(",") if code.strip()]
    print(f"\n✅ 本次分析标的：{stock_codes}")
    print("-"*50)
    
    # 3. 批量分析股票
    full_report = ""
    success_count = 0
    
    for code in stock_codes:
        print(f"\n🔍 正在分析 {code}...")
        # 获取行情
        stock_info = get_stock_base_info(code)
        if stock_info.get("error"):
            full_report += f"❌ {code} 分析失败：{stock_info['error']}\n---\n"
            continue
        
        if stock_info["name"] == "未知":
            full_report += f"❌ {code} 未找到对应股票，请检查代码格式\n---\n"
            continue
        
        # 获取新闻
        news_content = get_stock_news(stock_info["name"], code, stock_info["market"])
        # 生成分析报告
        single_report = generate_analysis_report(stock_info, news_content, strategy_config)
        # 汇总
        full_report += single_report + "\n---\n"
        success_count += 1
        print(f"✅ {code}({stock_info['name']}) 分析完成")
    
    # 4. 输出结果&推送
    print("\n" + "="*50)
    print(f"📊 分析完成：成功{success_count}只，失败{len(stock_codes)-success_count}只")
    print("="*50)
    print("\n📋 完整分析报告：")
    print(full_report)
    
    # 推送到钉钉
    push_to_dingtalk(full_report, input_code)
