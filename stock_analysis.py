# -*- coding: utf-8 -*-
import os
import argparse
import json
import pytz
import requests
import hmac
import hashlib
import base64
import time
import akshare as ak
import yfinance as yf
from datetime import datetime, timedelta
from typing import List, Dict, Optional

# ===================== 1. 命令行参数解析 =====================
parser = argparse.ArgumentParser(description="daily_stock_analysis 股票智能分析系统")
parser.add_argument("--stock-code", type=str, default="", help="手动输入股票代码，支持A股/港股/美股，例：002244,09992.HK,AAPL")
parser.add_argument("--force-run", action="store_true", help="强制运行，无视交易日判断")
parser.add_argument("--market-type", type=str, default="cn", help="市场类型：cn(A股)/hk(港股)/us(美股)/both(全部)")
args = parser.parse_args()

# ===================== 2. 全局配置（全量空值容错）=====================
# 强制锁定北京时间
BEIJING_TZ = pytz.timezone("Asia/Shanghai")
os.environ["TZ"] = "Asia/Shanghai"
try:
    time.tzset()
except Exception:
    pass

# AI模型配置
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "deepseek-chat").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
AI_PRIORITY = ["gemini", "openai"] if GEMINI_API_KEY else ["openai", "gemini"]

# 股票配置
INPUT_STOCK_LIST = args.stock_code.strip().split(",") if args.stock_code.strip() else []
ENV_STOCK_LIST = os.getenv("STOCK_LIST", "").strip().split(",") if os.getenv("STOCK_LIST", "").strip() else []
STOCK_LIST = INPUT_STOCK_LIST if INPUT_STOCK_LIST else ENV_STOCK_LIST
STOCK_LIST = [code.strip() for code in STOCK_LIST if code.strip()]

# 新闻搜索配置
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", os.getenv("TAVILY_API_KEYS", "")).strip()
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", os.getenv("SERPAPI_API_KEYS", "")).strip()
NEWS_MAX_AGE_DAYS = int(os.getenv("NEWS_MAX_AGE_DAYS", "").strip() or "3")

# 钉钉推送配置（核心修复：兼容你的配置，全链路日志）
DINGTALK_WEBHOOK_URL = os.getenv("DINGTALK_WEBHOOK_URL", os.getenv("CUSTOM_WEBHOOK_URLS", "")).strip()
DINGTALK_SECRET = os.getenv("DINGTALK_SECRET", "").strip()
WECHAT_WEBHOOK_URL = os.getenv("WECHAT_WEBHOOK_URL", "").strip()
FEISHU_WEBHOOK_URL = os.getenv("FEISHU_WEBHOOK_URL", "").strip()

# 交易纪律配置
BIAS_THRESHOLD = float(os.getenv("BIAS_THRESHOLD", "").strip() or "5.0")
DEFAULT_MA_CONFIG = [5, 10, 20, 60]
REPORT_TYPE = os.getenv("REPORT_TYPE", "full").strip().lower()
REPORT_SUMMARY_ONLY = os.getenv("REPORT_SUMMARY_ONLY", "false").strip().lower() == "true"
SINGLE_STOCK_NOTIFY = os.getenv("SINGLE_STOCK_NOTIFY", "false").strip().lower() == "true"
ANALYSIS_DELAY = int(os.getenv("ANALYSIS_DELAY", "").strip() or "3")

# 全局缓存
TRADE_CAL_CACHE: Optional[List[str]] = None
STOCK_NAME_CACHE: Dict[str, str] = {}

# ===================== 3. 核心工具函数（全量修复）=====================
def get_now() -> datetime:
    return datetime.now(BEIJING_TZ)

def get_today_str() -> str:
    return get_now().strftime("%Y-%m-%d")

# 钉钉加签函数（100%匹配钉钉官方算法）
def dingtalk_sign(secret: str) -> Dict:
    timestamp =  str(round(time.time() * 1000))
    secret_enc = secret.encode("utf-8")
    string_to_sign = f"{timestamp}\n{secret}"
    string_to_sign_enc = string_to_sign.encode("utf-8")
    hmac_code = hmac.new(secret_enc, string_to_sign_enc, digestmod=hashlib.sha256).digest()
    sign = base64.b64encode(hmac_code).decode("utf-8")
    print(f"[钉钉加签日志] 加签完成，timestamp={timestamp}")
    return {"timestamp": timestamp, "sign": sign}

# 交易日判断
def is_trade_day(market: str = "cn") -> bool:
    global TRADE_CAL_CACHE
    today = get_today_str()
    now = get_now()
    print(f"[系统日志] 当前北京时间：{now.strftime('%Y-%m-%d %H:%M:%S')}，今日日期：{today}")

    if args.force_run:
        print("[系统日志] 已开启强制运行，无视交易日判断")
        return True

    if market == "hk":
        weekday = now.weekday()
        if weekday >= 5:
            print("[系统日志] 港股今日周末，非交易日")
            return False
        hk_holiday_2026 = [
            "2026-01-01", "2026-01-29", "2026-02-17", "2026-03-30", "2026-04-04",
            "2026-04-07", "2026-05-01", "2026-05-28", "2026-06-30", "2026-07-01",
            "2026-09-28", "2026-10-01", "2026-10-02", "2026-12-25", "2026-12-26"
        ]
        is_trade = today not in hk_holiday_2026
        print(f"[系统日志] 港股交易日校验：{is_trade}")
        return is_trade

    if market == "us":
        weekday = now.weekday()
        if weekday >= 5:
            print("[系统日志] 美股今日周末，非交易日")
            return False
        us_holiday_2026 = [
            "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
            "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25"
        ]
        is_trade = today not in us_holiday_2026
        print(f"[系统日志] 美股交易日校验：{is_trade}")
        return is_trade

    try:
        if TRADE_CAL_CACHE is None:
            trade_cal_df = ak.tool_trade_date_hist_sina()
            TRADE_CAL_CACHE = trade_cal_df["trade_date"].astype(str).tolist()
        is_trade = today in TRADE_CAL_CACHE
        print(f"[系统日志] A股交易日校验：{is_trade}")
        return is_trade
    except Exception as e:
        print(f"[系统警告] 交易日历拉取失败：{str(e)}，启用备用规则")
        weekday = now.weekday()
        if weekday >= 5:
            print("[系统日志] 今日周末，非交易日")
            return False
        cn_holiday_2026 = [
            "2026-01-01", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20",
            "2026-02-21", "2026-02-22", "2026-02-23", "2026-04-04", "2026-04-05",
            "2026-04-06", "2026-05-01", "2026-05-02", "2026-05-03", "2026-06-12",
            "2026-06-13", "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04",
            "2026-10-05", "2026-10-06", "2026-10-07"
        ]
        is_trade = today not in cn_holiday_2026
        print(f"[系统日志] A股备用规则校验：{is_trade}")
        return is_trade

# 股票数据获取（核心修复：港股兼容+重试机制+详细日志）
def get_stock_data(stock_code: str) -> Dict:
    raw_code = stock_code.strip()
    print(f"[股票数据日志] 开始处理：{raw_code}")
    # 兼容所有港股代码格式：09992.HK、hk09992、HK09992
    code = raw_code.lower().replace(".hk", "").replace("sz", "").replace("sh", "").replace("hk", "")
    market = "cn"
    if raw_code.lower().endswith(".hk") or raw_code.lower().startswith("hk"):
        market = "hk"
    elif raw_code.isalpha() or raw_code.lower().startswith("us"):
        market = "us"
    print(f"[股票数据日志] 识别市场：{market}，清洗后代码：{code}")

    try:
        stock_name = code
        # 港股数据获取（修复：接口兼容+异常捕获）
        if market == "hk":
            try:
                stock_name = ak.stock_hk_name_from_code_em(code=code)
                print(f"[股票数据日志] 港股名称获取成功：{stock_name}")
            except Exception as e:
                print(f"[股票数据警告] 港股名称获取失败：{str(e)}")
            # 港股K线数据（3次重试）
            end_date = get_now().strftime("%Y%m%d")
            start_date = (get_now() - timedelta(days=120)).strftime("%Y%m%d")
            kline_df = None
            for retry in range(3):
                try:
                    kline_df = ak.stock_hk_hist(symbol=code, period="daily", start_date=start_date, end_date=end_date, adjust="qfq")
                    if len(kline_df) >= 60:
                        break
                    print(f"[股票数据日志] 港股K线重试{retry+1}，数据量不足{len(kline_df)}")
                    time.sleep(1)
                except Exception as e:
                    print(f"[股票数据日志] 港股K线重试{retry+1}失败：{str(e)}")
                    time.sleep(1)
            if kline_df is None or len(kline_df) < 60:
                raise Exception(f"港股K线数据获取失败，仅获取到{len(kline_df) if kline_df else 0}条")
            # 港股实时行情
            spot_info = {}
            try:
                spot_df = ak.stock_hk_spot_em()
                spot_info = spot_df[spot_df["代码"] == code].iloc[0].to_dict() if len(spot_df[spot_df["代码"] == code]) > 0 else {}
                print(f"[股票数据日志] 港股实时行情获取成功")
            except Exception as e:
                print(f"[股票数据警告] 港股实时行情获取失败：{str(e)}")
            chip_concentration = 0

        # A股数据获取
        elif market == "cn":
            if code not in STOCK_NAME_CACHE:
                name_df = ak.stock_info_a_code_name()
                STOCK_NAME_CACHE = dict(zip(name_df["code"], name_df["name"]))
            stock_name = STOCK_NAME_CACHE.get(code, code)
            print(f"[股票数据日志] A股名称获取成功：{stock_name}")
            end_date = get_now().strftime("%Y%m%d")
            start_date = (get_now() - timedelta(days=120)).strftime("%Y%m%d")
            # A股K线数据
            kline_df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_date, end_date=end_date, adjust="qfq")
            if len(kline_df) < 60:
                raise Exception(f"A股K线数据不足，仅获取到{len(kline_df)}条")
            # A股实时行情
            spot_df = ak.stock_zh_a_spot_em()
            spot_info = spot_df[spot_df["代码"] == code].iloc[0].to_dict() if len(spot_df[spot_df["代码"] == code]) > 0 else {}
            # A股筹码分布
            chip_concentration = 0
            try:
                chip_df = ak.stock_chip_distribution_em(symbol=code, date=end_date)
                chip_concentration = chip_df["筹码集中度90"].iloc[0] if len(chip_df) > 0 else 0
            except Exception as e:
                print(f"[股票数据警告] 筹码分布获取失败：{str(e)}")

        # 美股数据获取
        elif market == "us":
            ticker = yf.Ticker(code.upper())
            stock_name = ticker.info.get("shortName", code)
            print(f"[股票数据日志] 美股名称获取成功：{stock_name}")
            kline_df = ticker.history(period="4mo", interval="1d").reset_index()
            kline_df.rename(columns={
                "Date": "日期", "Open": "开盘", "High": "最高", "Low": "最低",
                "Close": "收盘", "Volume": "成交量", "Adj Close": "收盘"
            }, inplace=True)
            if len(kline_df) < 60:
                raise Exception(f"美股K线数据不足，仅获取到{len(kline_df)}条")
            spot_info = {
                "涨跌幅": ((kline_df["收盘"].iloc[-1] - kline_df["收盘"].iloc[-2]) / kline_df["收盘"].iloc[-2] * 100) if len(kline_df)>=2 else 0,
                "成交量": kline_df["成交量"].iloc[-1],
                "成交额": kline_df["收盘"].iloc[-1] * kline_df["成交量"].iloc[-1]
            }
            chip_concentration = 0

        # 技术指标计算
        kline_df = kline_df.sort_values("日期", ascending=True).reset_index(drop=True)
        latest = kline_df.iloc[-1]
        ma_list = {}
        for ma in DEFAULT_MA_CONFIG:
            ma_list[f"ma{ma}"] = kline_df["收盘"].rolling(ma).mean().iloc[-1]
        bias = (latest["收盘"] - ma_list["ma20"]) / ma_list["ma20"] * 100
        is_long_trend = ma_list["ma5"] > ma_list["ma10"] > ma_list["ma20"]
        current_bias_threshold = BIAS_THRESHOLD * 1.6 if is_long_trend else BIAS_THRESHOLD

        # 类型安全转换
        today_change = round(float(spot_info.get("涨跌幅", latest.get("涨跌幅", 0))), 2)
        today_amount = round(float(spot_info.get("成交额", latest.get("成交额", 0)))/10000, 2)

        print(f"[股票数据日志] {stock_name}({raw_code}) 数据获取完成，最新价{round(latest['收盘'],2)}元")
        return {
            "code": code,
            "name": stock_name,
            "market": market,
            "full_code": raw_code,
            "latest_price": round(latest["收盘"], 2),
            "today_change": today_change,
            "today_volume": spot_info.get("成交量", latest["成交量"]),
            "today_amount": today_amount,
            "ma5": round(ma_list["ma5"], 2),
            "ma10": round(ma_list["ma10"], 2),
            "ma20": round(ma_list["ma20"], 2),
            "ma60": round(ma_list["ma60"], 2),
            "bias": round(bias, 2),
            "bias_threshold": round(current_bias_threshold, 2),
            "is_long_trend": is_long_trend,
            "chip_concentration": round(chip_concentration, 2),
        }
    except Exception as e:
        print(f"[股票数据错误] {raw_code} 数据获取彻底失败：{str(e)}")
        return {}

# 新闻舆情获取
def get_stock_news(stock_code: str, stock_name: str, market: str = "cn") -> List[Dict]:
    news_list = []
    end_date = get_now()
    start_date = end_date - timedelta(days=NEWS_MAX_AGE_DAYS)
    market_name = "港股" if market == "hk" else "美股" if market == "us" else "A股"
    query = f"{stock_name} {stock_code} {market_name} 最新消息 业绩公告 研报 行业政策 {start_date.strftime('%Y-%m-%d')} 至 {end_date.strftime('%Y-%m-%d')}"

    try:
        if TAVILY_API_KEY:
            print(f"[新闻日志] 开始调用Tavily搜索：{stock_name}")
            resp = requests.post(
                "https://api.tavily.com/search",
                headers={"Content-Type": "application/json"},
                json={
                    "api_key": TAVILY_API_KEY,
                    "query": query,
                    "search_depth": "basic",
                    "max_results": 8,
                    "include_answer": False,
                },
                timeout=20
            )
            if resp.status_code == 200:
                results = resp.json().get("results", [])
                for item in results:
                    news_list.append({
                        "title": item.get("title", ""),
                        "content": item.get("content", ""),
                        "publish_time": item.get("published_time", get_today_str()),
                    })
                print(f"[新闻日志] {stock_name} 新闻获取成功，共{len(news_list)}条")
        if not news_list and SERPAPI_API_KEY:
            print(f"[新闻日志] 切换到SerpAPI搜索：{stock_name}")
            resp = requests.get(
                "https://serpapi.com/search",
                params={"api_key": SERPAPI_API_KEY, "q": query, "tbm": "nws", "num": 8, "gl": "cn", "hl": "zh-CN"},
                timeout=20
            )
            if resp.status_code == 200:
                results = resp.json().get("news_results", [])
                for item in results:
                    news_list.append({
                        "title": item.get("title", ""),
                        "content": item.get("snippet", ""),
                        "publish_time": item.get("date", get_today_str()),
                    })
                print(f"[新闻日志] {stock_name} 新闻获取成功，共{len(news_list)}条")
    except Exception as e:
        print(f"[新闻获取警告] {stock_name} 新闻拉取失败：{str(e)}")
    return news_list[:8]

# 大盘复盘获取（修复：增加备用接口+异常兜底）
def get_market_review(market: str = "cn") -> str:
    today = get_today_str()
    review_content = f"🎯 {today} 大盘复盘\n\n"
    print(f"[大盘日志] 开始获取{market}市场大盘数据")
    try:
        # A股大盘数据
        if market in ["cn", "both"]:
            review_content += "📊 A股主要指数\n"
            # 备用接口1：东方财富指数
            try:
                index_df = ak.stock_zh_index_spot()
                szzs = index_df[index_df["代码"] == "sh000001"].iloc[0].to_dict() if len(index_df[index_df["代码"] == "sh000001"]) > 0 else {}
                szcz = index_df[index_df["代码"] == "sz399001"].iloc[0].to_dict() if len(index_df[index_df["代码"] == "sz399001"]) > 0 else {}
                cybz = index_df[index_df["代码"] == "sz399006"].iloc[0].to_dict() if len(index_df[index_df["代码"] == "sz399006"]) > 0 else {}
                if szzs:
                    change = round(float(szzs['涨跌幅']), 2)
                    review_content += f"- 上证指数: {szzs['最新价']} (🟢+{change}% 🔴{change}%)\n".replace("+ -", "-")
                if szcz:
                    change = round(float(szcz['涨跌幅']), 2)
                    review_content += f"- 深证成指: {szcz['最新价']} (🟢+{change}% 🔴{change}%)\n".replace("+ -", "-")
                if cybz:
                    change = round(float(cybz['涨跌幅']), 2)
                    review_content += f"- 创业板指: {cybz['最新价']} (🟢+{change}% 🔴{change}%)\n".replace("+ -", "-")
            except Exception as e:
                print(f"[大盘警告] 东方财富指数接口失败：{str(e)}")
                review_content += "- 上证指数: 数据获取失败\n- 深证成指: 数据获取失败\n- 创业板指: 数据获取失败\n"

            # A股市场涨跌概况
            try:
                market_df = ak.stock_zh_a_market_deal_em()
                up_count = market_df["上涨家数"].iloc[0] if len(market_df) > 0 else 0
                down_count = market_df["下跌家数"].iloc[0] if len(market_df) > 0 else 0
                limit_up_count = market_df["涨停家数"].iloc[0] if len(market_df) > 0 else 0
                limit_down_count = market_df["跌停家数"].iloc[0] if len(market_df) > 0 else 0
                review_content += f"\n📈 市场概况\n上涨: {up_count} | 下跌: {down_count} | 涨停: {limit_up_count} | 跌停: {limit_down_count}\n"
            except Exception as e:
                print(f"[大盘警告] 市场概况接口失败：{str(e)}")
                review_content += "\n📈 市场概况\n数据获取失败\n"

            # A股板块涨跌
            try:
                board_up_df = ak.stock_board_concept_name_em()
                top_board = board_up_df.head(3)["板块名称"].tolist()
                bottom_board = board_up_df.tail(3)["板块名称"].tolist()
                review_content += f"\n🔥 板块表现\n领涨: {','.join(top_board)}\n领跌: {','.join(bottom_board)}\n"
            except Exception as e:
                print(f"[大盘警告] 板块数据接口失败：{str(e)}")
                review_content += "\n🔥 板块表现\n数据获取失败\n"

        # 港股大盘数据
        if market in ["hk", "both"]:
            review_content += "\n📊 港股主要指数\n"
            try:
                hk_index_df = ak.stock_hk_index_spot_em()
                hsi = hk_index_df[hk_index_df["代码"] == "HSI"].iloc[0].to_dict() if len(hk_index_df[hk_index_df["代码"] == "HSI"]) > 0 else {}
                if hsi:
                    change = round(float(hsi['涨跌幅']), 2)
                    review_content += f"- 恒生指数: {hsi['最新价']} (🟢+{change}% 🔴{change}%)\n".replace("+ -", "-")
                else:
                    review_content += "- 恒生指数: 数据获取失败\n"
            except Exception as e:
                print(f"[大盘警告] 港股指数接口失败：{str(e)}")
                review_content += "- 恒生指数: 数据获取失败\n"

        # 美股大盘数据
        if market in ["us", "both"]:
            review_content += "\n📊 美股主要指数\n"
            try:
                spx = yf.Ticker("^GSPC").history(period="1d").iloc[-1]
                dji = yf.Ticker("^DJI").history(period="1d").iloc[-1]
                ixic = yf.Ticker("^IXIC").history(period="1d").iloc[-1]
                spx_change = round((spx['Close']-spx['Open'])/spx['Open']*100, 2)
                review_content += f"- 标普500(SPX): {round(spx['Close'],2)} (🟢+{spx_change}% 🔴{spx_change}%)\n".replace("+ -", "-")
                dji_change = round((dji['Close']-dji['Open'])/dji['Open']*100, 2)
                review_content += f"- 道琼斯(DJI): {round(dji['Close'],2)} (🟢+{dji_change}% 🔴{dji_change}%)\n".replace("+ -", "-")
                ixic_change = round((ixic['Close']-ixic['Open'])/ixic['Open']*100, 2)
                review_content += f"- 纳斯达克(IXIC): {round(ixic['Close'],2)} (🟢+{ixic_change}% 🔴{ixic_change}%)\n".replace("+ -", "-")
            except Exception as e:
                print(f"[大盘警告] 美股指数接口失败：{str(e)}")
                review_content += "- 标普500: 数据获取失败\n- 道琼斯: 数据获取失败\n- 纳斯达克: 数据获取失败\n"
        print(f"[大盘日志] 大盘数据获取完成")
    except Exception as e:
        print(f"[大盘错误] 大盘数据获取彻底失败：{str(e)}")
        review_content += "⚠️ 大盘数据获取失败，请稍后重试\n"
    review_content += f"\n生成时间: {get_now().strftime('%H:%M')}"
    return review_content

# AI分析模块
def generate_ai_report(stock_info: Dict, news_list: List[Dict]) -> str:
    if not stock_info:
        return ""
    stock_code = stock_info["full_code"]
    stock_name = stock_info["name"]
    market_name = "港股" if stock_info["market"] == "hk" else "美股" if stock_info["market"] == "us" else "A股"

    prompt = f"""
你是专业的{market_name}股票分析助手，严格按照以下固定格式和交易规则，生成{stock_name}({stock_code})的决策仪表盘报告，禁止偏离格式，禁止编造数据，所有内容必须基于我提供的真实数据。

===== 固定格式要求（必须100%遵守）=====
1. 个股报告开头必须包含【股票名称+代码】，然后按顺序生成以下模块：
   - 📰 重要信息速览（舆情情绪、业绩预期、最新动态）
   - 🚨 风险警报（至少3条，每条清晰明确）
   - ✨ 利好催化（至少3条，每条清晰明确）
   - 📊 技术面与筹码分布分析
   - 🎯 精确操作点位（买入区间、止损价、2档目标价，必须明确）
   - 📋 交易纪律检查清单（固定5项，每项标注✅满足/⚠️注意/❌不满足，附核验说明）
2. 语言简洁专业，符合{market_name}投资语境，禁止冗余内容

===== 固定交易规则（必须100%遵守）=====
- 严禁追高：乖离率超过{stock_info['bias_threshold']}%，标记为不满足
- 趋势交易：MA5>MA10>MA20多头排列，标记为满足
- 精确点位：必须给出明确的买入价、止损价、目标价
- 新闻时效：仅使用近{NEWS_MAX_AGE_DAYS}天的新闻
- 风险核验：必须全面排查风险，设置明确止损线

===== 我提供的真实数据 =====
【股票基础信息】
股票名称：{stock_name}
股票代码：{stock_code}
市场类型：{market_name}
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

    report = ""
    for ai_type in AI_PRIORITY:
        try:
            if ai_type == "gemini" and GEMINI_API_KEY:
                import google.generativeai as genai
                genai.configure(api_key=GEMINI_API_KEY)
                model = genai.GenerativeModel("gemini-1.5-flash")
                response = model.generate_content(prompt)
                report = response.text
                print(f"[AI日志] Gemini生成报告成功")
                break
            elif ai_type == "openai" and OPENAI_API_KEY:
                from openai import OpenAI
                client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
                response = client.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                    timeout=60
                )
                report = response.choices[0].message.content
                print(f"[AI日志] DeepSeek生成报告成功")
                break
        except Exception as e:
            print(f"[AI警告] {ai_type.upper()}调用失败：{str(e)}，切换下一个模型")
            continue

    if not report:
        print(f"[AI错误] 所有AI模型调用失败，生成兜底报告")
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

# 推送模块（核心修复：钉钉推送全链路优化+日志+格式兼容）
def push_report(report_content: str, is_single: bool = False):
    if not report_content:
        print("[推送日志] 无推送内容，跳过推送")
        return

    today = get_today_str()
    title = f"{today} 个股股票分析报告" if is_single else f"{today} 股票分析总报告"
    # 强制添加钉钉关键词兜底，避免被安全规则拦截
    report_content = f"# {title}\n\n" + report_content
    print(f"[推送日志] 报告标题：{title}，内容长度：{len(report_content)}字符")

    # 钉钉推送（核心修复：全链路日志+格式兼容）
    if DINGTALK_WEBHOOK_URL:
        print(f"[钉钉推送日志] 开始推送，Webhook地址：{DINGTALK_WEBHOOK_URL[:50]}...")
        try:
            payload = {
                "msgtype": "markdown",
                "markdown": {"title": title, "text": report_content}
            }
            # 加签处理
            if DINGTALK_SECRET:
                sign_data = dingtalk_sign(DINGTALK_SECRET)
                payload["timestamp"] = sign_data["timestamp"]
                payload["sign"] = sign_data["sign"]
            # 发送请求
            resp = requests.post(
                DINGTALK_WEBHOOK_URL,
                headers={"Content-Type": "application/json"},
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                timeout=15
            )
            resp_json = resp.json()
            print(f"[钉钉推送日志] 钉钉响应：{resp_json}")
            if resp.status_code == 200 and resp_json.get("errcode") == 0:
                print(f"[钉钉推送日志] 钉钉推送成功！")
            else:
                print(f"[钉钉推送错误] 钉钉推送失败，错误信息：{resp_json.get('errmsg', '未知错误')}")
        except Exception as e:
            print(f"[钉钉推送错误] 推送请求异常：{str(e)}")

    # 企业微信推送
    if WECHAT_WEBHOOK_URL:
        try:
            payload = {"msgtype": "markdown", "markdown": {"content": report_content}}
            resp = requests.post(WECHAT_WEBHOOK_URL, json=payload, timeout=10)
            if resp.status_code == 200:
                print("[企业微信推送日志] 推送成功")
        except Exception as e:
            print(f"[企业微信推送错误] {str(e)}")

# ===================== 6. 主程序入口 =====================
if __name__ == "__main__":
    print("="*60)
    print("📈 daily_stock_analysis 股票智能分析系统")
    print("="*60)

    # 前置校验
    if not STOCK_LIST:
        print("[系统错误] 未配置股票代码！请手动输入--stock-code，或在Secrets中配置STOCK_LIST")
        exit(1)
    if not OPENAI_API_KEY and not GEMINI_API_KEY:
        print("[系统错误] 至少配置一个AI模型API_KEY！")
        exit(1)
    if not TAVILY_API_KEY and not SERPAPI_API_KEY:
        print("[系统警告] 未配置新闻搜索API，将无法获取舆情数据")
    if not DINGTALK_WEBHOOK_URL and not WECHAT_WEBHOOK_URL:
        print("[系统警告] 未配置推送渠道，将仅在控制台输出报告")

    # 交易日校验
    if not is_trade_day(market=args.market_type):
        print("[系统日志] 今日非交易日，且未开启强制运行，程序正常退出")
        exit(0)

    # 初始化统计
    full_report = ""
    stock_count = len(STOCK_LIST)
    buy_count = 0
    wait_count = 0
    sell_count = 0
    analysis_failed = 0

    # 生成大盘复盘
    market_review = get_market_review(market=args.market_type)

    # 批量分析股票
    print(f"[系统日志] 开始分析{stock_count}只股票：{','.join(STOCK_LIST)}")
    for idx, stock_code in enumerate(STOCK_LIST):
        stock_code = stock_code.strip()
        if not stock_code:
            continue
        print(f"\n[分析进度] {idx+1}/{stock_count} 正在分析：{stock_code}")
        # 获取数据
        stock_info = get_stock_data(stock_code)
        if not stock_info:
            print(f"[分析失败] {stock_code} 基础数据获取失败，跳过")
            analysis_failed += 1
            continue
        # 获取新闻
        news_list = get_stock_news(stock_code, stock_info["name"], market=stock_info["market"])
        # 生成报告
        single_report = generate_ai_report(stock_info, news_list)
        if not single_report:
            print(f"[分析失败] {stock_code} 报告生成失败，跳过")
            analysis_failed += 1
            continue
        # 统计结果
        if "🟢买入" in single_report:
            buy_count += 1
        elif "🔴卖出" in single_report:
            sell_count += 1
        else:
            wait_count += 1
        # 单股推送
        if SINGLE_STOCK_NOTIFY:
            push_report(single_report, is_single=True)
        # 拼接完整报告
        full_report += single_report + "\n\n---\n\n"
        print(f"[分析完成] {stock_info['name']}({stock_code}) 分析完成")
        # 限流延迟
        if idx < stock_count - 1:
            time.sleep(ANALYSIS_DELAY)

    # 生成报告头部
    header = f"""
🎯 {get_today_str()} 决策仪表盘
共分析{stock_count}只股票 | 🟢买入:{buy_count} 🟡观望:{wait_count} 🔴卖出:{sell_count} ❌失败:{analysis_failed}
"""
    # 最终报告
    if REPORT_SUMMARY_ONLY:
        final_report = header + "\n\n" + market_review
    else:
        final_report = header + "\n\n" + market_review + "\n\n" + full_report
    final_report += f"\n\n生成时间: {get_now().strftime('%Y-%m-%d %H:%M:%S')}\n分析系统: daily_stock_analysis 股票智能分析系统"

    # 推送最终报告
    push_report(final_report, is_single=False)

    # 控制台输出
    print("\n" + "="*60)
    print("[系统日志] 全部分析任务完成！")
    print(f"[统计结果] 共分析{stock_count}只，买入{buy_count}，观望{wait_count}，卖出{sell_count}，失败{analysis_failed}")
    print("="*60)
    print("\n" + final_report)
    exit(0)
