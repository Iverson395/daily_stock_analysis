# -*- coding: utf-8 -*-
"""
手动选股分析脚本（兼容 daily_stock_analysis 原项目框架）
功能：手动输入股票代码，即时生成AI分析报告，自动推送到钉钉
已适配：DeepSeek API、Tavily 新闻搜索、钉钉推送（含加签兼容）
"""
import os
import re
import time
import yaml
import json
import hmac
import hashlib
import base64
import urllib.parse
import akshare as ak
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from dotenv import load_dotenv
from openai import OpenAI
from tavily import TavilyClient
import requests

# -------------------------- 基础初始化（兼容原项目） --------------------------
# 加载原项目.env环境变量（与GitHub Actions Secrets完全兼容）
load_dotenv()

# 读取配置文件
with open("stock.yml", "r", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

# 全局调试开关
DEBUG = CONFIG["base"]["debug"]

# -------------------------- 环境变量加载（复用你已配置的内容） --------------------------
# DeepSeek API配置（OpenAI兼容格式，与原项目完全一致）
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")

# Tavily 新闻搜索配置（复用你已配置的key）
TAVILY_API_KEY = os.getenv("TAVILY_API_KEYS", "").split(",")[0].strip()

# 钉钉推送配置（复用你已配置的Webhook，支持加签）
DINGTALK_WEBHOOK = os.getenv("CUSTOM_WEBHOOK_URLS", "").split(",")[0].strip()
DINGTALK_SECRET = os.getenv("DINGTALK_SECRET", "")  # 加签模式必填，关键词模式可不填

# -------------------------- 工具函数（与原项目逻辑对齐） --------------------------
def debug_log(msg: str):
    """调试日志打印"""
    if DEBUG:
        print(f"[DEBUG] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {msg}")

def parse_stock_code(code: str) -> tuple:
    """
    解析股票代码，自动识别市场（与原项目格式完全兼容）
    支持格式：A股600519、港股hk00700、美股AAPL
    返回：(标准化代码, 市场类型, 代码后缀)
    """
    code = code.strip().upper()
    # 港股识别
    if code.startswith("HK"):
        stock_code = code[2:] if len(code) > 2 else code
        return code, "hk", f"{stock_code}.HK"
    # 美股识别（非数字开头）
    elif not re.match(r"^\d{6}$", code):
        return code, "us", code
    # A股识别（6位数字）
    else:
        return code, "cn", code

def get_stock_base_info(code: str, market: str) -> tuple:
    """获取股票基础信息+K线数据+技术指标（与原项目交易纪律对齐）"""
    debug_log(f"正在获取【{code}】行情数据，市场：{market}")
    kline_days = CONFIG["base"]["kline_days"]
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=kline_days)).strftime("%Y%m%d")

    try:
        # A股行情（akshare，与原项目数据源一致）
        if market == "cn":
            # 获取K线数据
            df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_date, end_date=end_date, adjust="qfq")
            # 获取股票名称
            name_df = ak.stock_info_a_code_name()
            stock_name = name_df[name_df["code"] == code]["name"].values[0]
            df = df.sort_values("日期", ascending=True).reset_index(drop=True)

        # 港股/美股行情（yfinance，与原项目数据源一致）
        else:
            ticker = yf.Ticker(code)
            df = ticker.history(start=start_date, end=end_date, interval="1d")
            df = df.reset_index()
            df.rename(columns={
                "Date": "日期", "Open": "开盘", "High": "最高", "Low": "最低 ",
                "Close": "收盘", "Volume": "成交量"
            }, inplace=True)
            stock_name = ticker.info.get("shortName", code)
            df = df.sort_values("日期", ascending=True).reset_index(drop=True)

        # 数据校验
        if df.empty:
            raise Exception("未获取到K线数据，请检查股票代码是否正确")

        # 计算技术指标（与原项目交易纪律完全对齐）
        ma_short = CONFIG["technical"]["ma_periods"]["short"]
        ma_mid = CONFIG["technical"]["ma_periods"]["mid"]
        ma_long = CONFIG["technical"]["ma_periods"]["long"]

        df[f"MA{ma_short}"] = df["收盘"].rolling(ma_short).mean()
        df[f"MA{ma_mid}"] = df["收盘"].rolling(ma_mid).mean()
        df[f"MA{ma_long}"] = df["收盘"].rolling(ma_long).mean()

        # 最新数据提取
        latest = df.iloc[-1]
        ma5 = round(latest[f"MA{ma_short}"], 2)
        ma10 = round(latest[f"MA{ma_mid}"], 2)
        ma20 = round(latest[f"MA{ma_long}"], 2)
        current_price = round(latest["收盘"], 2)
        trade_date = latest["日期"].strftime("%Y-%m-%d") if hasattr(latest["日期"], "strftime") else str(latest["日期"])

        # 乖离率计算（与原项目追高风险判断对齐）
        bias = round(((current_price - ma5) / ma5) * 100, 2)

        # 趋势判断
        long_rule = CONFIG["trading_rules"]["long_trend_rule"]
        short_rule = CONFIG["trading_rules"]["short_trend_rule"]
        if ma5 > ma10 > ma20:
            trend = "多头排列（看多）"
        elif ma5 < ma10 < ma20:
            trend = "空头排列（看空）"
        else:
            trend = "震荡趋势（中性）"

        # 量能变化
        volume_period = CONFIG["technical"]["volume_period"]
        latest_volume = latest["成交量"]
        avg_volume = df["成交量"].tail(volume_period).mean()
        volume_change = "放量" if latest_volume > avg_volume * 1.2 else "缩量" if latest_volume < avg_volume * 0.8 else "量能平稳"

        # 近期高低点
        high_20 = round(df["最高"].tail(20).max(), 2)
        low_20 = round(df["最低"].tail(20).min(), 2)

        # 组装基础信息
        base_info = {
            "stock_name": stock_name,
            "stock_code": code,
            "market": market,
            "current_price": current_price,
            "trade_date": trade_date,
            "ma5": ma5,
            "ma10": ma10,
            "ma20": ma20,
            "bias": bias,
            "trend": trend,
            "volume_change": volume_change,
            "high_20": high_20,
            "low_20": low_20,
            "bias_threshold": CONFIG["technical"]["bias_threshold"],
            "strong_bias_threshold": CONFIG["technical"]["strong_bias_threshold"],
            "max_age_days": CONFIG["news"]["max_age_days"],
            "generate_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        debug_log(f"【{code}】行情数据获取成功，当前价格：{current_price}元，趋势：{trend}")
        return base_info, df

    except Exception as e:
        print(f"❌ 【{code}】行情数据获取失败：{str(e)}")
        return None, None

def get_stock_news(stock_name: str, stock_code: str, market: str) -> str:
    """获取股票最新舆情新闻（Tavily，与原项目逻辑对齐）"""
    debug_log(f"正在搜索【{stock_name}({stock_code})】最新新闻")
    if not TAVILY_API_KEY:
        debug_log("未配置Tavily API Key，跳过新闻搜索")
        return "无可用新闻数据，未配置Tavily API Key"

    try:
        tavily_client = TavilyClient(api_key=TAVILY_API_KEY)
        # 搜索关键词模板（与原项目一致）
        search_template = CONFIG["news"]["search_template"]
        query = search_template.format(stock_name=stock_name, code=stock_code)
        # 搜索语言适配
        search_lang = CONFIG["news"]["search_lang"] if market != "us" else "en"

        # 执行搜索（仅获取3天内新闻，与原项目时效一致）
        response = tavily_client.search(
            query=query,
            search_depth="basic",
            max_results=CONFIG["news"]["news_limit"],
            days=CONFIG["news"]["max_age_days"],
            language=search_lang
        )

        # 整理新闻内容
        news_list = response.get("results", [])
        if not news_list:
            return f"近{CONFIG['news']['max_age_days']}天暂无相关新闻"

        news_content = ""
        for idx, news in enumerate(news_list, 1):
            publish_time = news.get("published_time", "未知时间")
            title = news.get("title", "无标题")
            content = news.get("content", "无内容")[:200]  # 限制单条新闻长度
            news_content += f"{idx}. 【{publish_time}】{title}\n   摘要：{content}\n"

        debug_log(f"【{stock_name}】新闻搜索完成，共获取{len(news_list)}条新闻")
        return news_content

    except Exception as e:
        print(f"❌ 【{stock_name}】新闻搜索失败：{str(e)}")
        return "新闻搜索失败，跳过舆情分析"

def generate_ai_analysis(base_info: dict, news_content: str) -> str:
    """调用DeepSeek生成AI分析报告（与原项目决策仪表盘格式完全一致）"""
    debug_log(f"正在生成【{base_info['stock_name']}】AI分析报告")
    if not OPENAI_API_KEY:
        raise Exception("未配置DeepSeek API Key，请检查OPENAI_API_KEY环境变量")

    try:
        # 初始化OpenAI客户端（DeepSeek兼容）
        client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
        # 填充Prompt模板（与原项目格式完全对齐）
        prompt = CONFIG["ai"]["prompt_template"].format(**base_info, news_content=news_content)

        # 调用DeepSeek API
        response = client.chat.completions.create(
            model=CONFIG["ai"]["model_name"],
            messages=[{"role": "user", "content": prompt}],
            temperature=CONFIG["ai"]["temperature"],
            max_tokens=CONFIG["ai"]["max_tokens"],
            timeout=CONFIG["ai"]["timeout"]
        )

        report = response.choices[0].message.content.strip()
        debug_log(f"【{base_info['stock_name']}】AI分析报告生成完成")
        return report

    except Exception as e:
        print(f"❌ AI分析生成失败：{str(e)}")
        return None

def dingtalk_sign(secret: str) -> tuple:
    """钉钉加签算法（官方标准，解决加签模式推送失败问题）"""
    timestamp = str(round(time.time() * 1000))
    secret_enc = secret.encode('utf-8')
    string_to_sign = f"{timestamp}\n{secret}"
    string_to_sign_enc = string_to_sign.encode('utf-8')
    hmac_code = hmac.new(secret_enc, string_to_sign_enc, digestmod=hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    return timestamp, sign

def push_to_dingtalk(report: str, stock_name: str, stock_code: str) -> bool:
    """推送报告到钉钉（官方标准格式，兼容关键词/加签模式）"""
    if not CONFIG["push"]["enable_push"] or not DINGTALK_WEBHOOK:
        debug_log("钉钉推送已关闭或未配置Webhook，跳过推送")
        return False

    debug_log(f"正在推送【{stock_name}({stock_code})】分析报告到钉钉")
    try:
        # 处理加签
        final_url = DINGTALK_WEBHOOK
        if DINGTALK_SECRET:
            timestamp, sign = dingtalk_sign(DINGTALK_SECRET)
            final_url = f"{DINGTALK_WEBHOOK}&timestamp={timestamp}&sign={sign}"

        # 钉钉官方标准消息格式（解决之前推送失败的核心）
        msg_type = CONFIG["push"]["msg_type"]
        title = f"{CONFIG['push']['title']} - {stock_name}({stock_code})"

        if msg_type == "markdown":
            data = {
                "msgtype": "markdown",
                "markdown": {
                    "title": title,
                    "text": report
                }
            }
        else:
            data = {
                "msgtype": "text",
                "text": {
                    "content": f"{title}\n\n{report}"
                }
            }

        # 发送请求
        headers = {"Content-Type": "application/json;charset=utf-8"}
        response = requests.post(url=final_url, json=data, headers=headers, timeout=10)
        result = response.json()

        # 打印返回结果（方便排查问题）
        print("===== 钉钉推送接口返回结果 =====")
        print(response.text)
        print("==================================")

        if result.get("errcode") == 0:
            print(f"✅ 【{stock_name}({stock_code})】钉钉推送成功")
            return True
        else:
            print(f"❌ 钉钉推送失败，错误码：{result.get('errcode')}，原因：{result.get('errmsg')}")
            return False

    except Exception as e:
        print(f"❌ 钉钉推送异常：{str(e)}")
        return False

def save_report_local(report: str, stock_code: str):
    """保存分析报告到本地（可选）"""
    save_path = CONFIG["base"]["report_save_path"]
    if not save_path:
        return

    # 创建目录
    if not os.path.exists(save_path):
        os.makedirs(save_path)

    # 保存文件
    file_name = f"{save_path}/{stock_code}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    with open(file_name, "w", encoding="utf-8") as f:
        f.write(report)
    debug_log(f"报告已保存到本地：{file_name}")

# -------------------------- 主程序入口 --------------------------
def main():
    print("="*50)
    print("📈 股票智能手动分析系统（兼容原项目框架）")
    print("="*50)

    # 1. 获取用户输入的股票代码
    import sys
    # 支持命令行传参（例：python manual_stock_analysis.py 600519,AAPL,hk00700）
    if len(sys.argv) > 1:
        input_codes = sys.argv[1].strip()
    else:
        # 手动输入模式
        input_codes = input("请输入股票代码（多个用英文逗号分隔，支持A股/港股/美股）：").strip()

    if not input_codes:
        print("❌ 未输入任何股票代码，程序退出")
        return

    code_list = [code.strip() for code in input_codes.split(",") if code.strip()]
    print(f"\n📋 待分析股票列表：{code_list}")
    print(f"📊 共 {len(code_list)} 只股票，开始分析...\n")

    # 2. 批量分析股票
    success_count = 0
    for code in code_list:
        print("-"*50)
        print(f"🔍 开始分析：{code}")

        # 2.1 解析股票代码
        std_code, market, _ = parse_stock_code(code)
        # 2.2 获取行情数据
        base_info, _ = get_stock_base_info(std_code, market)
        if not base_info:
            continue
        # 2.3 获取新闻舆情
        news_content = get_stock_news(base_info["stock_name"], std_code, market)
        # 2.4 生成AI分析报告
        report = generate_ai_analysis(base_info, news_content)
        if not report:
            continue
        # 2.5 打印报告
        print("\n" + "="*30 + " 分析报告 " + "="*30)
        print(report)
        print("="*70 + "\n")
        # 2.6 保存本地
        save_report_local(report, std_code)
        # 2.7 钉钉推送
        push_to_dingtalk(report, base_info["stock_name"], std_code)

        success_count += 1
        # 避免API限流，添加延迟（与原项目一致）
        if len(code_list) > 1 and CONFIG["base"]["debug"]:
            delay = os.getenv("ANALYSIS_DELAY", 3)
            time.sleep(int(delay))

    # 3. 分析完成总结
    print("-"*50)
    print(f"\n🎉 分析完成！成功分析 {success_count}/{len(code_list)} 只股票")
    print("💡 若钉钉推送失败，请查看上方接口返回结果，对照之前的排查指南解决")

if __name__ == "__main__":
    main()
