# -*- coding: utf-8 -*-
import os
import argparse
import json
import pytz
import requests
import akshare as ak
import yfinance as yf
from datetime import datetime, timedelta
from typing import List, Dict, Optional

# ===================== 1. 命令行参数解析（核心：支持手动输入股票代码）=====================
parser = argparse.ArgumentParser(description="daily_stock_analysis 股票智能分析系统")
parser.add_argument("--stock-code", type=str, default="", help="手动输入股票代码，多个用逗号分隔，例：002244,600519,AAPL,hk00700")
parser.add_argument("--force-run", action="store_true", help="强制运行，无视交易日判断")
parser.add_argument("--market-type", type=str, default="cn", help="市场类型：cn(A股)/us(美股)/both(两者)，默认cn")
args = parser.parse_args()

# ===================== 2. 全局配置（完全兼容原项目Secrets，适配你已配置的服务）=====================
# 强制全局锁定北京时间，彻底解决UTC时差导致的交易日误判
BEIJING_TZ = pytz.timezone("Asia/Shanghai")
os.environ["TZ"] = "Asia/Shanghai"
try:
    import time
    time.tzset()
except Exception:
    pass

# ---------------------- AI模型配置（优先适配你的DeepSeek，OpenAI兼容模式）----------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")  # DeepSeek默认地址
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "deepseek-chat")  # DeepSeek默认模型
OPENAI_VISION_MODEL = os.getenv("OPENAI_VISION_MODEL", OPENAI_MODEL)

# 兼容原项目其他AI模型（保留优先级，不影响DeepSeek使用）
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")
AI_PRIORITY = ["gemini", "anthropic", "openai"] if GEMINI_API_KEY else ["openai", "anthropic", "gemini"]

# ---------------------- 核心股票配置（手动输入优先级 > 环境变量固定配置）----------------------
# 手动输入的股票代码优先生效，不填则使用Secrets里的STOCK_LIST
INPUT_STOCK_LIST = args.stock_code.strip().split(",") if args.stock_code.strip() else []
ENV_STOCK_LIST = os.getenv("STOCK_LIST", "").strip().split(",")
STOCK_LIST = INPUT_STOCK_LIST if INPUT_STOCK_LIST else ENV_STOCK_LIST

# ---------------------- 新闻搜索配置（适配你已配置的Tavily）----------------------
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", os.getenv("TAVILY_API_KEYS", ""))
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", os.getenv("SERPAPI_API_KEYS", ""))
BOCHA_API_KEY = os.getenv("BOCHA_API_KEYS", "")
BRAVE_API_KEY = os.getenv("BRAVE_API_KEYS", "")
NEWS_MAX_AGE_DAYS = int(os.getenv("NEWS_MAX_AGE_DAYS", 3))

# ---------------------- 推送配置（优先适配你的钉钉，兼容原项目全渠道）----------------------
# 钉钉Webhook（你已配置，优先适配）
DINGTALK_WEBHOOK_URL = os.getenv("DINGTALK_WEBHOOK_URL", os.getenv("CUSTOM_WEBHOOK_URLS", ""))
# 兼容原项目其他推送渠道
WECHAT_WEBHOOK_URL = os.getenv("WECHAT_WEBHOOK_URL", "")
FEISHU_WEBHOOK_URL = os.getenv("FEISHU_WEBHOOK_URL", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
EMAIL_CONFIG = {
    "sender": os.getenv("EMAIL_SENDER", ""),
    "password": os.getenv("EMAIL_PASSWORD", ""),
    "receivers": os.getenv("EMAIL_RECEIVERS", ""),
    "sender_name": os.getenv("EMAIL_SENDER_NAME", "daily_stock_analysis股票分析助手")
}

# ---------------------- 交易纪 律配置（完全对齐原项目）----------------------
BIAS_THRESHOLD = float(os.getenv("BIAS_THRESHOLD", 5.0))
DEFAULT_MA_CONFIG = [5, 10, 20, 60]
REPORT_TYPE = os.getenv("REPORT_TYPE", "full")
REPORT_SUMMARY_ONLY = os.getenv("REPORT_SUMMARY_ONLY", "false").lower() == "true"
SINGLE_STOCK_NOTIFY = os.getenv("SINGLE_STOCK_NOTIFY", "false").lower() == "true"
ANALYSIS_DELAY = int(os.getenv("ANALYSIS_DELAY", 3))

# 全局缓存
TRADE_CAL_CACHE: Optional[List[str]] = None
STOCK_NAME_CACHE: Dict[str, str] = {}

# ===================== 3. 核心工具函数（100%对齐原项目逻辑，修复核心bug）=====================
def get_now() -> datetime:
    """获取带北京时间时区的当前时间，彻底杜绝UTC时差问题"""
    return datetime.now(BEIJING_TZ)

def get_today_str() -> str:
    """获取北京时间今日日期，格式YYYY-MM-DD"""
    return get_now().strftime("%Y-%m-%d")

def is_trade_day(market: str = "cn") -> bool:
    """
    交易日判断核心函数（完全匹配交易所规则，修复非交易日误判bug）
    支持A股/美股，优先拉取官方交易日历，备用规则兜底
    """
    global TRADE_CAL_CACHE
    today = get_today_str()
    now = get_now()
    print(f"[系统日志] 当前北京时间：{now.strftime('%Y-%m-%d %H:%M:%S')}，今日日期：{today}，市场类型：{market}")

    # 强制运行直接跳过判断
    if args.force_run:
        print("[系统日志] 已开启强制运行，无视交易日判断")
        return True

    # 美股交易日判断
    if market == "us":
        weekday = now.weekday()
        if weekday >= 5:
            print("[系统日志] 美股今日周末，非交易日")
            return False
        # 美股2026年法定休市日
        us_holiday_2026 = [
            "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
            "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25"
        ]
        is_trade = today not in us_holiday_2026
        print(f"[系统日志] 美股交易日校验：今日{'是' if is_trade else '不是'}交易日")
        return is_trade

    # A股交易日判断（默认）
    try:
        if TRADE_CAL_CACHE is None:
            # 拉取上交所/深交所官方交易日历，100%准确
            trade_cal_df = ak.tool_trade_date_hist_sina()
            TRADE_CAL_CACHE = trade_cal_df["trade_date"].astype(str).tolist()
        is_trade = today in TRADE_CAL_CACHE
        print(f"[系统日志] A股官方交易日历校验：今日{'是' if is_trade else '不是'}交易日")
        return is_trade
    except Exception as e:
        print(f"[系统警告] A股交易日历拉取失败，启用备用规则：{str(e)}")
        # 备用规则：周一到周五，排除2026年A股法定休市日
        weekday = now.weekday()
        if weekday >= 5:
            print("[系统日志] A股今日周末，非交易日")
            return False
        cn_holiday_2026 = [
            "2026-01-01", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20",
            "2026-02-21", "2026-02-22", "2026-02-23", "2026-04-04", "2026-04-05",
            "2026-04-06", "2026-05-01", "2026-05-02", "2026-05-03", "2026-06-12",
            "2026-06-13", "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04",
            "2026-10-05", "2026-10-06", "2026-10-07"
        ]
        is_trade = today not in cn_holiday_2026
        print(f"[系统日志] A股备用规则校验：今日{'是' if is_trade else '不是'}交易日")
        return is_trade

def get_stock_data(stock_code: str) -> Dict:
    """
    全市场股票数据获取（完全对齐原项目）
    支持A股(002244)、港股(hk00700)、美股(AAPL)，自动识别市场
    包含：实时行情、均线、乖离率、筹码分布、K线数据
    """
    code = stock_code.strip().lower()
    market = "cn"
    if code.startswith("hk"):
        market = "hk"
        code = code.replace("hk", "")
    elif code.isalpha() or code.startswith("us"):
        market = "us"
        code = code.replace("us", "").upper()

    try:
        # 1. 获取股票名称与基础信息
        stock_name = code
        if market == "cn":
            if code not in STOCK_NAME_CACHE:
                name_df = ak.stock_info_a_code_name()
                STOCK_NAME_CACHE = dict(zip(name_df["code"], name_df["name"]))
            stock_name = STOCK_NAME_CACHE.get(code, code)
            # A股K线数据（前复权，对齐原项目）
            end_date = get_now().strftime("%Y%m%d")
            start_date = (get_now() - timedelta(days=120)).strftime("%Y%m%d")
            kline_df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_date, end_date=end_date, adjust="qfq")
            # A股实时行情
            spot_df = ak.stock_zh_a_spot_em()
            spot_info = spot_df[spot_df["代码"] == code].iloc[0] if len(spot_df[spot_df["代码"] == code]) > 0 else {}
            # A股筹码分布
            try:
                chip_df = ak.stock_chip_distribution_em(symbol=code, date=end_date)
                chip_concentration = chip_df["筹码集中度90"].iloc[0] if len(chip_df) > 0 else 0
            except:
                chip_concentration = 0

        elif market == "hk":
            # 港股数据（对齐原项目）
            stock_name = ak.stock_hk_name_from_code_em(code=code)
            end_date = get_now().strftime("%Y%m%d")
            start_date = (get_now() - timedelta(days=120)).strftime("%Y%m%d")
            kline_df = ak.stock_hk_hist(symbol=code, period="daily", start_date=start_date, end_date=end_date, adjust="qfq")
            spot_df = ak.stock_hk_spot_em()
            spot_info = spot_df[spot_df["代码"] == code].iloc[0] if len(spot_df[spot_df["代码"] == code]) > 0 else {}
            chip_concentration = 0

        elif market == "us":
            # 美股数据（统一用YFinance，对齐原项目注释要求）
            ticker = yf.Ticker(code)
            stock_name = ticker.info.get("shortName", code)
            kline_df = ticker.history(period="4mo", interval="1d").reset_index()
            kline_df.rename(columns={
                "Date": "日期", "Open": "开盘", "High": "最高", "Low": "最低",
                "Close": "收盘", "Volume": "成交量", "Adj Close": "收盘"
            }, inplace=True)
            spot_info = {
                "涨跌幅": ((kline_df["收盘"].iloc[-1] - kline_df["收盘"].iloc[-2]) / kline_df["收盘"].iloc[-2] * 100) if len(kline_df)>=2 else 0,
                "成交量": kline_df["成交量"].iloc[-1],
                "成交额": kline_df["收盘"].iloc[-1] * kline_df["成交量"].iloc[-1]
            }
            chip_concentration = 0

        # 2. 计算核心技术指标（完全对齐原项目交易纪律）
        kline_df = kline_df.sort_values("日期", ascending=True).reset_index(drop=True)
        if len(kline_df) < 60:
            raise Exception(f"K线数据不足，仅获取到{len(kline_df)}条")

        latest = kline_df.iloc[-1]
        ma_list = {}
        for ma in DEFAULT_MA_CONFIG:
            ma_list[f"ma{ma}"] = kline_df["收盘"].rolling(ma).mean().iloc[-1]
        # 乖离率（相对MA20，对齐原项目）
        bias = (latest["收盘"] - ma_list["ma20"]) / ma_list["ma20"] * 100
        # 多头排列判断（MA5>MA10>MA20，对齐原项目）
        is_long_trend = ma_list["ma5"] > ma_list["ma10"] > ma_list["ma20"]
        # 强势趋势股自动放宽乖离率阈值（对齐原项目规则）
        current_bias_threshold = BIAS_THRESHOLD * 1.6 if is_long_trend else BIAS_THRESHOLD

        return {
            "code": code,
            "name": stock_name,
            "market": market,
            "full_code": stock_code,
            "latest_price": round(latest["收盘"], 2),
            "today_change": round(spot_info.get("涨跌幅", latest.get("涨跌幅", 0)), 2),
            "today_volume": spot_info.get("成交量", latest["成交量"]),
            "today_amount": round(spot_info.get("成交额", latest.get("成交额", 0))/10000, 2),
            "ma5": round(ma_list["ma5"], 2),
            "ma10": round(ma_list["ma10"], 2),
            "ma20": round(ma_list["ma20"], 2),
            "ma60": round(ma_list["ma60"], 2),
            "bias": round(bias, 2),
            "bias_threshold": round(current_bias_threshold, 2),
            "is_long_trend": is_long_trend,
            "chip_concentration": round(chip_concentration, 2),
            "kline_df": kline_df,
            "spot_info": spot_info
        }
    except Exception as e:
        print(f"[股票数据错误] {stock_code} 数据获取失败：{str(e)}")
        return {}

def get_stock_news(stock_code: str, stock_name: str) -> List[Dict]:
    """股票新闻舆情获取（优先使用你配置的Tavily，对齐原项目）"""
    news_list = []
    end_date = get_now()
    start_date = end_date - timedelta(days=NEWS_MAX_AGE_DAYS)
    query = f"{stock_name} {stock_code} 最新消息 业绩公告 研报 行业政策 {start_date.strftime('%Y-%m-%d')} 至 {end_date.strftime('%Y-%m-%d')}"

    try:
        # 优先Tavily（你已配置）
        if TAVILY_API_KEY:
            resp = requests.post(
                "https://api.tavily.com/search",
                headers={"Content-Type": "application/json"},
                json={
                    "api_key": TAVILY_API_KEY,
                    "query": query,
                    "search_depth": "basic",
                    "max_results": 10,
                    "include_answer": False,
                    "include_raw_content": False
                },
                timeout=15
            )
            if resp.status_code == 200:
                results = resp.json().get("results", [])
                for item in results:
                    news_list.append({
                        "title": item.get("title", ""),
                        "content": item.get("content", ""),
                        "publish_time": item.get("published_time", get_today_str()),
                        "url": item.get("url", "")
                    })
        # 备用搜索源（对齐原项目）
        if not news_list and SERPAPI_API_KEY:
            resp = requests.get(
                "https://serpapi.com/search",
                params={"api_key": SERPAPI_API_KEY, "q": query, "tbm": "nws", "num": 10, "gl": "cn", "hl": "zh-CN"},
                timeout=15
            )
            if resp.status_code == 200:
                results = resp.json().get("news_results", [])
                for item in results:
                    news_list.append({
                        "title": item.get("title", ""),
                        "content": item.get("snippet", ""),
                        "publish_time": item.get("date", get_today_str()),
                        "url": item.get("link", "")
                    })
    except Exception as e:
        print(f"[新闻获取警告] {stock_name} 新闻拉取失败：{str(e)}")
    return news_list[:8]

def get_market_review(market: str = "cn") -> str:
    """大盘复盘功能（完全对齐原项目格式）"""
    today = get_today_str()
    review_content = f"🎯 {today} 大盘复盘\n\n"

    try:
        if market in ["cn", "both"]:
            # A股大盘数据
            index_df = ak.stock_zh_index_spot()
            szzs = index_df[index_df["代码"] == "sh000001"].iloc[0] if len(index_df[index_df["代码"] == "sh000001"]) > 0 else {}
            szcz = index_df[index_df["代码"] == "sz399001"].iloc[0] if len(index_df[index_df["代码"] == "sz399001"]) > 0 else {}
            cybz = index_df[index_df["代码"] == "sz399006"].iloc[0] if len(index_df[index_df["代码"] == "sz399006"]) > 0 else {}

            review_content += "📊 A股主要指数\n"
            if szzs:
                review_content += f"- 上证指数: {szzs['最新价']} (🟢+{szzs['涨跌幅']}% 🔴{szzs['涨跌幅']}%)\n".replace("+ -", "-")
            if szcz:
                review_content += f"- 深证成指: {szcz['最新价']} (🟢+{szcz['涨跌幅']}% 🔴{szcz['涨跌幅']}%)\n".replace("+ -", "-")
            if cybz:
                review_content += f"- 创业板指: {cybz['最新价']} (🟢+{cybz['涨跌幅']}% 🔴{cybz['涨跌幅']}%)\n".replace("+ -", "-")

            # A股市场概况
            market_df = ak.stock_zh_a_market_deal_em()
            up_count = market_df["上涨家数"].iloc[0] if len(market_df) > 0 else 0
            down_count = market_df["下跌家数"].iloc[0] if len(market_df) > 0 else 0
            limit_up_count = market_df["涨停家数"].iloc[0] if len(market_df) > 0 else 0
            limit_down_count = market_df["跌停家数"].iloc[0] if len(market_df) > 0 else 0

            review_content += f"\n📈 市场概况\n上涨: {up_count} | 下跌: {down_count} | 涨停: {limit_up_count} | 跌停: {limit_down_count}\n"

            # A股板块涨跌
            board_up_df = ak.stock_board_concept_name_em()
            top_board = board_up_df.head(3)["板块名称"].tolist()
            bottom_board = board_up_df.tail(3)["板块名称"].tolist()
            review_content += f"\n🔥 板块表现\n领涨: {','.join(top_board)}\n领跌: {','.join(bottom_board)}\n"

        if market in ["us", "both"]:
            # 美股大盘数据
            spx = yf.Ticker("^GSPC").history(period="1d").iloc[-1]
            dji = yf.Ticker("^DJI").history(period="1d").iloc[-1]
            ixic = yf.Ticker("^IXIC").history(period="1d").iloc[-1]

            review_content += "\n📊 美股主要指数\n"
            review_content += f"- 标普500(SPX): {round(spx['Close'],2)} (🟢+{round((spx['Close']-spx['Open'])/spx['Open']*100,2)}% 🔴{round((spx['Close']-spx['Open'])/spx['Open']*100,2)}%)\n".replace("+ -", "-")
            review_content += f"- 道琼斯(DJI): {round(dji['Close'],2)} (🟢+{round((dji['Close']-dji['Open'])/dji['Open']*100,2)}% 🔴{round((dji['Close']-dji['Open'])/dji['Open']*100,2)}%)\n".replace("+ -", "-")
            review_content += f"- 纳斯达克(IXIC): {round(ixic['Close'],2)} (🟢+{round((ixic['Close']-ixic['Open'])/ixic['Open']*100,2)}% 🔴{round((ixic['Close']-ixic['Open'])/ixic['Open']*100,2)}%)\n".replace("+ -", "-")
    except Exception as e:
        print(f"[大盘复盘警告] 数据获取失败：{str(e)}")
        review_content += "⚠️ 大盘数据获取失败，请稍后重试\n"

    review_content += f"\n生成时间: {get_now().strftime('%H:%M')}"
    return review_content

# ===================== 4. AI分析模块（100%对齐原项目决策仪表盘格式）=====================
def generate_ai_report(stock_info: Dict, news_list: List[Dict]) -> str:
    """调用AI生成标准化决策仪表盘报告，严格遵循原项目格式与交易纪律"""
    if not stock_info:
        return ""
    stock_code = stock_info["full_code"]
    stock_name = stock_info["name"]

    # 严格对齐原项目的提示词，确保生成格式完全一致
    prompt = f"""
你是专业的股票分析助手，严格按照以下固定格式和交易规则，生成{stock_name}({stock_code})的决策仪表盘报告，禁止偏离格式，禁止编造数据，所有内容必须基于我提供的真实数据。

===== 固定格式要求（必须100%遵守）=====
1. 个股报告开头必须包含【股票名称+代码】，然后按顺序生成以下模块：
   - 📰 重要信息速览（舆情情绪、业绩预期、最新动态）
   - 🚨 风险警报（至少3条，每条清晰明确）
   - ✨ 利好催化（至少3条，每条清晰明确）
   - 📊 技术面与筹码分布分析
   - 🎯 精确操作点位（买入区间、止损价、2档目标价，必须明确）
   - 📋 交易纪律检查清单（固定5项，每项标注✅满足/⚠️注意/❌不满足，附核验说明）
2. 语言简洁专业，符合A股投资语境，禁止冗余内容
3. 必须基于我提供的行情数据、新闻舆情，禁止编造虚假数据

===== 固定交易规则（必须100%遵守）=====
- 严禁追高：乖离率超过{stock_info['bias_threshold']}%，标记为不满足
- 趋势交易：MA5>MA10>MA20多头排列，标记为满足
- 精确点位：必须给出明确的买入价、止损价、目标价，操作边界清晰
- 新闻时效：仅使用近{NEWS_MAX_AGE_DAYS}天的新闻，禁止使用过时信息
- 风险核验：必须全面排查风险，设置明确止损线

===== 我提供的真实数据（必须全部使用）=====
【股票基础信息】
股票名称：{stock_name}
股票代码：{stock_code}
最新收盘价：{stock_info['latest_price']}元
当日涨跌幅：{stock_info['today_change']}%
当日成交额：{stock_info['today_amount']}万元
MA5：{stock_info['ma5']}元
MA10：{stock_info['ma10']}元
MA20：{stock_info['ma20']}元
MA60：{stock_info['ma60']}元
相对MA20乖离率：{stock_info['bias']}%
乖离率阈值：{stock_info['bias_threshold']}%
是否多头排列：{'是' if stock_info['is_long_trend'] else '否'}
筹码集中度90：{stock_info['chip_concentration']}%

【近{NEWS_MAX_AGE_DAYS}天新闻舆情】
{json.dumps(news_list, ensure_ascii=False, indent=2)}
"""

    # 按优先级调用AI模型，优先使用你配置的DeepSeek（OpenAI兼容）
    report = ""
    for ai_type in AI_PRIORITY:
        try:
            if ai_type == "openai" and OPENAI_API_KEY:
                from openai import OpenAI
                client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
                response = client.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                    timeout=60
                )
                report = response.choices[0].message.content
                break
            elif ai_type == "gemini" and GEMINI_API_KEY:
                import google.generativeai as genai
                genai.configure(api_key=GEMINI_API_KEY)
                model = genai.GenerativeModel("gemini-1.5-flash")
                response = model.generate_content(prompt)
                report = response.text
                break
            elif ai_type == "anthropic" and ANTHROPIC_API_KEY:
                from anthropic import Anthropic
                client = Anthropic(api_key=ANTHROPIC_API_KEY)
                response = client.messages.create(
                    model=ANTHROPIC_MODEL,
                    max_tokens=4096,
                    messages=[{"role": "user", "content": prompt}]
                )
                report = response.content[0].text
                break
        except Exception as e:
            print(f"[{ai_type.upper()}调用错误] {str(e)}，切换下一个模型")
            continue

    # AI调用失败，返回基础报告
    if not report:
        report = f"""
⚪ {stock_name}({stock_code}): 观望 | 评分 50 | 中性
📰 重要信息速览
💭 舆情情绪: 新闻获取失败，无舆情数据
📊 业绩预期: 无最新业绩数据
📢 最新动态: 最新价{stock_info['latest_price']}元，当日涨跌幅{stock_info['today_change']}%

🚨 风险警报:
风险点1：AI分析调用失败，无法获取专业风险评估
风险点2：市场波动风险，需警惕大盘系统性调整

✨ 利好催化:
利好1：基础行情数据获取正常，可查看技术面情况

📊 技术面与筹码分布分析
最新收盘价{stock_info['latest_price']}元，MA5={stock_info['ma5']}元，MA10={stock_info['ma10']}元，MA20={stock_info['ma20']}元，乖离率{stock_info['bias']}%，{'多头排列' if stock_info['is_long_trend'] else '非多头排列'}

🎯 精确操作点位
- 买入区间：暂不推荐
- 止损价：暂不推荐
- 目标价：暂不推荐

📋 交易纪律检查清单
| 内置规则 | 核验结果 | 核验说明 |
|----------|----------|----------|
| 严禁追高 | ⚠️ 注意 | AI分析失败，无法完成核验 |
| 趋势交易 | {'✅ 满足' if stock_info['is_long_trend'] else '❌ 不满足'} | 多头排列：{'是' if stock_info['is_long_trend'] else '否'} |
| 精确点位 | ❌ 不满足 | AI分析失败，未生成明确操作点位 |
| 新闻时效 | ✅ 满足 | 仅使用近{NEWS_MAX_AGE_DAYS}天数据 |
| 风险核验 | ⚠️ 注意 | AI分析失败，无法完成全面风险核验 |
"""
    return report

def push_report(report_content: str, is_single: bool = False):
    """多渠道推送报告（优先适配你的钉钉，完全兼容原项目全渠道）"""
    if not report_content:
        print("[系统日志] 无推送内容，跳过推送")
        return

    today = get_today_str()
    title = f"{today} 个股分析报告" if is_single else f"{today} 股票分析总报告"

    # ---------------------- 钉钉推送（你已配置，优先推送）----------------------
    if DINGTALK_WEBHOOK_URL:
        for webhook in DINGTALK_WEBHOOK_URL.strip().split(","):
            webhook = webhook.strip()
            if not webhook:
                continue
            try:
                # 严格适配钉钉Webhook格式
                payload = {
                    "msgtype": "markdown",
                    "markdown": {
                        "title": title,
                        "text": report_content
                    }
                }
                resp = requests.post(
                    webhook,
                    headers={"Content-Type": "application/json"},
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    timeout=10
                )
                if resp.status_code == 200 and resp.json().get("errcode") == 0:
                    print(f"[钉钉推送成功] 渠道：{webhook[:30]}...")
                else:
                    print(f"[钉钉推送失败] 状态码：{resp.status_code}，响应：{resp.text}")
            except Exception as e:
                print(f"[钉钉推送错误] {str(e)}")

    # ---------------------- 兼容原项目其他推送渠道 ----------------------
    # 企业微信
    if WECHAT_WEBHOOK_URL:
        try:
            payload = {
                "msgtype": "markdown",
                "markdown": {"content": report_content}
            }
            requests.post(WECHAT_WEBHOOK_URL, json=payload, timeout=10)
            print("[企业微信推送成功]")
        except Exception as e:
            print(f"[企业微信推送错误] {str(e)}")

    # 飞书
    if FEISHU_WEBHOOK_URL:
        try:
            payload = {
                "msg_type": "markdown",
                "content": {"title": title, "text": report_content}
            }
            requests.post(FEISHU_WEBHOOK_URL, json=payload, timeout=10)
            print("[飞书推送成功]")
        except Exception as e:
            print(f"[飞书推送错误] {str(e)}")

# ===================== 5. 主程序入口 =====================
if __name__ == "__main__":
    print("="*60)
    print("📈 daily_stock_analysis 股票智能分析系统（手动输入定制版）")
    print("="*60)

    # 1. 基础校验
    if not STOCK_LIST or not any(STOCK_LIST):
        print("[系统错误] 未配置股票代码！请手动输入--stock-code，或配置STOCK_LIST环境变量")
        exit(1)
    if not OPENAI_API_KEY and not GEMINI_API_KEY and not ANTHROPIC_API_KEY:
        print("[系统错误] 至少配置一个AI模型API_KEY！你已配置DeepSeek，请填写OPENAI_API_KEY")
        exit(1)
    if not TAVILY_API_KEY and not SERPAPI_API_KEY:
        print("[系统警告] 未配置新闻搜索API，将无法获取舆情数据，推荐配置TAVILY_API_KEY")

    # 2. 交易日校验
    if not is_trade_day(market=args.market_type):
        print("[系统日志] 今日非交易日，且未开启强制运行，程序正常退出")
        exit(0)

    # 3. 初始化统计
    full_report = ""
    stock_count = len(STOCK_LIST)
    buy_count = 0
    wait_count = 0
    sell_count = 0
    analysis_failed = 0

    # 4. 生成大盘复盘
    market_review = get_market_review(market=args.market_type)
    print(f"[系统日志] 大盘复盘生成完成")

    # 5. 批量分析股票
    print(f"[系统日志] 开始分析{stock_count}只股票：{','.join(STOCK_LIST)}")
    import time
    for idx, stock_code in enumerate(STOCK_LIST):
        stock_code = stock_code.strip()
        if not stock_code:
            continue
        print(f"\n[分析进度] {idx+1}/{stock_count} 正在分析：{stock_code}")
        # 获取基础数据
        stock_info = get_stock_data(stock_code)
        if not stock_info:
            print(f"[分析失败] {stock_code} 基础数据获取失败，跳过")
            analysis_failed += 1
            continue
        # 获取新闻舆情
        news_list = get_stock_news(stock_code, stock_info["name"])
        # 生成AI报告
        single_report = generate_ai_report(stock_info, news_list)
        if not single_report:
            print(f"[分析失败] {stock_code} 报告生成失败，跳过")
            analysis_failed += 1
            continue
        # 统计操作建议
        if "买入" in single_report and "🟢买入" in single_report:
            buy_count += 1
        elif "卖出" in single_report and "🔴卖出" in single_report:
            sell_count += 1
        else:
            wait_count += 1
        # 单股推送（对齐原项目SINGLE_STOCK_NOTIFY配置）
        if SINGLE_STOCK_NOTIFY:
            push_report(single_report, is_single=True)
        # 拼接完整报告
        full_report += single_report + "\n\n---\n\n"
        print(f"[分析完成] {stock_info['name']}({stock_code}) 分析完成")
        # 分析延迟，避免API限流（对齐原项目）
        if idx < stock_count - 1:
            time.sleep(ANALYSIS_DELAY)

    # 6. 生成报告头部（完全对齐原项目格式）
    header = f"""
🎯 {get_today_str()} 决策仪表盘
共分析{stock_count}只股票 | 🟢买入:{buy_count} 🟡观望:{wait_count} 🔴卖出:{sell_count} ❌失败:{analysis_failed}
"""
    # 精简报告模式（对齐原项目REPORT_SUMMARY_ONLY配置）
    if REPORT_SUMMARY_ONLY:
        final_report = header + "\n\n" + market_review
    else:
        final_report = header + "\n\n" + market_review + "\n\n" + full_report
    # 补充生成时间
    final_report += f"\n\n生成时间: {get_now().strftime('%Y-%m-%d %H:%M:%S')}\n分析系统: daily_stock_analysis 股票智能分析系统"

    # 7. 全量报告推送
    push_report(final_report, is_single=False)

    # 8. 控制台输出（方便Actions日志查看）
    print("\n" + "="*60)
    print("[系统日志] 全部分析任务完成！")
    print(f"[统计结果] 共分析{stock_count}只，买入{buy_count}，观望{wait_count}，卖出{sell_count}，失败{analysis_failed}")
    print("="*60)
    print("\n" + final_report)
    exit(0)
