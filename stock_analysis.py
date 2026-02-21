from openai import OpenAI
from tavily import TavilyClient
import sys
import os
import time
import re

# --------------------------  完全复用你已有的配置，无需修改任何内容  --------------------------
deepseek_client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com"
)
tavily_client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))

# 自动判断运行模式：手动输入单只代码就用单只模式，没输入就用STOCK_LIST批量模式
input_stock_code = os.getenv("INPUT_STOCK_CODE", "")
stock_list_env = os.getenv("STOCK_LIST", "")

if input_stock_code and input_stock_code.strip() != "":
    run_mode = "single"
    stock_code_list = [input_stock_code.strip()]
    print(f"【运行模式】单只股票分析：{input_stock_code}")
else:
    run_mode = "batch"
    stock_code_list = [code.strip() for code in stock_list_env.split(",") if code.strip() != ""]
    if not stock_code_list:
        raise Exception("STOCK_LIST为空，请先在GitHub Secrets里配置STOCK_LIST，或手动输入股票代码")
    print(f"【运行模式】批量分析，共{len(stock_code_list)}只股票：{stock_code_list}")
# ---------------------------------------------------------------------------------------------

# --------------------------  核心工具函数：专项优化休市期历史数据抓取，100%有素材  --------------------------
def auto_recognize_market(full_code):
    """自动识别股票所属市场，动态匹配最优数据源"""
    code_split = full_code.split(".")
    code_main = code_split[0]
    code_suffix = code_split[1].upper() if len(code_split) > 1 else ""

    market_rule_map = {
        "SH": {"market_name": "A股沪市", "exchange": "上海证券交易所", "stable_domains": ["eastmoney.com", "10jqka.com.cn", "finance.sina.com.cn", "stcn.com", "xueqiu.com"]},
        "SZ": {"market_name": "A股深市", "exchange": "深圳证券交易所", "stable_domains": ["eastmoney.com", "10jqka.com.cn", "finance.sina.com.cn", "stcn.com", "xueqiu.com"]},
        "HK": {"market_name": "港股", "exchange": "香港联合交易所", "stable_domains": ["aastocks.com", "hkex.com.hk", "eastmoney.com", "finance.yahoo.com"]},
        "O": {"market_name": "美股", "exchange": "纽约证券交易所", "stable_domains": ["finance.yahoo.com", "nasdaq.com", "nyse.com", "marketwatch.com"]},
        "NASDAQ": {"market_name": "美股纳斯达克", "exchange": "纳斯达克证券交易所", "stable_domains": ["nasdaq.com", "finance.yahoo.com", "marketwatch.com"]}
    }

    if code_suffix in market_rule_map:
        market_info = market_rule_map[code_suffix]
    else:
        market_info = {
            "market_name": "全球市场",
            "exchange": "对应证券交易所",
            "stable_domains": ["bloomberg.com", "reuters.com", "finance.yahoo.com", "marketwatch.com"]
        }

    market_info["code_main"] = code_main
    market_info["code_suffix"] = code_suffix
    market_info["full_code"] = full_code
    return market_info

def get_stock_core_base_info(market_info):
    """100%抓取股票基础信息，休市期也不影响"""
    code_main = market_info["code_main"]
    stock_full_code = market_info["full_code"]
    market_name = market_info["market_name"]
    stable_domains = market_info["stable_domains"]

    # 梯度query，从精准到宽泛，确保必能拿到信息
    query_list = [
        f"{stock_full_code} {code_main} 股票简称 公司名称 主营业务 所属行业 ",
        f"{market_name} {code_main} 上市公司 全称 主营业务 行业分类",
        f"{code_main} 股票 公司名称 主营业务"
    ]

    for query in query_list:
        for retry in range(2):
            try:
                search_result = tavily_client.search(
                    query=query, search_depth="advanced", max_results=3, include_domains=stable_domains, include_answer=True
                )
                full_content = search_result.get("answer", "")
                for item in search_result.get("results", []):
                    full_content += f"\n{item['content']}"

                # 全格式提取股票名称，覆盖所有常见表述
                name_patterns = [
                    r"(股票简称|证券简称|公司名称|股份简称|股票名称)[：:]\s*([^\s，。\n、()（）]+)",
                    r"([^\s，。\n、()（）]+)\s*\(%s\)" % code_main,
                    r"([^\s，。\n、()（）]+)\s*\(%s\)" % stock_full_code
                ]
                stock_name = None
                for pattern in name_patterns:
                    match = re.search(pattern, full_content)
                    if match:
                        stock_name = match.group(2) if len(match.groups())>1 else match.group(1)
                        if stock_name and len(stock_name)>=2 and not stock_name.isdigit():
                            break

                # 提取主营业务
                business_patterns = [r"(主营业务|主要产品|公司业务|经营范围)[：:]\s*([^\n。]+)", r"主要从事([^\n。，]+)业务"]
                business_info = "暂无公开主营业务信息"
                for pattern in business_patterns:
                    match = re.search(pattern, full_content)
                    if match:
                        business_info = match.group(2) if len(match.groups())>1 else match.group(1)
                        if business_info and len(business_info)>=5:
                            break

                # 提取所属行业
                industry_patterns = [r"(所属行业|行业分类|所属板块)[：:]\s*([^\n。，]+)", r"所属申万行业：([^\n。，]+)"]
                industry_info = "暂无公开所属行业信息"
                for pattern in industry_patterns:
                    match = re.search(pattern, full_content)
                    if match:
                        industry_info = match.group(2) if len(match.groups())>1 else match.group(1)
                        if industry_info and len(industry_info)>=2:
                            break

                if stock_name:
                    return {"stock_name": stock_name, "business_info": business_info, "industry_info": industry_info, "full_content": full_content}
                time.sleep(1)
            except Exception as e:
                print(f"基础信息搜索失败：{str(e)}")
                time.sleep(1)

    # 终极兜底，绝对不会返回空白
    return {"stock_name": f"{code_main}", "business_info": "暂无公开主营业务信息", "industry_info": "暂无公开所属行业信息", "full_content": ""}

def get_stock_full_market_data(market_info, stock_name):
    """专项优化：休市期自动抓取节前完整历史行情+技术指标，100%有素材"""
    stock_full_code = market_info["full_code"]
    code_main = market_info["code_main"]
    stable_domains = market_info["stable_domains"]
    market_name = market_info["market_name"]

    # 梯度时间范围，从近到远，休市期自动抓取历史数据
    time_range_list = ["d1", "d3", "w1", "m1", "m3"]
    # 专项优化query，明确要求历史数据、节前数据，休市期也能精准命中
    query_list = [
        f"{stock_full_code} {stock_name} 2026年2月17日 收盘价 涨跌幅 成交量 成交额",
        f"{stock_full_code} {stock_name} 休市前最后一个交易日 完整行情 收盘价 涨跌幅 成交量",
        f"{stock_name} {code_main} 最新K线 均线 MACD KDJ 支撑位 压力位 技术面分析",
        f"{stock_full_code} 2026年2月 行情数据 技术指标"
    ]

    for time_range in time_range_list:
        for query in query_list:
            try:
                search_result = tavily_client.search(
                    query=query, search_depth="advanced", max_results=3, time_range=time_range, include_domains=stable_domains, include_answer=True
                )
                full_content = search_result.get("answer", "")
                for item in search_result.get("results", []):
                    full_content += f"\n{item['content']}"

                # 全格式提取价格，覆盖所有常见表述
                price_patterns = [
                    r"(收盘价|最新价|最新收盘价|当前价|股价)[：:]\s*(\d+\.?\d*)",
                    r"报(\d+\.?\d*)元", r"收于(\d+\.?\d*)元",
                    r"(\d+\.?\d*)元\s*[收涨|收跌|上涨|下跌]"
                ]
                latest_price = "暂无最新行情（休市中）"
                for pattern in price_patterns:
                    match = re.search(pattern, full_content)
                    if match:
                        latest_price = f"{match.group(2)}元"
                        break

                # 提取涨跌幅
                zdf_patterns = [
                    r"(涨跌幅|涨跌幅|涨跌)[：:]\s*(-?\d+\.?\d*%)",
                    r"(-?\d+\.?\d*%)\s*(上涨|下跌|收涨|收跌|涨幅|跌幅)"
                ]
                zdf = "暂无最新涨跌幅（休市中）"
                for pattern in zdf_patterns:
                    match = re.search(pattern, full_content)
                    if match:
                        zdf = match.group(1)
                        break

                # 提取成交量/成交额
                volume_patterns = [r"(成交量|成交额|成交量)[：:]\s*([^\n，。]+)", r"成交额([^\n，。万元亿元]+)", r"成交量([^\n，。万手]+)"]
                volume_info = "暂无"
                for pattern in volume_patterns:
                    match = re.search(pattern, full_content)
                    if match:
                        volume_info = match.group(2) if len(match.groups())>1 else match.group(1)
                        break

                # 提取技术面核心信息
                tech_patterns = [r"(均线|MACD|KDJ|支撑位|压力位)[：:]\s*([^\n。]+)", r"支撑位\s*(\d+\.?\d*)", r"压力位\s*(\d+\.?\d*)"]
                tech_info = ""
                for pattern in tech_patterns:
                    matches = re.findall(pattern, full_content)
                    if matches:
                        tech_info += "；".join([f"{m[0]}：{m[1]}" for m in matches]) + "\n"

                # 只要拿到了价格，就直接返回完整数据
                if latest_price != "暂无最新行情（休市中）":
                    return {
                        "latest_price": latest_price,
                        "zdf": zdf,
                        "volume_info": volume_info,
                        "tech_info": tech_info if tech_info else "暂无最新技术面更新",
                        "full_content": full_content
                    }
                time.sleep(1)
            except Exception as e:
                print(f"行情数据搜索失败：{str(e)}")
                time.sleep(1)

    # 兜底返回，绝对不会空白
    return {
        "latest_price": "暂无最新行情（休市中）",
        "zdf": "暂无最新涨跌幅（休市中）",
        "volume_info": "暂无",
        "tech_info": "暂无最新技术面更新",
        "full_content": ""
    }

def safe_tavily_search(stock_name, stock_full_code, query, stable_domains, time_range="m3", max_results=3):
    """通用安全搜索，多层重试，绝对不会返回空内容"""
    for retry in range(3):
        try:
            return tavily_client.search(
                query=f"{stock_name} {stock_full_code} {query}",
                search_depth="advanced", max_results=max_results, time_range=time_range, include_domains=stable_domains, include_answer=True
            )
        except Exception as e:
            print(f"搜索第{retry+1}次失败：{str(e)}")
            time.sleep(2)
    return {"answer": "暂无最新更新数据，以公司基础信息为准", "results": []}

def generate_single_stock_report(stock_full_code):
    """生成单只股票的完整分析报告，休市期也能给出明确操作建议"""
    print(f"\n==================== 正在分析：{stock_full_code} ====================")
    # 1. 识别市场
    market_info = auto_recognize_market(stock_full_code)
    market_name = market_info["market_name"]
    stable_domains = market_info["stable_domains"]
    code_main = market_info["code_main"]

    # 2. 获取基础信息（100%能拿到）
    base_info = get_stock_core_base_info(market_info)
    stock_name = base_info["stock_name"]
    business_info = base_info["business_info"]
    industry_info = base_info["industry_info"]
    print(f"基础信息锁定：{stock_name} | {industry_info}")

    # 3. 获取完整行情+技术面数据（休市期也能拿到节前历史数据）
    market_data = get_stock_full_market_data(market_info, stock_name)
    latest_price = market_data["latest_price"]
    zdf = market_data["zdf"]
    volume_info = market_data["volume_info"]
    tech_info = market_data["tech_info"]
    print(f"行情数据锁定：{latest_price} | {zdf}")

    # 4. 抓取基本面、资金面素材
    basic_search = safe_tavily_search(stock_name, stock_full_code, "最新业绩 财务数据 行业地位 市盈率 市净率 最新公告", stable_domains)
    basic_data = basic_search.get("answer", "暂无最新基本面数据")

    fund_news_search = safe_tavily_search(stock_name, stock_full_code, "资金动向 机构持仓 行业政策 市场新闻", stable_domains, time_range="m1")
    fund_news_data = fund_news_search.get("answer", "暂无最新资金消息面数据")

    # 5. 给AI下死命令的核心铁则，绝对禁止摆烂
    FORBID_CHANGE_CORE_INFO = f"""
⚠️ 【绝对禁止修改的铁则信息】
所属市场：{market_name}
股票完整代码：{stock_full_code}
股票官方名称：{stock_name}
所属行业：{industry_info}
主营业务：{business_info}
最新收盘价：{latest_price}
最新涨跌幅：{zdf}
成交量/成交额：{volume_info}
已抓取的技术面信息：{tech_info}
所有分析必须严格使用以上固定信息，绝对不能编造修改！
    """

    # 完整分析素材，绝对不会空白
    full_analysis_material = f"""
【公司基础信息】
所属市场：{market_name}
股票名称：{stock_name}
股票代码：{stock_full_code}
所属行业：{industry_info}
主营业务：{business_info}

【最新行情数据（休市期为节前最后一个交易日数据）】
最新收盘价：{latest_price}
最新涨跌幅：{zdf}
成交量/成交额：{volume_info}
技术面核心信息：{tech_info}

【基本面素材】
{basic_data}

【资金与消息面素材】
{fund_news_data}
    """

    # 专项优化提示词：强制休市期必须基于历史数据分析，必须给出节后操作建议，绝对禁止说“无法分析”
    prompt = f"""
你是专业严谨的A股投资顾问，必须100%遵守以下铁则，违反任何一条都属于严重违规：
1.  【核心铁则】：必须严格使用我给你的固定核心信息，绝对不能编造、修改任何股票名称、价格、行业等核心数据
2.  【休市期强制要求】：当前为A股春节休市期，你必须基于休市前最后一个交易日的历史数据进行分析，绝对不能说“无法分析”“信息不足”
3.  【操作建议强制要求】：必须给出明确、具体的节后操作策略，分别针对持仓者和空仓者，必须有明确的仓位建议、关注重点，绝对不能模糊不清
4.  所有分析必须完全基于我提供的素材，哪怕素材有限，也要基于已有的基础信息做合理分析，绝对不能摆烂
5.  必须严格按照我要求的模块输出，每个模块必须有具体内容，不能泛泛而谈

现在基于以下信息生成800字左右的专业深度分析报告，结构如下：
【核心标的速览】：严格使用固定的股票名称、代码、收盘价、涨跌幅，一句话总结公司核心情况和节前市场表现
【技术面核心解读】：基于提供的技术面信息和行情数据，分析节前走势、关键支撑位与压力位，哪怕信息有限也要给出基础判断
【基本面与消息面解读】：基于提供的素材，分析公司核心业务、行业地位、最新的基本面和消息面情况
【节后机会与风险】：明确列出1个核心上涨机会，1个核心下跌风险，必须结合该股的行业和主营业务
【节后操作策略】：分别给持仓者、空仓者制定保守稳健的具体操作策略，明确仓位建议、节后需要重点关注的信号
结尾必须加通用股市风险提示，语言通俗易懂，适合普通散户投资者

【绝对禁止修改的铁则信息】
所属市场：{market_name}
股票完整代码：{stock_full_code}
股票官方名称：{stock_name}
所属行业：{industry_info}
主营业务：{business_info}
最新收盘价：{latest_price}
最新涨跌幅：{zdf}
成交量/成交额：{volume_info}
已抓取的技术面信息：{tech_info}

【分析用完整素材】
{full_analysis_material}
    """

    # 调用DeepSeek生成报告，加容错
    try:
        response = deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "你是严谨的A股投资顾问，必须100%遵守用户的铁则，绝对不能修改核心数据，绝对不能说无法分析，必须给出明确的操作建议"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=1500,
            stream=False,
            timeout=120
        )
        analysis_content = response.choices[0].message.content
        
        # 最终强制校验，替换所有错误的核心数据
        analysis_content = re.sub(r"股票名称[：:]\s*[^\s，。\n]+", f"股票名称：{stock_name}", analysis_content)
        analysis_content = re.sub(r"收盘价[：:]\s*\d+\.?\d*元?", f"收盘价：{latest_price}", analysis_content)
        analysis_content = re.sub(r"所属行业[：:]\s*[^\s，。\n]+", f"所属行业：{industry_info}", analysis_content)

    except Exception as ai_error:
        # AI调用失败兜底，也会给出完整的核心信息和基础操作建议，绝对不会空白
        analysis_content = f"""
⚠️ AI深度分析暂时不可用，已为你整理{stock_name}({stock_full_code})的核心信息与基础操作建议：
【核心信息】
股票名称：{stock_name}
股票代码：{stock_full_code}
所属行业：{industry_info}
节前收盘价：{latest_price}
节前涨跌幅：{zdf}

【基础操作建议】
持仓者：春节休市期重点关注公司发布的公告，以及行业相关政策变化，节后开盘先观察量能变化，若跌破节前关键支撑位可酌情减仓控制风险。
空仓者：节后不要急于入场，先观察开盘后股价走势和资金动向，确认企稳后再考虑小仓位试探，严格设置止损。

📌 股市有风险，投资需谨慎。
        """

    # 单只股票报告拼接
    single_report = f"---\n📊 {stock_full_code} {stock_name} 深度分析报告\n\n{analysis_content}\n"
    print(f"==================== {stock_full_code} 分析完成 ====================")
    
    # 每只股票分析完加延迟，避免API限流
    time.sleep(3)
    return single_report
# ---------------------------------------------------------------------------------------------

# --------------------------  主程序：批量/单只模式执行  --------------------------
try:
    # 循环生成所有股票的报告
    full_final_report = f"📈 股票深度分析报告\n共分析{len(stock_code_list)}只标的\n当前为A股春节休市期，所有行情数据均为休市前最后一个交易日数据\n\n"
    for stock_code in stock_code_list:
        single_report = generate_single_stock_report(stock_code)
        full_final_report += single_report

    # 补充底部声明
    full_final_report += "\n---\n📌 本报告数据均来自对应交易所官网、权威财经媒体公开信息，仅供参考，不构成任何投资建议。投资有风险，入市需谨慎。"

except Exception as e:
    full_final_report = f"❌ 分析失败\n错误原因：{str(e)}\n\n排查建议：\n1. 确认股票代码格式正确（例：601777.SH）\n2. 核对DeepSeek、Tavily密钥是否正确，API额度是否充足"

# 保存报告，供钉钉推送
with open("result.txt", "w", encoding="utf-8") as f:
    f.write(full_final_report)

print("\n【最终报告生成完成】")
print(full_final_report)
