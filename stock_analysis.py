import sys
import os
import requests
import pandas as pd
import akshare as ak
import yfinance as yf
from datetime import datetime, timedelta
from openai import OpenAI
from tavily import TavilyClient

# ===================== 1. 核心参数读取（和yml完全匹配，解决报错）=====================
if __name__ == "__main__":
    # 优先级1：手动运行时输入的股票代码（即时分析用）
    input_stock = sys.argv[1] if len(sys.argv) > 1 else ""
    # 优先级2：Secrets里配置的批量股票列表
    secret_stock = os.getenv("STOCK_LIST", "")
    
    # 解析最终股票列表，都没有就弹出提示退出
    stock_list = []
    if input_stock.strip():
        stock_list = [s.strip() for s in input_stock.split(",") if s.strip()]
    elif secret_stock.strip():
        stock_list = [s.strip() for s in secret_stock.split(",") if s.strip()]
    else:
        print("📌 股票分析提示")
        print("您未手动输入股票代码，也未在GitHub Secrets中配置STOCK_LIST，请按以下方式操作：")
        print("1. 单只股票分析：触发运行时，在输入框中填写完整股票代码（例：601777.SH）")
        print("2. 批量股票分析：在GitHub Secrets中新建STOCK_LIST，填写多只股票代码，用英文逗号分隔（例：601777.SH,000001.SZ）")
        print("\n📌 股市有风险，投资需谨慎。")
        sys.exit(1)
    print(f"✅ 成功获取待分析股票列表：{stock_list}")

    # ===================== 2. 环境配置初始化（你的DeepSeek/钉钉/Tavily）=====================
    # 2.1 DeepSeek AI模型初始化（OpenAI兼容格式）
    try:
        client = OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
        )
        ai_model = os.getenv("OPENAI_MODEL", "deepseek-chat")
        print("✅ DeepSeek AI模型初始化成功")
    except Exception as e:
        print(f"❌ DeepSeek初始化失败：{e}")
        sys.exit(1)

    # 2.2 Tavily新闻搜索初始化
    try:
        tavily_client = TavilyClient(api_key=os.getenv("TAVILY_API_KEYS").split(",")[0])
        news_max_days = int(os.getenv("NEWS_MAX_AGE_DAYS", 3))
        print("✅ Tavily新闻搜索初始化成功")
    except Exception as e:
        print(f"❌ Tavily初始化失败：{e}")
        sys.exit(1)

    # 2.3 钉钉推送配置
    dingtalk_webhooks = os.getenv("CUSTOM_WEBHOOK_URLS", "").split(",")
    dingtalk_enabled = len(dingtalk_webhooks) > 0 and dingtalk_webhooks[0].strip() != ""
    if dingtalk_enabled:
        print("✅ 钉钉推送配置成功")
    else:
        print("⚠️  未配置钉钉推送，仅输出分析结果")

    # 2.4 交易纪律参数
    bias_threshold = float(os.getenv("BIAS_THRESHOLD", 5.0))
    print(f"✅ 交易纪律参数加载完成，乖离率阈值：{bias_threshold}%")

    # ===================== 3. 核心功能函数（开源系统核心能力）=====================
    # 3.1 获取股票行情与技术面数据
    def get_stock_data(stock_code):
        """兼容A股/港股/美股，获取K线、均线、乖离率等核心数据"""
        try:
            # A股处理（格式：601777.SH/000001.SZ）
            if ".SH" in stock_code or ".SZ" in stock_code:
                code = stock_code.split(".")[0]
                df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=(datetime.now()-timedelta(days=60)).strftime("%Y%m%d"), end_date=datetime.now().strftime("%Y%m%d"), adjust="qfq")
                df = df.sort_values("日期", ascending=True).reset_index(drop=True)
                current_price = df["收盘"].iloc[-1]
                stock_name = ak.stock_individual_info_em(symbol=code).loc[ak.stock_individual_info_em(symbol=code)["item"]=="股票名称", "value"].values[0]
            
            # 港股处理（格式：hk00700）
            elif stock_code.startswith("hk") or stock_code.startswith("HK"):
                code = stock_code.replace("hk", "").replace("HK", "")
                df = ak.stock_hk_hist(symbol=code, period="daily", start_date=(datetime.now()-timedelta(days=60)).strftime("%Y%m%d"), end_date=datetime.now().strftime("%Y%m%d"), adjust="qfq")
                df = df.sort_values("日期", ascending=True).reset_index(drop=True)
                current_price = df["收盘"].iloc[-1]
                stock_name = f"港股{code}"
            
            # 美股处理（格式：AAPL/TSLA）
            else:
                ticker = yf.Ticker(stock_code)
                df = ticker.history(period="60d", interval="1d")
                df = df.reset_index()
                df.columns = [col.lower() for col in df.columns]
                current_price = df["close"].iloc[-1]
                stock_name = ticker.info.get("shortName", stock_code)

            # 计算核心技术指标
            df["ma5"] = df["收盘" if "收盘" in df.columns else "close"].rolling(5).mean()
            df["ma10"] = df["收盘" if "收盘" in df.columns else "close"].rolling(10).mean()
            df["ma20"] = df["收盘" if "收盘" in df.columns else "close"].rolling(20).mean()
            latest = df.iloc[-1]
            
            # 乖离率计算
            bias = (current_price - latest["ma20"]) / latest["ma20"] * 100
            # 多头排列判断
            trend_up = latest["ma5"] > latest["ma10"] > latest["ma20"]
            # 支撑压力位
            support = latest["ma20"]
            pressure = df["最高" if "最高" in df.columns else "high"].iloc[-10:].max()

            return {
                "name": stock_name,
                "code": stock_code,
                "current_price": round(current_price, 2),
                "ma5": round(latest["ma5"], 2),
                "ma10": round(latest["ma10"], 2),
                "ma20": round(latest["ma20"], 2),
                "bias": round(bias, 2),
                "trend_up": trend_up,
                "support": round(support, 2),
                "pressure": round(pressure, 2),
                "change": round((current_price - df["收盘" if "收盘" in df.columns else "close"].iloc[-2])/df["收盘" if "收盘" in df.columns else "close"].iloc[-2]*100, 2)
            }
        except Exception as e:
            print(f"❌ 获取{stock_code}数据失败：{e}")
            return None

    # 3.2 获取股票最新舆情新闻
    def get_stock_news(stock_name, stock_code):
        """用Tavily获取最新新闻，过滤过时信息"""
        try:
            search_query = f"{stock_name} {stock_code} 最新消息 业绩公告 行业新闻 2025-2026"
            response = tavily_client.search(
                query=search_query,
                max_results=5,
                days=news_max_days,
                include_raw_content=False
            )
            news_list = [f"【{res['title']}】{res['content'][:200]}..." for res in response["results"]]
            return "\n".join(news_list) if news_list else "暂无最新相关新闻"
        except Exception as e:
            print(f"⚠️  获取{stock_name}新闻失败：{e}")
            return "新闻获取失败"

    # 3.3 AI生成决策仪表盘（开源系统核心亮点）
    def generate_ai_report(stock_data, news_content):
        """生成包含核心结论、买卖点位、纪律检查、打分的完整报告"""
        prompt = f"""
        你是专业的股票分析师，严格按照以下格式生成【股票决策仪表盘】，语言简洁专业，数据精准。
        股票基础信息：
        名称：{stock_data['name']}
        代码：{stock_data['code']}
        当前价格：{stock_data['current_price']}元
        涨跌幅：{stock_data['change']}%
        技术面数据：
        MA5：{stock_data['ma5']}元，MA10：{stock_data['ma10']}元，MA20：{stock_data['ma20']}元
        20日乖离率：{stock_data['bias']}%，多头排列：{"是" if stock_data['trend_up'] else "否"}
        支撑位：{stock_data['support']}元，压力位：{stock_data['pressure']}元
        最新舆情新闻：
        {news_content}
        交易纪律规则：
        1. 乖离率超过{bias_threshold}%，提示严禁追高风险
        2. 多头排列为趋势向好信号
        3. 必须给出精确的买入价、止损价、目标价
        4. 每项检查项以「满足/注意/不满足」标记

        严格按照以下固定格式输出，不要添加额外内容：
        🎯 {stock_data['name']}({stock_data['code']}) 决策仪表盘
        📊 综合评分：0-100分 | 操作建议：买入/观望/卖出 | 多空观点：看多/看空/震荡
        💡 一句话核心结论：（不超过50字，直接给核心判断）

        📈 精确买卖点位
        - 建议买入价：xxx元
        - 止损价：xxx元
        - 第一目标价：xxx元
        - 第二目标价：xxx元

        ✅ 交易纪律检查清单
        - 多头趋势排列：满足/注意/不满足
        - 乖离率追高风险：满足/注意/不满足
        - 基本面舆情支撑：满足/注意/不满足
        - 盈亏比合理性：满足/注意/不满足

        📰 舆情与基本面速览
        利好催化：
        1. xxx
        2. xxx
        风险警报：
        1. xxx
        2. xxx
        """
        try:
            response = client.chat.completions.create(
                model=ai_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=2000
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"❌ AI生成报告失败：{e}")
            return f"❌ {stock_data['name']}分析失败，AI调用异常"

    # 3.4 钉钉推送函数
    def send_dingtalk(content):
        """推送分析报告到钉钉"""
        if not dingtalk_enabled:
            return
        for webhook in dingtalk_webhooks:
            if not webhook.strip():
                continue
            try:
                data = {
                    "msgtype": "markdown",
                    "markdown": {
                        "title": "📈 股票智能分析报告",
                        "text": content
                    }
                }
                requests.post(webhook.strip(), json=data, timeout=10)
                print(f"✅ 钉钉推送成功")
            except Exception as e:
                print(f"❌ 钉钉推送失败：{e}")

    # ===================== 4. 主执行流程 =====================
    print(f"\n🚀 开始执行股票分析，共{len(stock_list)}只股票")
    full_report = f"# 🎯 股票智能分析系统报告\n📅 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    success_count = 0

    for stock in stock_list:
        print(f"\n===================== 正在分析：{stock} =====================")
        # 1. 获取行情数据
        stock_data = get_stock_data(stock)
        if not stock_data:
            full_report += f"## ❌ {stock} 分析失败，数据获取异常\n\n"
            continue
        # 2. 获取舆情新闻
        news_content = get_stock_news(stock_data["name"], stock)
        # 3. 生成AI分析报告
        ai_report = generate_ai_report(stock_data, news_content)
        print(ai_report)
        # 4. 汇总报告
        full_report += f"{ai_report}\n\n---\n\n"
        success_count += 1

    # 最终汇总
    summary = f"## 📊 分析结果汇总\n共分析{len(stock_list)}只股票，成功{success_count}只，失败{len(stock_list)-success_count}只\n\n📌 股市有风险，投资需谨慎。"
    full_report += summary
    print(f"\n🎉 分析完成，{summary}")

    # 推送钉钉
    send_dingtalk(full_report)
    print("✅ 全部流程执行完成")
