import akshare as ak
import google.generativeai as genai
from tavily import TavilyClient
import sys
import os
import time

# --------------------------  复用你已有的配置，完全不用改  --------------------------
# 核心修复：换成带latest后缀的兼容模型名，适配v1beta版本API
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
ai_model = genai.GenerativeModel("gemini-1.5-flash-latest")
tavily = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))
stock_code = sys.argv[1]
code = stock_code.split(".")[0]
# -------------------------------------------------------------------------------------

# --------------------------  带重试的行情数据获取，彻底解决网络超时  --------------------------
def get_stock_data(code, max_retry=3):
    for retry in range(max_retry):
        try:
            print(f"第{retry+1}次尝试获取行情数据...")
            spot_data = ak.stock_zh_a_spot_em(timeout=60)
            match_result = spot_data[spot_data["代码"] == code]
            if not match_result.empty:
                return match_result.iloc[0], "东方财富接口"
        except Exception as e:
            print(f"第{retry+1}次失败：{str(e)}")
            time.sleep(2)
    
    # 自动切换备用新浪接口
    print("东方财富接口超时，切换新浪备用接口...")
    for retry in range(max_retry):
        try:
            spot_data = ak.stock_zh_a_spot_sina(timeout=60)
            match_result = spot_data[spot_data["代码"] == code]
            if not match_result.empty:
                return match_result.iloc[0], "新浪接口"
        except Exception as e:
            print(f"新浪接口第{retry+1}次失败：{str(e)}")
            time.sleep(2)
    
    raise Exception("所有行情接口均连接超时，请稍后重试")
# -------------------------------------------------------------------------------------

try:
    # --------------------------  第一步：稳定获取股票行情数据  --------------------------
    stock_info, data_source = get_stock_data(code)
    stock_name = stock_info.get("名称", "未知股票")
    print(f"成功从{data_source}获取{stock_name}行情数据")

    # 容错提取所有核心数据，找不到的字段用「-」代替
    def safe_get(key, default="-"):
        return stock_info.get(key, default)

    # 整理核心行情数据（兼容两个数据源）
    core_data = f"""
【{stock_name}（{stock_code}）今日核心行情】
数据来源：{data_source}
最新价格：{safe_get('最新价')} 元
今日涨跌幅：{safe_get('涨跌幅')} %
开盘价：{safe_get('今开', safe_get('开盘'))} 元
最高价：{safe_get('最高')} 元
最低价：{safe_get('最低')} 元
成交量：{round(float(safe_get('成交量', 0))/10000, 2) if safe_get('成交量', 0) != '-' else '-'} 万手
成交额：{round(float(safe_get('成交额', 0))/100000000, 2) if safe_get('成交额', 0) != '-' else '-'} 亿元
换手率：{safe_get('换手率')} %
动态市盈率：{safe_get('市盈率-动态', safe_get('动态市盈率'))}
市净率：{safe_get('市净率')}
"""
    # ---------------------------------------------------------------------------------------------

    # --------------------------  第二步：Tavily实时新闻搜索  --------------------------
    print(f"正在搜索{stock_name}最新相关信息...")
    try:
        search_result = tavily.search(
            query=f"A股{stock_name} {code} 最新公告 新闻 行业政策 市场消息",
            search_depth="basic",
            max_results=3,
            include_answer=True
        )
        news_content = "【最新相关动态】\n"
        if search_result.get("results"):
            for idx, item in enumerate(search_result["results"][:3]):
                news_content += f"{idx+1}. {item['title']}\n摘要：{item['content'][:100]}...\n"
        else:
            news_content += "暂无最新重大公告或新闻\n"
    except Exception as news_error:
        news_content = f"【最新相关动态】新闻获取失败：{str(news_error)}\n"
    # ---------------------------------------------------------------------------------------------

    # --------------------------  第三步：Gemini深度分析（加容错）  --------------------------
    print("正在生成深度分析报告...")
    prompt = f"""
你是一名拥有10年经验的A股专业投资顾问，基于下面的股票行情数据和最新动态信息，生成一份400字以内的专业深度分析报告，严格遵守以下要求：
1.  开头先给一个明确的今日表现总结，直接说涨跌核心原因
2.  分3个模块：盘面解读、消息面影响、操作建议，每个模块用小标题区分
3.  盘面解读结合行情数据，消息面结合搜索到的最新动态，不要泛泛而谈
4.  操作建议必须保守稳健，分持仓和空仓两种情况给出，不要给激进的买卖建议
5.  结尾必须加通用的股市风险提示
6.  语言口语化，通俗易懂，适合普通散户投资者

【基础行情数据】
{core_data}

【最新相关动态】
{news_content}
    """
    # 加模型调用容错，避免API报错
    try:
        ai_response = ai_model.generate_content(prompt)
        final_report = f"📈 {stock_name} 深度分析报告\n\n{ai_response.text}\n\n{core_data}\n{news_content}"
    except Exception as ai_error:
        # AI调用失败，直接输出行情+新闻，不会全流程报错
        final_report = f"📈 {stock_name} 行情报告\n\n⚠️ AI分析暂时不可用，已为你获取最新行情数据\n\n{core_data}\n{news_content}\n\n错误原因：{str(ai_error)}"

except Exception as e:
    final_report = f"❌ 深度分析失败\n股票代码：{stock_code}\n错误原因：{str(e)}\n\n排查建议：\n1. 确认股票代码格式正确（沪市加.SH 深市加.SZ）\n2. 确认股票是正常交易的A股，没有停牌/退市\n3. 核对Gemini、Tavily密钥是否配置正确"

# 保存报告供钉钉推送
with open("result.txt", "w", encoding="utf-8") as f:
    f.write(final_report)

print(final_report)
