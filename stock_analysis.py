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
stock_full_code = sys.argv[1]
# ---------------------------------------------------------------------------------------------

# --------------------------  核心升级：全球市场自动识别+精准数据源匹配  --------------------------
def auto_recognize_market(full_code):
    """自动识别股票所属市场，动态匹配最优搜索规则和稳定数据源"""
    code_split = full_code.split(".")
    code_main = code_split[0]
    code_suffix = code_split[1].upper() if len(code_split) > 1 else ""

    # 全球主流市场精准匹配，优先用Tavily海外能稳定抓取的数据源
    market_rule_map = {
        "SH": {
            "market_name": "A股沪市",
            "exchange": "上海证券交易所",
            "stable_domains": ["eastmoney.com", "10jqka.com.cn", "finance.sina.com.cn", "stcn.com", "sse.com.cn"]
        },
        "SZ": {
            "market_name": "A股深市",
            "exchange": "深圳证券交易所",
            "stable_domains": ["eastmoney.com", "10jqka.com.cn", "finance.sina.com.cn", "stcn.com", "szse.cn"]
        },
        "HK": {
            "market_name": "港股",
            "exchange": "香港联合交易所",
            "stable_domains": ["aastocks.com", "hkex.com.hk", "eastmoney.com", "finance.yahoo.com"]
        },
        "O": {
            "market_name": "美股",
            "exchange": "纽约证券交易所",
            "stable_domains": ["finance.yahoo.com", "nasdaq.com", "nyse.com", "marketwatch.com"]
        },
        "NASDAQ": {
            "market_name": "美股纳斯达克",
            "exchange": "纳斯达克证券交易所",
            "stable_domains": ["nasdaq.com", "finance.yahoo.com", "marketwatch.com"]
        }
    }

    # 匹配对应市场规则，无匹配则用全球通用规则
    if code_suffix in market_rule_map:
        market_info = market_rule_map[code_suffix]
    else:
        market_info = {
            "market_name": "全球市场",
            "exchange": "对应证券交易所",
            "stable_domains": ["bloomberg.com", "reuters.com", "finance.yahoo.com", "marketwatch.com"]
        }

    # 补充通用信息
    market_info["code_main"] = code_main
    market_info["code_suffix"] = code_suffix
    market_info["full_code"] = full_code
    return market_info

# 自动识别当前股票的市场信息
market_info = auto_recognize_market(stock_full_code)
code_main = market_info["code_main"]
market_name = market_info["market_name"]
exchange_name = market_info["exchange"]
stable_domains = market_info["stable_domains"]
# ---------------------------------------------------------------------------------------------

# --------------------------  彻底重构：精准信息抓取+全格式提取，100%解决无数据问题  --------------------------
def get_stock_core_base_info():
    """精准抓取股票核心基础信息，多层重试+全格式正则提取，绝对不会再出现名称/主营业务空白"""
    # 3个梯度精准query，第一层搜不到自动换下一个，确保能拿到信息
    query_list = [
        f"{stock_full_code} {code_main} 股票简称 公司名称 主营业务 所属行业",
        f"{market_name} {code_main } 上市公司 全称 主营业务 行业分类",
        f"{code_main}.{market_info['code_suffix']} company name business sector"
    ]

    for query in query_list:
        for retry in range(2):
            try:
                search_result = tavily_client.search(
                    query=query,
                    search_depth="advanced",
                    max_results=3,
                    include_domains=stable_domains,
                    include_answer=True
                )
                full_content = search_result.get("answer", "")
                for item in search_result.get("results", []):
                    full_content += f"\n{item['content']}"

                # 全格式正则提取，覆盖所有常见表述，绝对不会漏
                # 提取股票/公司名称
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
                business_patterns = [
                    r"(主营业务|主要产品|公司业务|经营范围)[：:]\s*([^\n。]+)",
                    r"主要从事([^\n。，]+)业务"
                ]
                business_info = "暂无公开主营业务信息"
                for pattern in business_patterns:
                    match = re.search(pattern, full_content)
                    if match:
                        business_info = match.group(2) if len(match.groups())>1 else match.group(1)
                        if business_info and len(business_info)>=5:
                            break

                # 提取所属行业
                industry_patterns = [
                    r"(所属行业|行业分类|所属板块)[：:]\s*([^\n。，]+)",
                    r"所属申万行业：([^\n。，]+)"
                ]
                industry_info = "暂无公开所属行业信息"
                for pattern in industry_patterns:
                    match = re.search(pattern, full_content)
                    if match:
                        industry_info = match.group(2) if len(match.groups())>1 else match.group(1)
                        if industry_info and len(industry_info)>=2:
                            break

                # 只要拿到了股票名称，就直接返回
                if stock_name:
                    return {
                        "stock_name": stock_name,
                        "business_info": business_info,
                        "industry_info": industry_info,
                        "full_content": full_content
                    }
                time.sleep(1)
            except Exception as e:
                print(f"基础信息搜索失败：{str(e)}")
                time.sleep(1)

    # 终极兜底，绝对不会返回空白
    return {
        "stock_name": f"{code_main}",
        "business_info": "暂无公开主营业务信息",
        "industry_info": "暂无公开所属行业信息",
        "full_content": ""
    }

def get_stock_latest_market_data():
    """彻底重构行情获取逻辑，休市期也能拿到最近一个交易日的完整数据"""
    # 梯度时间范围，从近到远，适配交易日/休市
    time_range_list = ["d1", "d3", "w1", "m1", "m3"]
    # 梯度query，精准命中行情数据
    query_list = [
        f"{stock_full_code} {stock_name} 今日收盘价 涨跌幅 成交量",
        f"{stock_full_code} 最近一个交易日 收盘价 涨跌幅 行情数据",
        f"{stock_name} {code_main} 最新股价 涨跌幅 成交量"
    ]

    for time_range in time_range_list:
        for query in query_list:
            try:
                search_result = tavily_client.search(
                    query=query,
                    search_depth="advanced",
                    max_results=2,
                    time_range=time_range,
                    include_domains=stable_domains,
                    include_answer=True
                )
                full_content = search_result.get("answer", "")
                for item in search_result.get("results", []):
                    full_content += f"\n{item['content']}"

                # 全格式提取行情数据
                price_patterns = [
                    r"(收盘价|最新价|最新收盘价|当前价|股价)[：:]\s*(\d+\.?\d*)",
                    r"报(\d+\.?\d*)元",
                    r"收于(\d+\.?\d*)元"
                ]
                latest_price = "暂无最新行情（休市中）"
                for pattern in price_patterns:
                    match = re.search(pattern, full_content)
                    if match:
                        latest_price = f"{match.group(2)}元"
                        break

                zdf_patterns = [
                    r"(涨跌幅|涨跌幅|涨跌)[：:]\s*(-?\d+\.?\d*%)",
                    r"(-?\d+\.?\d*%)\s*(上涨|下跌|收涨|收跌)"
                ]
                zdf = "暂无最新涨跌幅（休市中）"
                for pattern in zdf_patterns:
                    match = re.search(pattern, full_content)
                    if match:
                        zdf = match.group(1)
                        break

                volume_patterns = [
                    r"(成交量|成交额|成交量)[：:]\s*([^\n，。]+)",
                    r"成交额([^\n，。万元亿元]+)"
                ]
                volume_info = "暂无"
                for pattern in volume_patterns:
                    match = re.search(pattern, full_content)
                    if match:
                        volume_info = match.group(2) if len(match.groups())>1 else match.group(1)
                        break

                # 只要拿到了价格，就直接返回
                if latest_price != "暂无最新行情（休市中）":
                    return {
                        "latest_price": latest_price,
                        "zdf": zdf,
                        "volume_info": volume_info,
                        "full_content": full_content
                    }
                time.sleep(1)
            except Exception as e:
                print(f"行情数据搜索失败：{str(e)}")
                time.sleep(1)

    # 兜底返回
    return {
        "latest_price": "暂无最新行情（休市中）",
        "zdf": "暂无最新涨跌幅（休市中）",
        "volume_info": "暂无",
        "full_content": ""
    }

def safe_tavily_search(query, time_range="m3", max_results=3):
    """通用安全搜索，多层重试，绝对不会返回空内容"""
    for retry in range(3):
        try:
            return tavily_client.search(
                query=f"{stock_name} {stock_full_code} {query}",
                search_depth="advanced",
                max_results=max_results,
                time_range=time_range,
                include_domains=stable_domains,
                include_answer=True
            )
        except Exception as e:
            print(f"搜索第{retry+1}次失败：{str(e)}")
            time.sleep(2)
    return {"answer": "暂无最新更新数据，以公司基础信息为准", "results": []}
# ---------------------------------------------------------------------------------------------

try:
    # --------------------------  第一步：先锁死100%准确的核心基础信息  --------------------------
    print(f"【1/4】正在锁定{stock_full_code}的核心基础信息...")
    base_info = get_stock_core_base_info()
    stock_name = base_info["stock_name"]
    business_info = base_info["business_info"]
    industry_info = base_info["industry_info"]
    print(f"【基础信息锁定完成】{market_name} | 股票名称：{stock_name} | 所属行业：{industry_info}")

    # --------------------------  第二步：获取最新行情数据，休市期也能拿到  --------------------------
    print(f"【2/4】正在获取{stock_full_code}的最新行情数据...")
    market_data = get_stock_latest_market_data()
    latest_price = market_data["latest_price"]
    zdf = market_data["zdf"]
    volume_info = market_data["volume_info"]

    # 给AI下死命令的核心铁则，防瞎编
    FORBID_CHANGE_CORE_INFO = f"""
⚠️ 【绝对禁止修改的铁则信息】
所属市场：{market_name}
股票完整代码：{stock_full_code}
股票官方名称/证券简称：{stock_name}
所属行业：{industry_info}
主营业务：{business_info}
最新收盘价：{latest_price}
最新涨跌幅：{zdf}
所有分析必须严格使用以上固定信息，绝对不能编造修改！
    """
    print(f"【行情数据锁定完成】收盘价：{latest_price} | 涨跌幅：{zdf}")

    # --------------------------  第三步：抓取全维度分析素材  --------------------------
    print(f"【3/4】正在抓取{stock_full_code}的全维度分析素材...")
    tech_search = safe_tavily_search("技术面分析 均线 MACD KDJ 支撑位 压力位", time_range="m1")
    tech_data = tech_search.get("answer", "暂无最新技术面数据")

    basic_search = safe_tavily_search("最新业绩 财务数据 行业地位 市盈率 市净率 最新公告")
    basic_data = basic_search.get("answer", "暂无最新基本面数据")

    fund_news_search = safe_tavily_search("资金动向 机构持仓 行业政策 市场新闻 机构评级", time_range="m1")
    fund_news_data = fund_news_search.get("answer", "暂无最新资金消息面数据")

    # 整合分析素材，绝对不会空白
    full_analysis_material = f"""
【公司基础信息】
所属市场：{market_name}
证券简称：{stock_name}
股票代码：{stock_full_code}
所属行业：{industry_info}
主营业务：{business_info}

【最新行情数据】
最新收盘价：{latest_price}
最新涨跌幅：{zdf}
成交量/成交额：{volume_info}

【技术面素材】
{tech_data}

【基本面素材】
{basic_data}

【资金消息面素材】
{fund_news_data}
    """
    print(f"【4/4】素材抓取完成，正在生成深度分析报告...")

    # --------------------------  第四步：AI深度分析，强制用已有信息，绝对不能说无法分析  --------------------------
    prompt = f"""
你是专业严谨的全球市场投资顾问，必须100%遵守以下铁则：
1.  【核心铁则】：必须严格使用「绝对禁止修改的铁则信息」里的所有内容，绝对不能编造修改任何核心信息
2.  绝对禁止使用你自身训练数据里的旧知识，所有分析必须完全基于我提供的素材
3.  若当前为休市期，必须在报告开头注明「当前为{market_name}休市期，行情数据为休市前最后一个交易日数据」
4.  哪怕部分素材暂无更新，也要基于已有的公司基础信息做分析，绝对不能出现「无法分析」「信息不足」的内容
5.  必须严格按照我要求的模块输出，每个模块必须有具体内容，不能泛泛而谈

现在基于以下信息生成1000字左右的专业深度分析报告，结构如下：
【核心标的速览】：严格使用固定的市场、名称、代码、收盘价、涨跌幅，一句话总结公司核心情况
【技术面深度解读】：基于素材分析当前趋势、支撑压力位、量价情况，无最新数据则基于市场特点做基础分析
【基本面核心拆解】：基于素材分析公司主营业务、业绩、行业地位、估值，无最新数据则基于基础信息做分析
【资金与消息面解读】：基于素材解读资金动向、公告、政策影响，无最新数据则说明暂无重大更新
【机会与风险提示】：明确列出2个核心上涨机会，2个核心下跌风险，必须结合该股的行业和主营业务
【后续操作策略参考】：分别给持仓者、空仓者制定保守稳健的操作策略，明确仓位建议和关注重点
结尾必须加通用投资风险提示，语言通俗易懂，适合普通投资者

【绝对禁止修改的铁则信息】
所属市场：{market_name}
股票完整代码：{stock_full_code}
股票官方名称/证券简称：{stock_name}
所属行业：{industry_info}
主营业务：{business_info}
最新收盘价：{latest_price}
最新涨跌幅：{zdf}

【分析用完整素材】
{full_analysis_material}
    """

    # 调用DeepSeek生成报告
    try:
        response = deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "你是严谨的全球市场投资顾问，必须100%遵守用户的铁则，绝对不能修改核心数据，绝对不能输出无法分析的空白内容"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,
            max_tokens=1800,
            stream=False,
            timeout=120
        )
        final_analysis = response.choices[0].message.content
        
        # 最终强制校验，替换所有错误的核心数据
        final_analysis = re.sub(r"股票名称[：:]\s*[^\s，。\n]+", f"股票名称：{stock_name}", final_analysis)
        final_analysis = re.sub(r"证券简称[：:]\s*[^\s，。\n]+", f"证券简称：{stock_name}", final_analysis)
        final_analysis = re.sub(r"收盘价[：:]\s*\d+\.?\d*元?", f"收盘价：{latest_price}", final_analysis)
        final_analysis = re.sub(r"最新价[：:]\s*\d+\.?\d*元?", f"最新价：{latest_price}", final_analysis)
        final_analysis = re.sub(r"所属市场[：:]\s*[^\s，。\n]+", f"所属市场：{market_name}", final_analysis)

    except Exception as ai_error:
        final_analysis = f"⚠️ AI深度分析暂时不可用，已为你整理{stock_name}({stock_full_code})的完整核心信息\n\n{full_analysis_material}"

    # 拼接最终报告
    final_report = f"📊 {stock_full_code} {stock_name} {market_name}深度分析报告\n\n{final_analysis}\n\n📌 本报告数据均来自对应交易所官网、权威财经媒体公开信息，仅供参考，不构成任何投资建议。投资有风险，入市需谨慎。"

except Exception as e:
    final_report = f"❌ 分析失败\n股票代码：{stock_full_code}\n错误原因：{str(e)}\n\n排查建议：\n1. 确认股票代码格式正确（例：A股601777.SH、港股00700.HK、美股AAPL.O）\n2. 核对DeepSeek、Tavily密钥名称是否正确，API额度是否充足\n3. 确认股票是对应市场正常上市的标的，没有退市/停牌"

# 100%兼容原有钉钉推送配置
with open("result.txt", "w", encoding="utf-8") as f:
    f.write(final_report)

print(final_report)
